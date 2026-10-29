"""The agentic editor's tools, built per request over the document being edited."""

from typing import Any

from app.modules.sow_generator.agents.tools.proposals import Proposal, build_proposal_tools
from app.modules.sow_generator.agents.tools.section_view import (
    build_read_tools,
    flatten_sections,
)


def build_editor_tools(sections_map: dict[str, Any], proposals: list[Proposal]) -> list[Any]:
    """Read tools first, then the proposal tools; proposals are appended to `proposals`."""
    return [
        *build_read_tools(flatten_sections(sections_map)),
        *build_proposal_tools(sections_map, proposals),
    ]


__all__ = ["Proposal", "build_editor_tools"]
