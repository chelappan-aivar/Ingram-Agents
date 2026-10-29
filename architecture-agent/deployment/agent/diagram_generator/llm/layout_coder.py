"""LLM 3, the Diagram coder: FinalSpec -> Layout (tool call `submit_layout`).

The model decides box positions and group rectangles, guided by the AWS diagram skill's layout rules
(diagram_generator/prompts/layout_system.md). Code then draws the XML (diagram_generator/render/drawio_builder.py)
and routes every edge with libavoid (diagram_generator/render/edge_router.py). This replaced writing the full
draw.io XML in one answer, which exceeded the output limit for ~35-component architectures.
"""
from __future__ import annotations

import json
from typing import Any

from diagram_generator.contracts import FinalSpec, Layout
from diagram_generator.llm.bedrock import user_msg
from diagram_generator.llm.extractor import inline_refs, load_prompt

TOOL_NAME = "submit_layout"
PROMPT_FILE = "layout_system.md"
MAX_ERRORS = 40
AUX_SHAPES = {"cloudwatch", "cloudwatch_2", "cloudtrail", "xray", "identity_and_access_management"}


def build_tool_spec() -> dict:
    return {"toolSpec": {
        "name": TOOL_NAME,
        "description": "Submit the diagram layout: box positions and group rectangles.",
        "inputSchema": {"json": inline_refs(Layout.model_json_schema())},
    }}


def build_system_prompt() -> str:
    return load_prompt(PROMPT_FILE)


def is_auxiliary(c) -> bool:
    return c.icon.shape in AUX_SHAPES


def build_user_message(spec: FinalSpec) -> str:
    n_c, n_r, n_b = len(spec.components), len(spec.relationships), len(spec.boundaries)
    out = [f"Lay out this architecture: \"{spec.title}\". Direction: left to right.", "",
           "## Components (each is a 120x120 box)", "id | name | kind | boundary | category | auxiliary"]
    for c in spec.components:
        out.append(" | ".join(str(x) for x in (c.id, c.name, c.kind, c.boundary or "-",
                                               c.icon.category_label or c.icon.category,
                                               "yes" if is_auxiliary(c) else "no")))
    out += ["", "## Boundaries (groups; nest children inside parents)", "id | type | name | parent"]
    out += [" | ".join(str(x) for x in (b.id, b.type, b.name, b.parent or "-")) for b in spec.boundaries] or ["(none)"]
    out += ["", "## Relationships (the data flow; code draws and routes these lines)", "id | source -> target | label | direction"]
    out += [f"{r.id} | {r.source} -> {r.target} | {r.label or '-'} | {r.direction}" for r in spec.relationships]
    out += ["", f"Counts: {n_c} nodes, {n_b} groups ({n_r} relationships drawn by code). "
                f"Call `{TOOL_NAME}` once with the complete layout."]
    return "\n".join(out)


def _call(llm, settings, text: str) -> Layout:
    raw: Any = llm.converse_tool(build_system_prompt(), [user_msg(text)], build_tool_spec(),
                                 max_tokens=settings.max_tokens_for("coder"))
    return Layout.model_validate(raw)


def generate_layout(llm, settings, spec: FinalSpec) -> Layout:
    return _call(llm, settings, build_user_message(spec))


def retry_layout(llm, settings, spec: FinalSpec, previous: Layout, errors: list[str]) -> Layout:
    """One fresh request: the original message, the previous layout and the problems found in it."""
    shown = errors[:MAX_ERRORS]
    more = f"\n- ... and {len(errors) - len(shown)} more" if len(errors) > len(shown) else ""
    text = (build_user_message(spec) + "\n\n## Your previous layout\n" +
            json.dumps(previous.model_dump(mode="json"), separators=(",", ":")) +
            "\n\n## It failed these checks\n" + "\n".join(f"- {e}" for e in shown) + more +
            f"\n\nFix every problem (move boxes, resize or move groups) and call `{TOOL_NAME}` again "
            "with the complete corrected layout.")
    return _call(llm, settings, text)
