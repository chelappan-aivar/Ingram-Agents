"""LLM 2 (Assumer): forced tool call `submit_assumptions`. It may only ADD items."""
from __future__ import annotations

from diagram_generator.config import Settings
from diagram_generator.contracts import AssumerOutput, Extraction, SourceDoc
from diagram_generator.llm.extractor import documents_block, inline_refs, load_prompt, user_msg

TOOL_NAME = "submit_assumptions"
PROMPT_FILE = "assumer_system.md"


def build_tool_spec() -> dict:
    schema = inline_refs(AssumerOutput.model_json_schema())
    return {
        "toolSpec": {
            "name": TOOL_NAME,
            "description": "Submit NEW assumed components, relationships and boundaries "
                           "(add-only, each with supports_ids and evidence) and unresolved gaps.",
            "inputSchema": {"json": schema},
        }
    }


def assume(llm, settings: Settings, sources: list[SourceDoc], extraction: Extraction,
           catalog_section: str) -> dict:
    """Return the raw tool input (not yet checked by Harness 2)."""
    text = (
        documents_block(sources)
        + "\n\n<extraction>\n" + extraction.model_dump_json(indent=1) + "\n</extraction>\n\n"
        "<catalog>\n" + catalog_section + "\n</catalog>\n\n"
        f"Decide what must be added, applying the three-way test, and call {TOOL_NAME} once. "
        "Output only NEW items (ids starting a_ / ar1..); never repeat or change extracted ones."
    )
    return llm.converse_tool(load_prompt(PROMPT_FILE), [user_msg(text)], build_tool_spec(),
                             max_tokens=settings.max_tokens_for("assumer"))
