"""Validated, transport-neutral inputs handed to the agents."""

from dataclasses import dataclass, field
from typing import Any

from app.modules.sow_generator.schemas import UpstreamInputs


@dataclass(frozen=True, slots=True)
class GenerationInputs:
    customer_name: str
    template_id: str
    project_details: str
    transcripts: list[str]
    clarification_answers: dict[str, str]
    architecture_image_b64: str | None
    project_detail_image_b64s: list[str]
    extra_arch_image_b64s: list[str]
    arch_image_filenames: list[str]
    upstream: UpstreamInputs | None


@dataclass(frozen=True, slots=True)
class EditInputs:
    instruction: str
    selected_text: str
    heading_breadcrumb: str
    document_outline: list[str]
    sections_map: dict[str, Any]
    customer_name: str
    template_id: str
    chat_history: list[dict[str, str]] = field(default_factory=list)
    resolving_issue: dict[str, str] | None = None
