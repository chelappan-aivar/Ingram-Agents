"""Reviews a SOW draft against the Well-Architected pillars, before the architecture diagram.

Mirrors ReviewService: deterministic rules always, the LLM pass only when the caller asks for it.
"""

from sow_generator.agents.wa_verification import wa_review_draft
from sow_generator.core.config import SowSettings
from sow_generator.schemas.upstream import UpstreamInputs
from sow_generator.schemas.wa import WaReviewRequest, WaReviewResponse
from sow_generator.services.wa_review import WaVerificationOutcome, run_wa_review


class WaReviewService:
    def __init__(self, settings: SowSettings) -> None:
        self._settings = settings

    async def _verify(
        self, sow_markdown: str, upstream: UpstreamInputs | None
    ) -> WaVerificationOutcome:
        return await wa_review_draft(self._settings, sow_markdown, upstream)

    async def review(self, request: WaReviewRequest) -> WaReviewResponse:
        return await run_wa_review(request, self._verify)
