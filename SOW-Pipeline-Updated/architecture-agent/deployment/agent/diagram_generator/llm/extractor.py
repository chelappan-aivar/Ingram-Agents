"""LLM 1 (Extractor): forced tool call `submit_extraction`, plus a repair call.

The system prompt is static (diagram_generator/prompts/extractor_system.md, no placeholders). The catalog section
(Catalog.extractor_prompt_section()) and the documents go in the user message.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from diagram_generator.config import Settings
from diagram_generator.contracts import Extraction, SourceDoc

try:  # bedrock.py is written by WP1; fall back so this module imports on its own
    from diagram_generator.llm.bedrock import user_msg
except ImportError:  # pragma: no cover
    def user_msg(text: str) -> dict:
        return {"role": "user", "content": [{"text": text}]}

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
TOOL_NAME = "submit_extraction"
PROMPT_FILE = "extractor_system.md"


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def inline_refs(schema: dict) -> dict:
    """Bedrock tool schemas are safest without $ref/$defs/title: inline them."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            # drop "$defs" and the "title" annotation (a string), but keep a property NAMED "title"
            return {k: walk(v) for k, v in node.items()
                    if k != "$defs" and not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def build_tool_spec() -> dict:
    schema = inline_refs(Extraction.model_json_schema())
    return {
        "toolSpec": {
            "name": TOOL_NAME,
            "description": "Submit the extracted components, relationships, boundaries, "
                           "contradictions and resolutions, each with evidence.",
            "inputSchema": {"json": schema},
        }
    }


def documents_block(sources: list[SourceDoc]) -> str:
    parts = []
    for s in sources:
        name = s.file.replace('"', "&quot;")
        parts.append(f'<document file="{name}">\n{s.text}\n</document>')
    return "\n\n".join(parts)


def _user_text(sources: list[SourceDoc], catalog_section: str) -> str:
    return (
        documents_block(sources)
        + "\n\n<catalog>\n" + catalog_section + "\n</catalog>\n\n"
        f"Extract the architecture by calling {TOOL_NAME} once. "
        "Use the file names above exactly in evidence, and quotes copied verbatim."
    )


def _call(llm, settings: Settings, text: str) -> dict:
    return llm.converse_tool(load_prompt(PROMPT_FILE), [user_msg(text)], build_tool_spec(),
                             max_tokens=settings.max_tokens_for("extractor"))


def extract(llm, settings: Settings, sources: list[SourceDoc], catalog_section: str) -> dict:
    """Return the raw tool input as produced by the model (not yet checked by Harness 1)."""
    return _call(llm, settings, _user_text(sources, catalog_section))


def repair(llm, settings: Settings, sources: list[SourceDoc], catalog_section: str,
           previous: dict, errors: list[str]) -> dict:
    text = (
        _user_text(sources, catalog_section)
        + "\n\nA previous attempt produced this extraction:\n"
        "<previous_extraction>\n" + json.dumps(previous, indent=1, ensure_ascii=False) + "\n</previous_extraction>\n\n"
        "It failed the checks with these errors:\n" + "\n".join(f"- {e}" for e in errors) + "\n\n"
        f"Call {TOOL_NAME} again with a corrected, COMPLETE extraction that fixes every error. "
        "Keep everything else faithful to the documents."
    )
    return _call(llm, settings, text)
