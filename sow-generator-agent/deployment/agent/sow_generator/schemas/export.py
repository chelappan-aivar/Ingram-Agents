"""DOCX export request contract."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from sow_generator.schemas._compat import NonBlankStr, accept_legacy_client_name
from sow_generator.schemas.session import SessionId
from sow_generator.schemas.upstream import ArchImage


class ExportDocxRequest(BaseModel):
    customer_name: str
    sow_markdown: NonBlankStr
    doc_title: str = "Statement of Work"
    session_id: SessionId | None = Field(
        default=None, description="Session id so outputs land beside their inputs"
    )
    opportunity_id: str | None = Field(default=None, description="Opportunity id for the S3 folder")
    persist_output: bool = Field(
        default=False,
        description=(
            "When true, write final markdown and .docx to configured storage (Produce Save only)."
        ),
    )
    architecture_images: list[ArchImage] = Field(
        default_factory=list,
        description=(
            "Architecture diagrams referenced from the markdown as "
            "![alt](arch:N) or ![alt](filename)"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_client_name(cls, data: Any) -> Any:
        """Accept deprecated 'client_name' as an alias for 'customer_name'."""
        return accept_legacy_client_name(data)


class ExportLinkResponse(BaseModel):
    """A short-lived link to download a stored `.docx`."""

    download_url: str
    filename: str
    expires_in: int
    session_id: str
