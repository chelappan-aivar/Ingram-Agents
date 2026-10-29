"""Checks a generated draft against the compliance rules and, optionally, the LLM reviewer."""

from app.core.config import SowSettings
from app.modules.sow_generator.agents.verification import verify_draft
from app.modules.sow_generator.models import VerificationOutcome
from app.modules.sow_generator.schemas import (
    DraftCheckResponse,
    UpstreamInputs,
    ValidateDraftRequest,
)
from app.modules.sow_generator.services.draft_check import run_draft_check


class ReviewService:
    def __init__(self, settings: SowSettings) -> None:
        self._settings = settings

    async def _verify(
        self, sow_markdown: str, upstream: UpstreamInputs | None
    ) -> VerificationOutcome:
        return await verify_draft(self._settings, sow_markdown, upstream)

    async def check(self, request: ValidateDraftRequest) -> DraftCheckResponse:
        return await run_draft_check(request, self._verify)
