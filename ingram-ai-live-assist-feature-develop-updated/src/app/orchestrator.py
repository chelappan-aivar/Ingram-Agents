"""One agent run: loads the session, runs the agent on new turns, applies its
tool results and saves the session. Called by the background worker.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor

from collections.abc import Callable

from app.assist_config import PRIOR_CONTEXT_OFF, PRIOR_CONTEXT_SKIP, SCOPE_CHECKLIST, AssistConfig
from app.guidance import Guidance, GuidanceSet, UnknownOpportunityTypeError, builtin_guidance
from app.memory import MemoryScope
from app.prompts import CLOSING, CORE_PROMPT, suggestion_rules
from app.session_store import (
    FIELD_SOURCE_AGENT,
    FIELD_SOURCE_GUIDANCE,
    SessionState,
    SessionStore,
)
from app.tools import FieldDef, build_tools, field_defs_from_guidance
from app.transcript import TranscriptChunk

DEFAULT_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
DEFAULT_MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID")  # Unset disables AgentCore Memory.
DEFAULT_REGION = os.environ.get("AWS_REGION")
PROMPT_LOG_DIR = os.environ.get("PROMPT_LOG_DIR", "tmp")  # Per-request prompt/message dumps.

RECENT_SUGGESTIONS_IN_PROMPT = 5
RECENT_QUESTIONS_FOR_DEDUPE = 10
RECALL_TOP_K = 5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GapItem:
    field_id: str
    label: str
    description: str
    required: bool = True
    source: str = FIELD_SOURCE_GUIDANCE


@dataclass(frozen=True)
class CapturedItem:
    field_id: str
    label: str
    value: str
    speaker: str | None


@dataclass(frozen=True)
class Suggestion:
    question: str
    target_field_id: str | None
    target_label: str | None
    rationale: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    """Session state after one request. ``captured_fields``, ``added_gaps`` and
    ``dismissed_gaps`` list only this request's changes."""

    session_id: str
    opportunity_type: str | None
    opportunity_type_just_resolved: bool
    captured_fields: list[str]
    gaps: list[GapItem]
    suggestions: list[Suggestion] = field(default_factory=list)
    problem_summary: str | None = None
    captured: list[CapturedItem] = field(default_factory=list)
    added_gaps: list[str] = field(default_factory=list)
    dismissed_gaps: list[str] = field(default_factory=list)
    config: AssistConfig = field(default_factory=AssistConfig)
    narration: list[str] = field(default_factory=list)
    run_number: int = 0
    memory_lookups: list[dict] = field(default_factory=list)

    @property
    def suggestion(self) -> Suggestion | None:
        """The top suggestion, if any."""
        return self.suggestions[0] if self.suggestions else None


@dataclass(frozen=True)
class CallMemory:
    """Long-term memory access for one call, built by the processor from the call's meta."""

    scope: MemoryScope
    load_prior: Callable[[], dict | None]  # None result: unavailable, retried next run.
    search: Callable[[MemoryScope, str, str, int], list[dict]] | None = None  # None: no memory resource.


def _format_prompt(
    state: SessionState,
    known_types: list[str],
    config: AssistConfig | None = None,
    recall_available: bool = False,
    guidance: GuidanceSet | None = None,
) -> str:
    """Build the system prompt: instructions from ``config``, then the session's state."""
    config = config or AssistConfig()
    guidance = guidance or builtin_guidance()
    parts = [CORE_PROMPT, suggestion_rules(config), CLOSING]

    if config.instructions:
        parts.append(
            "## Additional instructions from the BA\n"
            "Follow these. They can change what, when and how you suggest, but never "
            "override the rules on capturing only what was said.\n" + config.instructions
        )

    prior = _prior_context_section(state.prior_context, config.use_prior_context, recall_available)
    if prior:
        parts.append(prior)

    parts += [
        "## Current understanding of the customer's problem\n"
        + (state.problem_summary or "Not established yet."),
        _opportunity_section(state, known_types, guidance),
        *_field_sections(state, guidance.general, config.scope),
    ]

    if state.suggestions:
        parts.append(
            "## Recent suggestions (do not repeat)\n"
            + "\n".join(
                f"- {max(state.chunk_count - int(s['turn']), 0)} request(s) ago, topic "
                f"{s.get('target_field_id') or 'none'}: \"{s['question']}\""
                for s in state.suggestions[-RECENT_SUGGESTIONS_IN_PROMPT:]
            )
        )

    captured = state.captured_fields()
    if captured:
        parts.append(
            "## Captured so far\n" + "\n".join(f"- {fid}: {fs.value}" for fid, fs in captured)
        )

    dismissed = state.dismissed_fields()
    if dismissed:
        parts.append(
            "## Dismissed\n" + "\n".join(f"- {fid}: {fs.note}" for fid, fs in dismissed)
        )

    return "\n\n".join(parts)


