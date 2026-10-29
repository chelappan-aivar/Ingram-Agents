"""Agentic editor request contracts."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from sow_generator.schemas._compat import NonBlankStr, accept_legacy_client_name


class EditChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ResolvingIssue(BaseModel):
    """Consistency issue the editor is being asked to resolve (editor-side resolution)."""

    id: str = ""
    severity: str = ""
    section: str = ""
    title: str = ""
    detail: str = ""
    suggestion: str = ""
    source: str = ""


class AgentEditRequest(BaseModel):
    customer_name: str = ""
    template_id: str = ""
    selected_text: str = Field(
        default="", description="Currently selected text (empty if no selection)"
    )
    instruction: NonBlankStr
    heading_breadcrumb: str = ""
    document_outline: list[str] = Field(default_factory=list)
    sections_map: dict[str, Any] = Field(
        default_factory=dict,
        description="Heading → structured section data with blocks (block_id+text) and tables",
    )
    chat_history: list[EditChatMessage] = Field(default_factory=list)
    resolving_issue: ResolvingIssue | None = Field(
        default=None,
        description=(
            "Set when the instruction resolves a flagged consistency issue; "
            "gives the agent precise context."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_client_name(cls, data: Any) -> Any:
        """Accept deprecated 'client_name' as an alias for 'customer_name'."""
        return accept_legacy_client_name(data)
