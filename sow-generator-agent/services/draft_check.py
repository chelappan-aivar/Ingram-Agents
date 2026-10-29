"""Draft verification: deterministic rules plus the optional LLM reviewer."""

from collections.abc import Awaitable, Callable

from app.modules.sow_generator.models import VerificationOutcome
from app.modules.sow_generator.schemas import (
    AiReviewMeta,
    DraftCheckResponse,
    UpstreamInputs,
    ValidateDraftRequest,
)
from app.modules.sow_generator.services.draft_rules import check_draft, merge_issues

type Verifier = Callable[[str, UpstreamInputs | None], Awaitable[VerificationOutcome]]


async def run_draft_check(request: ValidateDraftRequest, verify: Verifier) -> DraftCheckResponse:
    """Run the rules, then merge in the reviewer's issues when verification is requested."""
    result = check_draft(request.sow_markdown, request.upstream)
    if request.verify:
        outcome = await verify(request.sow_markdown, request.upstream)
        result.issues = merge_issues(result.issues, outcome.issues)
        result.ai_review = AiReviewMeta(
            requested=True,
            status=outcome.status,
            issues_added=sum(1 for i in result.issues if i.source == "model"),
            model_id=outcome.model_id,
            detail=outcome.detail,
        )
    return result
