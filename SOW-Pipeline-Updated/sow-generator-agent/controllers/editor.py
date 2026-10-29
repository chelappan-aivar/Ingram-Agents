"""Streams the agentic editor's events."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.db import DbSession
from app.modules.sow_generator.controllers.dependencies import (
    EditorServiceDep,
    InvokerPrincipal,
    RateLimited,
)
from app.modules.sow_generator.schemas import AgentEditRequest
from app.modules.sow_generator.services.sow_audit import SowAuditor
from app.modules.sow_generator.views.streaming import (
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    stream_editor_sse,
)

router = APIRouter()


@router.post(
    "/edits/stream",
    dependencies=[RateLimited],
    response_class=StreamingResponse,
    responses={200: {"content": {SSE_MEDIA_TYPE: {}}, "description": "Server-sent events."}},
)
async def stream_edit(
    request: AgentEditRequest,
    principal: InvokerPrincipal,
    session: DbSession,
    service: EditorServiceDep,
) -> StreamingResponse:
    await SowAuditor(session).edit_requested(principal.user.id, template_id=request.template_id)
    return StreamingResponse(
        stream_editor_sse(service.edit(request)),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
