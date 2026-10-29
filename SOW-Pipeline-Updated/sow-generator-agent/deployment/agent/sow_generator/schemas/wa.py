"""Well-Architected review contracts for a SOW draft.

This stage sits between the SOW and the architecture diagram. Running it against the SOW
catches a bad service decision while the architect is still writing prose, which is far cheaper
than catching it after a diagram and a cost estimate have been built on top of it.

Why this is ours and not the AWS managed Well-Architected Agent: that service's on-demand
architecture review takes an S3 URI of CDK or Terraform and returns updated templates
(https://docs.aws.amazon.com/wellarchitected/latest/userguide/agent-architecture-reviews.html).
It cannot read a SOW, and nothing upstream of this stage emits IaC. The managed agent remains
the right tool once IaC exists; see WaReviewResponse.managed_agent_note.
"""

from typing import Literal

from pydantic import BaseModel, Field

from sow_generator.schemas._compat import NonBlankStr
from sow_generator.schemas.upstream import UpstreamInputs

# The four pillars the AWS managed agent scores, plus the two it does not. A SOW is reviewable
# against all six because it is a document, not a deployed workload.
WaPillar = Literal[
    "operational_excellence",
    "security",
    "reliability",
    "performance_efficiency",
    "cost_optimization",
    "sustainability",
]

PILLAR_LABELS: dict[str, str] = {
    "operational_excellence": "Operational Excellence",
    "security": "Security",
    "reliability": "Reliability",
    "performance_efficiency": "Performance Efficiency",
    "cost_optimization": "Cost Optimization",
    "sustainability": "Sustainability",
}

WaSeverity = Literal["high", "medium", "low"]
WaSource = Literal["deterministic", "model"]


class WaReviewRequest(BaseModel):
    """Request body for the SOW Well-Architected review (``POST /api/v1/sow/wa-reviews``)."""

    sow_markdown: NonBlankStr = Field(..., description="The SOW body in markdown.")
    upstream: UpstreamInputs | None = Field(
        default=None,
        description="Optional upstream handoff. Architecture NFRs and assumptions sharpen the "
                    "reliability and performance checks when present.",
    )
    verify: bool = Field(
        default=False,
        description="Also run the LLM reviewer and merge its advisory findings.",
    )
    template_id: str | None = Field(
        default=None,
        description="'<category>/<stem>' of the SOW template. An assessment template (cva/*) skips the "
                    "build-oriented checks, because an assessment deploys nothing.",
    )


class WaFinding(BaseModel):
    """One Well-Architected concern raised against the SOW."""

    id: str = Field(..., description="Stable snake_case identifier, unique within a response.")
    pillar: WaPillar
    severity: WaSeverity
    title: str = Field(..., description="Short headline (<= 80 chars).")
    detail: str = Field(default="", description="1-3 sentences: what is missing or wrong, and why it matters.")
    suggestion: str = Field(default="", description="The concrete change to make to the SOW.")
    evidence: str = Field(
        default="",
        description="The SOW text the finding is based on, or a note that the expected text is absent.",
    )
    section: str = Field(default="", description="SOW section the finding applies to.")
    source: WaSource = Field(default="deterministic")


class WaPillarSummary(BaseModel):
    pillar: WaPillar
    label: str = ""
    findings: int = 0
    high: int = 0


class WaReviewSummary(BaseModel):
    total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    pillars: list[WaPillarSummary] = Field(default_factory=list)


WaReviewStatus = Literal["ok", "failed", "empty_output", "skipped"]


class WaAiReviewMeta(BaseModel):
    """Observability for the advisory LLM pass (``verify=true``)."""

    requested: bool = False
    status: WaReviewStatus = "skipped"
    findings_added: int = 0
    model_id: str = ""
    detail: str = ""


class WaReviewResponse(BaseModel):
    """Findings across the six pillars, newest-first by severity.

    ``can_proceed`` is advisory like the draft check: humans decide. It is False when a *high*
    finding is open, which is the signal to fix the SOW before the diagram is drawn rather than
    after.
    """

    findings: list[WaFinding] = Field(default_factory=list)
    summary: WaReviewSummary = Field(default_factory=WaReviewSummary)
    can_proceed: bool = True
    ai_review: WaAiReviewMeta | None = None
    managed_agent_note: str = Field(
        default=(
            "The AWS managed Well-Architected Agent reviews CDK or Terraform from an S3 URI, not "
            "prose, so it cannot review this SOW. Once the engagement produces IaC, run it there "
            "for the Cost Optimization, Security, Reliability and Performance pillars instead of "
            "extending these checks."
        ),
        description="Why this review is local and where the managed AWS agent fits instead.",
    )
