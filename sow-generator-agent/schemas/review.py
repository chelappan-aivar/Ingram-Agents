"""Post-generation draft check contracts (deterministic rules plus advisory LLM issues)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.sow_generator.schemas._compat import NonBlankStr
from app.modules.sow_generator.schemas.policies import PolicyCheckSummary, PolicyResult
from app.modules.sow_generator.schemas.upstream import UpstreamInputs


class ValidateDraftRequest(BaseModel):
    """Request body for the post-generation draft check (``POST /api/v1/sow/checks``).

    ``upstream`` is optional: section-presence rules work on the
    markdown alone, but cross-checks (pricing reconciliation, service coverage,
    timeline match) require the upstream handoff to compare against.
    """

    sow_markdown: NonBlankStr = Field(..., description="The generated SOW body in markdown.")
    upstream: UpstreamInputs | None = Field(
        default=None,
        description="Structured Stage 1-3 inputs the draft is cross-checked against.",
    )
    verify: bool = Field(
        default=False,
        description="Also run the LLM reviewer and merge its advisory issues.",
    )


IssueSeverity = Literal["critical", "high", "medium", "low"]
IssueSource = Literal["deterministic", "model"]


class ConsistencyIssue(BaseModel):
    """A single consistency concern raised against a generated SOW draft.

    Mirrors the JSON object the LLM verifier emits (see prompts/verification.txt)
    so deterministic and model-sourced issues share one shape and can be merged
    and de-duplicated by ``(section, title)``.
    """

    id: str = Field(..., description="Stable snake_case identifier, unique within a response.")
    severity: IssueSeverity
    section: str = Field(default="", description="SOW section the issue was found in.")
    title: str = Field(..., description="Short headline (<= 80 chars).")
    detail: str = Field(default="", description="1-3 sentences explaining the issue.")
    suggestion: str = Field(default="", description="Optional concrete fix.")
    source: IssueSource = Field(
        default="deterministic",
        description="'deterministic' (rule-derived) or 'model' (LLM verifier).",
    )


AiReviewStatus = Literal["ok", "failed", "empty_output", "skipped"]


class AiReviewMeta(BaseModel):
    """Observability for the advisory LLM review pass (``verify=true``)."""

    requested: bool = False
    status: AiReviewStatus = "skipped"
    issues_added: int = Field(default=0, description="Model-sourced issues surviving the merge.")
    model_id: str = ""
    detail: str = Field(
        default="", description="Short, non-sensitive reason when the review did not succeed."
    )


class DraftCheckResponse(BaseModel):
    """Combined Layer-1 rule results and Layer-1/2 consistency issues.

    ``can_export`` defaults to True (humans approve drafts); the UI shows a
    blocking banner when any *required* rule fails. A deployment may flip the
    ``SOW_HARD_BLOCK_EXPORT`` behaviour to hard-gate export for demos.
    """

    rules: list[PolicyResult] = Field(default_factory=list)
    issues: list[ConsistencyIssue] = Field(default_factory=list)
    summary: PolicyCheckSummary = Field(default_factory=PolicyCheckSummary)
    can_export: bool = True
    ai_review: AiReviewMeta | None = None
