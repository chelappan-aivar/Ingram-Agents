"""Revise an existing Design from a free-text edit instruction.

Reuses `build_tool_spec()` from adr_generator/generator.py UNCHANGED, so the revised result validates against the
same `Design` schema as the initial generation. Reads adr_generator/prompts/revise_system.md as the system prompt
(placeholder pending final prompt text from the lead)."""
from __future__ import annotations

from pathlib import Path

from adr_generator.availability import AvailabilityPolicy
from adr_generator.config import Settings
from adr_generator.contracts import Design
from adr_generator.generator import TOOL_NAME, build_tool_spec
from adr_generator.llm.bedrock import LLMClient, user_msg
from adr_generator.scope import Scope

PROMPT_FILE = "revise_system.md"


def build_system_prompt(settings: Settings, scope: Scope | None = None) -> str:
    """The revise prompt plus the same availability and scope policies the initial design used.

    Without these a revision silently reverts to whatever the static prompt implies, which is how
    a multi-AZ design would quietly become single-AZ on the first edit.
    """
    path = Path(settings.prompts_dir) / PROMPT_FILE
    parts = [path.read_text(encoding="utf-8").strip() if path.is_file() else ""]
    parts.append(AvailabilityPolicy.from_settings(settings).design_rule())
    if scope:
        parts.append(scope.design_rule())
    return "\n\n---\n\n".join(p for p in parts if p)


def build_user_message(srs_text: str, design: Design, instruction: str, scope: Scope | None = None) -> str:
    import json

    design_json = json.dumps(design.model_dump(mode="json"), indent=2, ensure_ascii=False)
    scope_block = f"{scope.prompt_block()}\n\n" if scope else ""
    return (
        f'<document file="srs">\n{srs_text}\n</document>\n\n'
        f"{scope_block}"
        f"The document above is the original Software Requirements Specification (SRS).\n\n"
        f"Here is the current design, as JSON:\n```json\n{design_json}\n```\n\n"
        f"Edit instruction from the user:\n{instruction}\n\n"
        f"Apply the instruction and call the `{TOOL_NAME}` tool exactly once with the complete, updated design."
    )


def revise_design(llm: LLMClient, settings: Settings, design: Design, srs_text: str,
                   instruction: str, scope: Scope | None = None) -> tuple[Design, dict]:
    """One forced tool call that revises `design` per `instruction`. Returns (validated Design, raw tool input),
    mirroring generator.generate()'s return shape."""
    raw = llm.converse_tool(
        build_system_prompt(settings, scope),
        [user_msg(build_user_message(srs_text, design, instruction, scope))],
        build_tool_spec(),
        settings.max_tokens,
    )
    return Design.model_validate(raw), raw
