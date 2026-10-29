"""Generation request and token usage contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from sow_generator.schemas._compat import accept_legacy_client_name
from sow_generator.schemas.session import SessionId
from sow_generator.schemas.upstream import UpstreamInputs


class TranscriptFile(BaseModel):
    name: str = Field(..., description="Original filename, used for extension detection")
    content_b64: str = Field(..., description="Data URL or raw base64 of the uploaded file")


class GenerateRequest(BaseModel):
    customer_name: str = Field(..., description="Customer name")
    template_id: str = Field(..., description="Template id like 'aiml/ai-ml'")
    session_id: SessionId | None = Field(
        default=None, description="Server-minted session id for S3 grouping"
    )
    opportunity_id: str | None = Field(
        default=None, description="Opportunity id override; defaults to upstream.meta"
    )
    upstream: UpstreamInputs | None = Field(default=None, description="Structured Stage 1-3 inputs")
    project_details: str = Field(default="", description="Free-text project brief")
    transcript_files: list[TranscriptFile] = Field(default_factory=list)
    project_detail_images: list[TranscriptFile] = Field(
        default_factory=list,
        description="Supporting images attached alongside the project details (data-URL or base64)",
    )
    clarification_answers: dict[str, str] = Field(default_factory=dict)
    architecture_image_b64: str | None = Field(default=None)
    architecture_image_b64s: list[str] = Field(default_factory=list)
    arch_image_filenames: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_client_name(cls, data: Any) -> Any:
        """Accept deprecated 'client_name' as an alias for 'customer_name'."""
        return accept_legacy_client_name(data)

    @property
    def resolved_opportunity_id(self) -> str | None:
        """Prefer an explicit opportunity id, else the upstream handoff's."""
        if self.opportunity_id and self.opportunity_id.strip():
            return self.opportunity_id.strip()
        if self.upstream and self.upstream.meta.opportunity_id.strip():
            return self.upstream.meta.opportunity_id.strip()
        return None


class TokenUsage(BaseModel):
    """Token counts; serialized with the camelCase keys Bedrock and the UI use."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    input_tokens: int = 0
    output_tokens: int = 0
