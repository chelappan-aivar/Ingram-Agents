"""Checks a generated draft against the compliance rules and, optionally, the LLM reviewer."""

from sow_generator.core.config import SowSettings
from sow_generator.agents.verification import verify_draft
from sow_generator.models import VerificationOutcome
from sow_generator.schemas import (
    DraftCheckResponse,
    UpstreamInputs,
    ValidateDraftRequest,
)
from sow_generator.services.draft_check import run_draft_check


class ReviewService:
    def __init__(self, settings: SowSettings) -> None:
        self._settings = settings

    async def _verify(
        self, sow_markdown: str, upstream: UpstreamInputs | None
    ) -> VerificationOutcome:
        return await verify_draft(self._settings, sow_markdown, upstream)

    async def check(self, request: ValidateDraftRequest) -> DraftCheckResponse:
        return await run_draft_check(request, self._verify)