def _prior_context_section(prior: dict | None, mode: str, recall_available: bool) -> str | None:
    if mode == PRIOR_CONTEXT_OFF or (not prior and not recall_available):
        return None
    prior = prior or {}
    lines = [
        "## Known from earlier calls with this customer (not said in this call)",
        "Never record a field as captured from this section; only from this call's transcript.",
        "Skip topics it already covers." if mode == PRIOR_CONTEXT_SKIP else
        "When an open topic is covered here, suggest a short question that confirms it instead "
        "of asking from scratch.",
    ]
    for key, title in (("account", "About the customer"), ("opportunity", "About this opportunity")):
        if prior.get(key):
            lines.append(f"### {title}\n" + "\n".join(f"- {t}" for t in prior[key]))
    for call in prior.get("previous_calls", []):
        lines.append(
            f"### Earlier call: {call.get('title') or 'Untitled'} ({(call.get('ended_at') or '')[:10]})\n"
            + (call.get("summary") or "")
            + "".join(f"\n- Decided: {d}" for d in call.get("decisions", []))
            + "".join(f"\n- Was still open: {q}" for q in call.get("open_questions", []))
        )
    if len(lines) == 3:
        lines.append("Nothing loaded yet.")
    if recall_available:
        lines.append("Use recall_prior_context to look up anything else from earlier calls.")
    return "\n".join(lines)


def _opportunity_section(state: SessionState, known_types: list[str], guidance: GuidanceSet) -> str:
    if state.opportunity_type:
        try:
            label = guidance.load(state.opportunity_type).label
        except UnknownOpportunityTypeError:
            label = state.opportunity_type
        return (
            f"## Opportunity type\n'{state.opportunity_type}' ({label}) is set and "
            "locked; its checklist is layered on the general one."
        )
    type_lines = []
    for t in known_types:
        g = guidance.load(t)
        type_lines.append(f"- {t}: {g.label} -- {g.description}")
    return (
        "## Opportunity type\nGeneral discovery -- no specialised type matched yet. "
        "Specialised types you may match:\n" + "\n".join(type_lines)
    )


def _field_sections(state: SessionState, general: Guidance, scope: str = SCOPE_CHECKLIST) -> list[str]:
    """Return the open-fields, topics and topic-hint sections."""
    checklist_only = scope == SCOPE_CHECKLIST
    by_id = {f.id: f for f in general.required_fields}
    categories = {f.id: f.category for f in general.required_fields}

    open_lines = []
    topics_by_category: dict[str, list[str]] = {}
    for fid, fs in state.open_fields():
        is_topic = fid in by_id or fs.source == FIELD_SOURCE_AGENT or not checklist_only
        tags = ["required" if fs.required else "optional"]
        if categories.get(fid):
            tags.append(categories[fid])
        if fs.source == FIELD_SOURCE_AGENT:
            tags.append("added by you")
        if not is_topic:
            tags.append("capture only, do not ask")
        open_lines.append(f"- {fid} [{', '.join(tags)}]: {fs.label} -- {fs.description}")
        if is_topic:
            topics_by_category.setdefault(categories.get(fid) or "Other", []).append(fid)

    sections = ["## Open fields (still needed)\n" + ("\n".join(open_lines) or "None.")]

    heading = "Topics you may ask about" if checklist_only else "Checklist topics still open"
    if not topics_by_category:
        sections.append(
            f"## {heading}\nNone are open."
            + (" Do not suggest a question." if checklist_only else "")
        )
        return sections

    # Checklist category order first, then anything else (e.g. "Other").
    order = [
        c for c in dict.fromkeys(f.category for f in general.required_fields)
        if c in topics_by_category
    ]
    order += [c for c in topics_by_category if c not in order]
    sections.append(
        f"## {heading} (in the order to prefer)\n"
        + "\n".join(f"- {c}: {', '.join(topics_by_category[c])}" for c in order)
    )

    hints = []
    for c in order:
        for fid in topics_by_category[c]:
            f = by_id.get(fid)
            if f is None or not (f.ask_when or f.examples):
                continue
            line = f"- {fid}:"
            if f.ask_when:
                line += f" ask {f.ask_when}"
            if f.examples:
                line += " Examples: " + ", ".join(f'"{e}"' for e in f.examples)
            hints.append(line)
    if hints:
        sections.append("## Topic hints\n" + "\n".join(hints))
    return sections


