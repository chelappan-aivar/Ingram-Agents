"""Checks a generated draft against the compliance rules and the optional AI reviewer."""

from fastapi import APIRouter

from app.core.db import DbSession
from app.modules.sow_generator.controllers.dependencies import (
    InvokerPrincipal,
    RateLimited,
    ReviewServiceDep,
)
from app.modules.sow_generator.schemas import DraftCheckResponse, ValidateDraftRequest
from app.modules.sow_generator.services.sow_audit import SowAuditor

router = APIRouter()


@router.post("/checks", dependencies=[RateLimited])
async def check_draft(
    request: ValidateDraftRequest,
    principal: InvokerPrincipal,
    session: DbSession,
    service: ReviewServiceDep,
) -> DraftCheckResponse:
    await SowAuditor(session).draft_checked(principal.user.id, ai_review=request.verify)
    return await service.check(request)
