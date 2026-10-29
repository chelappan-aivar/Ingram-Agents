"""Pydantic contracts for the SOW generator, split by use case."""

from app.modules.sow_generator.schemas.editing import (
    AgentEditRequest,
    EditChatMessage,
    ResolvingIssue,
)
from app.modules.sow_generator.schemas.export import ExportDocxRequest, ExportLinkResponse
from app.modules.sow_generator.schemas.generation import (
    GenerateRequest,
    TokenUsage,
    TranscriptFile,
)
from app.modules.sow_generator.schemas.policies import (
    PolicyCheckSummary,
    PolicyResult,
    ValidateInputsRequest,
    ValidateInputsResponse,
)
from app.modules.sow_generator.schemas.review import (
    AiReviewMeta,
    ConsistencyIssue,
    DraftCheckResponse,
    ValidateDraftRequest,
)
from app.modules.sow_generator.schemas.session import SessionResponse
from app.modules.sow_generator.schemas.templates import TemplateList, TemplateOption
from app.modules.sow_generator.schemas.upstream import (
    ArchImage,
    Architecture,
    Contact,
    Cost,
    CostLineItem,
    DiscoveryNotes,
    FileRef,
    Intake,
    Meta,
    Service,
    Stakeholder,
    TranscriptRef,
    UpstreamInputs,
)

__all__ = [
    "AgentEditRequest",
    "AiReviewMeta",
    "ArchImage",
    "Architecture",
    "ConsistencyIssue",
    "Contact",
    "Cost",
    "CostLineItem",
    "DiscoveryNotes",
    "DraftCheckResponse",
    "EditChatMessage",
    "ExportDocxRequest",
    "ExportLinkResponse",
    "FileRef",
    "GenerateRequest",
    "Intake",
    "Meta",
    "PolicyCheckSummary",
    "PolicyResult",
    "ResolvingIssue",
    "Service",
    "SessionResponse",
    "Stakeholder",
    "TemplateList",
    "TemplateOption",
    "TokenUsage",
    "TranscriptFile",
    "TranscriptRef",
    "UpstreamInputs",
    "ValidateDraftRequest",
    "ValidateInputsRequest",
    "ValidateInputsResponse",
]