def _seed_guidance_fields(state: SessionState, guidance: GuidanceSet | None = None) -> None:
    """Track the general checklist and the session's specialised one, if set.

    Idempotent, so sessions created before a checklist change pick up new fields.
    """
    guidance = guidance or builtin_guidance()
    for fd in field_defs_from_guidance(guidance.general):
        state.add_field(fd.id, label=fd.label, description=fd.description, required=fd.required)
    if state.opportunity_type in guidance.specialised:
        for fd in field_defs_from_guidance(guidance.specialised[state.opportunity_type]):
            state.add_field(fd.id, label=fd.label, description=fd.description, required=fd.required)


def _make_agent(*, session_id: str, system_prompt: str, tools: list) -> Agent:
    model = BedrockModel(model_id=DEFAULT_MODEL_ID, region_name=DEFAULT_REGION)

    session_manager = None
    if DEFAULT_MEMORY_ID:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        config = AgentCoreMemoryConfig(
            memory_id=DEFAULT_MEMORY_ID,
            session_id=session_id,
            actor_id=session_id,
        )
        session_manager = AgentCoreMemorySessionManager(config, region_name=DEFAULT_REGION)

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        session_manager=session_manager,
        # Run tools in emitted order: set_opportunity_type adds fields that a
        # later record_captured_field in the same turn may reference.
        tool_executor=SequentialToolExecutor(),
        # The default handler prints model output to stdout, which the runtime
        # ships to CloudWatch. Output is stored in the timeline instead.
        callback_handler=None,
    )


