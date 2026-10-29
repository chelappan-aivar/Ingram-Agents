"""The agentic editor: streams tool calls, text and change proposals for one instruction."""

from collections.abc import AsyncIterator
from typing import Any

from app.core.config import SowSettings
from app.modules.sow_generator.agents.factory import ROLE_EDITOR, build_agent
from app.modules.sow_generator.agents.tools import Proposal, build_editor_tools
from app.modules.sow_generator.models import EditInputs, EditorEvent
from app.modules.sow_generator.repositories.prompt_repository import load_editor_prompt

_MAX_TOKENS = 10000
_DEFAULT_TEMPERATURE = 0.2
_TOOL_RESULT_PREVIEW_CHARS = 500
_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens")


def _build_context(inputs: EditInputs) -> str:
    """The user message: the instruction plus whatever document context was supplied."""
    parts = [f"## INSTRUCTION\n{inputs.instruction}"]
    if inputs.selected_text:
        parts.append(f"## CURRENT SELECTION\n{inputs.selected_text}")
    if inputs.heading_breadcrumb:
        parts.append(f"## SELECTION LOCATION\n{inputs.heading_breadcrumb}")
    if inputs.document_outline:
        parts.append("## DOCUMENT OUTLINE\n" + "\n".join(f"- {h}" for h in inputs.document_outline))
    if inputs.customer_name:
        parts.append(f"## CLIENT\n{inputs.customer_name}")
    issue = inputs.resolving_issue
    if issue:
        issue_lines = [
            f"- Severity: {issue.get('severity', '')}",
            f"- Section: {issue.get('section', '')}",
            f"- Title: {issue.get('title', '')}",
            f"- Detail: {issue.get('detail', '')}",
            f"- Suggested fix: {issue.get('suggestion', '')}",
        ]
        parts.append("## CONSISTENCY ISSUE TO RESOLVE\n" + "\n".join(issue_lines))
    return "\n\n".join(parts)


def _replay_history(chat_history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Prior turns in the Bedrock message format, skipping empty ones."""
    return [
        {"role": msg.get("role", "user"), "content": [{"text": msg["content"]}]}
        for msg in chat_history
        if msg.get("content")
    ]


def _tool_result_text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content)


async def stream_editor(
    settings: SowSettings, inputs: EditInputs, *, model_id: str | None = None
) -> AsyncIterator[EditorEvent]:
    """Yield editor events: text, tool_call, tool_call_complete, tool_result, proposals, done."""
    proposals: list[Proposal] = []
    tools = build_editor_tools(inputs.sections_map, proposals)

    # Bedrock prompt caching: a checkpoint after the tools caches their schemas, and one after
    # the system prompt caches both. Sonnet's 1,024-token minimum is met by each checkpoint.
    system_prompt: list[dict[str, Any]] = [
        {"text": load_editor_prompt()},
        {"cachePoint": {"type": "default"}},
    ]
    agent = build_agent(
        settings,
        role=ROLE_EDITOR,
        model_id=model_id or settings.sow_generation_model,
        default_temperature=_DEFAULT_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        system_prompt=system_prompt,
        tools=tools,
        cache_tools="default",
    )
    history = _replay_history(inputs.chat_history)
    if history:
        agent.messages = history  # type: ignore[assignment]  # plain dicts match the Message shape

    seen_tool_ids: set[str] = set()
    usage = {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 0,
    }

    async for event in agent.stream_async(_build_context(inputs)):
        text = event.get("data")
        if text:
            yield {"type": "text", "chunk": text}

        current = event.get("current_tool_use")
        if current:
            tool_id = current.get("toolUseId", "")
            # Emit early with the name only, for immediate UI feedback.
            if current.get("name") and tool_id and tool_id not in seen_tool_ids:
                seen_tool_ids.add(tool_id)
                yield {
                    "type": "tool_call",
                    "tool": current["name"],
                    "args": current.get("input") or {},
                    "toolUseId": tool_id,
                }

        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            message_usage = (message.get("metadata") or {}).get("usage") or {}
            for key in usage:
                usage[key] += message_usage.get(key) or 0
            # Emit the full tool call, with populated args, from the complete message.
            for block in message.get("content", []):
                if isinstance(block, dict) and "toolUse" in block:
                    tool_use = block["toolUse"]
                    yield {
                        "type": "tool_call_complete",
                        "tool": tool_use.get("name", ""),
                        "args": tool_use.get("input", {}),
                        "toolUseId": tool_use.get("toolUseId", ""),
                    }
        elif message.get("role") == "user":
            for block in message.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result = _tool_result_text(block.get("content", ""))
                    yield {"type": "tool_result", "result": result[:_TOOL_RESULT_PREVIEW_CHARS]}

    for proposal in proposals:
        yield proposal
    yield {"type": "done", "usage": usage}
