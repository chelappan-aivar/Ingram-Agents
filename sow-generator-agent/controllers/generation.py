"""Streams a generated SOW draft."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.db import DbSession
from app.modules.sow_generator.controllers.dependencies import (
    GenerationServiceDep,
    InvokerPrincipal,
    RateLimited,
)
from app.modules.sow_generator.schemas import GenerateRequest
from app.modules.sow_generator.services.session_service import mint_session_id
from app.modules.sow_generator.services.sow_audit import SowAuditor
from app.modules.sow_generator.views.streaming import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    stream_generation_sse,
)

router = APIRouter()


@router.post(
    "/generations/stream",
    dependencies=[RateLimited],
    response_class=StreamingResponse,
    responses={200: {"content": {SSE_MEDIA_TYPE: {}}, "description": "Server-sent events."}},
)
async def stream_generation(
    request: GenerateRequest,
    principal: InvokerPrincipal,
    session: DbSession,
    service: GenerationServiceDep,
) -> StreamingResponse:
    """Events: `session`, then `chunk` and `usage`, closed by `done` or `error`."""
    # Mint the id here so the audit event and the stream agree on it.
    request = request.model_copy(update={"session_id": request.session_id or mint_session_id()})
    await SowAuditor(session).generation_started(
        principal.user.id,
        session_id=request.session_id,
        opportunity_id=request.resolved_opportunity_id,
        template_id=request.template_id,
    )
    return StreamingResponse(
        stream_generation_sse(service.generate(request)),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