async def process_turns(
    *,
    session_id: str,
    turns: list[TranscriptChunk],
    store: SessionStore,
    last_seq: int = 0,
    assist_config: AssistConfig | None = None,
    call_memory: CallMemory | None = None,
    preset_opportunity_type: str | None = None,
    guidance: GuidanceSet | None = None,
) -> tuple[ProcessResult, Agent]:
    """Run the agent once on new turns, apply its tool results and save the session.

    Args:
        turns: New turns, oldest first. Must not be empty.
        last_seq: Timeline seq of the newest turn; saved as the processing cursor.
        call_memory: Long-term memory for calls with an account. Prior context is
            loaded on the first run that reaches it.
        preset_opportunity_type: Set and locked before the first run (from the opportunity).
        guidance: The call's checklists (its config profile); the built-in set if None.

    The session state is created on the first run.
    """
    if not turns:
        raise ValueError("turns must not be empty")
    config = assist_config or AssistConfig()
    guidance = guidance or builtin_guidance()
    state = store.get(session_id) or SessionState.new(session_id)
    state.chunk_count += 1
    if preset_opportunity_type and not state.opportunity_type_locked:
        _lock_opportunity_type(state, preset_opportunity_type, guidance)
    _seed_guidance_fields(state, guidance)
    use_memory = call_memory is not None and config.use_prior_context != PRIOR_CONTEXT_OFF
    if use_memory and state.prior_context is None:
        state.prior_context = call_memory.load_prior()
    recall = _recall(call_memory, state) if use_memory and call_memory.search else None

    open_ids = {fid for fid, _ in state.open_fields()}
    known_types = guidance.list_opportunity_types()
    known_fields = {
        fid: FieldDef(fid, fs.label, fs.description, fs.required, fs.source)
        for fid, fs in state.fields.items()
    }
    scratch: dict = {}
    tools = build_tools(
        scratch=scratch,
        known_fields=known_fields,
        available_opportunity_types=known_types,
        opportunity_type_locked=state.opportunity_type_locked,
        agent_added_count=state.agent_added_count(),
        load_specialised_fields=lambda t: field_defs_from_guidance(guidance.load(t)),
        askable_field_ids=(
            guidance.askable_field_ids() & open_ids if config.scope == SCOPE_CHECKLIST else open_ids
        ),
        require_target=config.scope == SCOPE_CHECKLIST,
        ask_new_fields=config.scope != SCOPE_CHECKLIST,
        max_suggestions=config.max_suggestions,
        recent_questions=[
            s["question"] for s in state.suggestions[-RECENT_QUESTIONS_FOR_DEDUPE:]
        ],
        turn_seqs=[c.seq for c in turns if c.seq is not None],
        recall=recall,
    )
    system_prompt = _format_prompt(state, known_types, config, recall_available=recall is not None,
                                   guidance=guidance)
    _write_debug_file(session_id, state.chunk_count, "system_prompt.txt", system_prompt)

    agent = _make_agent(session_id=session_id, system_prompt=system_prompt, tools=tools)
    loaded_count = len(agent.messages)  # Restored from AgentCore Memory, if enabled.
    try:
        await agent.invoke_async("\n".join(c.format_line() for c in turns))
    finally:
        _save_messages(session_id, state.chunk_count, agent, loaded_count)

    changes = _apply_tool_results(state, scratch, known_fields, guidance)
    suggestions = _record_suggestions(state, scratch["suggestions"], turns)
    state.last_seq = max(state.last_seq, last_seq)
    store.save(state)
    result = _build_result(state, changes, suggestions, config)
    return replace(result, narration=_narration(agent), memory_lookups=scratch["memory_lookups"]), agent


def _recall(call_memory: CallMemory, state: SessionState) -> Callable[[str, str], list[dict] | None]:
    """The recall tool's search, bound to this call's scope. Drops facts already in the prompt."""
    known = set((state.prior_context or {}).get("account", [])) | set(
        (state.prior_context or {}).get("opportunity", [])
    )

    def search(query: str, level: str) -> list[dict] | None:
        try:
            facts = call_memory.search(call_memory.scope, query, level, RECALL_TOP_K + len(known))
        except Exception:  # noqa: BLE001 - the tool reports "unavailable" and the run goes on.
            logger.warning("memory recall failed session=%r", state.session_id, exc_info=True)
            return None
        return [f for f in facts if f["text"] not in known][:RECALL_TOP_K]

    return search


def _lock_opportunity_type(state: SessionState, opportunity_type: str, guidance: GuidanceSet) -> bool:
    """Set the specialised checklist and add its fields. False if the type is unknown."""
    try:
        specialised = guidance.load(opportunity_type)
    except UnknownOpportunityTypeError:
        logger.warning("unknown opportunity type %r session=%r", opportunity_type, state.session_id)
        return False
    state.opportunity_type = opportunity_type
    state.opportunity_type_locked = True
    for fd in field_defs_from_guidance(specialised):
        state.add_field(fd.id, label=fd.label, description=fd.description, required=fd.required)
    return True


def _narration(agent: Agent) -> list[str]:
    """Text blocks of the agent's final message."""
    messages = getattr(agent, "messages", None) or []
    if not messages or messages[-1].get("role") != "assistant":
        return []
    return [b["text"] for b in messages[-1].get("content", []) if b.get("text")]


@dataclass
class _Changes:
    """What the agent changed in this request."""

    type_resolved: bool = False
    added: list[str] = field(default_factory=list)
    captured: list[str] = field(default_factory=list)
    dismissed: list[str] = field(default_factory=list)


