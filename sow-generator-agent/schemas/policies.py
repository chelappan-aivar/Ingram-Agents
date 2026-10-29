"""Intake completeness (policy) contracts; PolicyResult is reused by the draft check."""

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.sow_generator.schemas.upstream import UpstreamInputs


class ValidateInputsRequest(BaseModel):
    """Request body for the Step 2 policy completeness check."""

    upstream: UpstreamInputs


PolicySeverity = Literal["required", "advisory"]
PolicyStatus = Literal["pass", "fail", "not_applicable"]


class PolicyResult(BaseModel):
    """Outcome of verifying one company policy against the submitted inputs."""

    id: str
    name: str
    description: str = ""
    severity: PolicySeverity
    status: PolicyStatus
    detail: str = Field(
        default="",
        description="For failures, why the policy was not met and what to supply.",
    )


class PolicyCheckSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    advisory: int = 0  # advisory policies that did not pass


class ValidateInputsResponse(BaseModel):
    """Ordered per-policy results plus the generation gate.

    ``can_generate`` is True only when no ``required`` policy failed.
    """

    can_generate: bool
    results: list[PolicyResult] = Field(default_factory=list)
    summary: PolicyCheckSummary = Field(default_factory=PolicyCheckSummary)