def _apply_tool_results(
    state: SessionState, scratch: dict, known_fields: dict[str, FieldDef], guidance: GuidanceSet
) -> _Changes:
    changes = _Changes()

    new_type = scratch.get("opportunity_type")
    if new_type and not state.opportunity_type_locked:
        changes.type_resolved = _lock_opportunity_type(state, new_type, guidance)

    for fid in scratch["added_gaps"]:
        fd = known_fields[fid]
        if state.add_field(
            fid, label=fd.label, description=fd.description, required=fd.required,
            source=FIELD_SOURCE_AGENT,
        ):
            changes.added.append(fid)

    for fid, info in scratch["captured_fields"].items():
        if fid in state.fields:
            state.set_field_captured(fid, info["value"], info["speaker"], info.get("evidence"))
            changes.captured.append(fid)

    for fid, reason in scratch["dismissed_gaps"].items():
        fs = state.fields.get(fid)
        if fs is None or not fs.is_open or (fs.required and fs.source == FIELD_SOURCE_GUIDANCE):
            continue
        state.set_field_not_applicable(fid, reason)
        changes.dismissed.append(fid)

    if scratch.get("problem_summary"):
        state.problem_summary = scratch["problem_summary"]

    return changes


def _record_suggestions(
    state: SessionState, raw_suggestions: list[dict], chunks: list[TranscriptChunk]
) -> list[Suggestion]:
    """Store the agent's suggestions, dropping any whose target was settled this request."""
    trigger_text = (
        chunks[0].text if len(chunks) == 1 else "\n".join(c.format_line() for c in chunks)
    )
    suggestions = []
    for raw in raw_suggestions:
        target = raw.get("target_field_id")
        target_fs = state.fields.get(target) if target else None
        if target_fs is not None and not target_fs.is_open:
            continue
        suggestion = Suggestion(
            question=raw["question"],
            target_field_id=target if target_fs else None,
            target_label=target_fs.label if target_fs else None,
            rationale=raw.get("rationale"),
        )
        state.add_suggestion(
            question=suggestion.question,
            target_field_id=suggestion.target_field_id,
            target_label=suggestion.target_label,
            trigger_speaker=chunks[-1].speaker,
            trigger_text=trigger_text,
            rationale=suggestion.rationale,
            trigger_seq=chunks[-1].seq,
        )
        suggestions.append(suggestion)
    return suggestions


def _build_result(
    state: SessionState,
    changes: _Changes,
    suggestions: list[Suggestion],
    config: AssistConfig,
) -> ProcessResult:
    return ProcessResult(
        session_id=state.session_id,
        opportunity_type=state.opportunity_type,
        opportunity_type_just_resolved=changes.type_resolved,
        captured_fields=changes.captured,
        gaps=[
            GapItem(fid, fs.label, fs.description, fs.required, fs.source)
            for fid, fs in state.open_fields()
        ],
        suggestions=suggestions,
        problem_summary=state.problem_summary,
        captured=[
            CapturedItem(fid, fs.label, fs.value or "", fs.last_speaker)
            for fid, fs in state.captured_fields()
        ],
        added_gaps=changes.added,
        dismissed_gaps=changes.dismissed,
        config=config,
        run_number=state.chunk_count,
    )


def _write_debug_file(session_id: str, invocation: int, suffix: str, content: str) -> None:
    """Write ``<PROMPT_LOG_DIR>/<session_id>/<NNNN>_<suffix>``. Failures are logged, not raised.

    An empty ``PROMPT_LOG_DIR`` disables the dumps.
    """
    if not PROMPT_LOG_DIR:
        return
    # session_id comes from the client; reduce it to one safe path segment.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id).lstrip(".") or "_"
    try:
        folder = Path(PROMPT_LOG_DIR) / safe_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{invocation:04d}_{suffix}").write_text(content, encoding="utf-8")
    except OSError:
        logger.warning("Could not save %s for session %s", suffix, session_id, exc_info=True)


def _save_messages(session_id: str, invocation: int, agent: Agent, loaded_count: int) -> None:
    """Save the full message history after the run, including restored history."""
    doc = {
        "session_id": session_id,
        "invocation": invocation,
        "loaded_before_invoke": loaded_count,
        "total_after_invoke": len(agent.messages),
        "messages": agent.messages,
    }
    _write_debug_file(
        session_id, invocation, "messages.json",
        json.dumps(doc, indent=2, ensure_ascii=False, default=str),
    )
