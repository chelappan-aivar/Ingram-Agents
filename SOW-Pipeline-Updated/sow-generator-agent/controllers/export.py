"""Exports the SOW markdown as a branded `.docx`."""

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.db import DbSession
from app.modules.sow_generator.controllers.dependencies import (
    ExportServiceDep,
    InvokerPrincipal,
    RateLimited,
)
from app.modules.sow_generator.schemas import ExportDocxRequest, ExportLinkResponse
from app.modules.sow_generator.services.sow_audit import SowAuditor

router = APIRouter()

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post(
    "/exports/docx",
    dependencies=[RateLimited],
    response_class=Response,
    responses={200: {"content": {_DOCX_MEDIA_TYPE: {}}, "description": "The .docx file."}},
)
async def export_docx(
    request: ExportDocxRequest,
    principal: InvokerPrincipal,
    session: DbSession,
    service: ExportServiceDep,
) -> Response:
    document = await service.export(request)
    await SowAuditor(session).exported(
        principal.user.id,
        session_id=request.session_id,
        opportunity_id=request.opportunity_id,
        persisted=request.persist_output and bool(request.session_id),
    )
    return Response(
        content=document.content,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


@router.post("/exports/docx-link", dependencies=[RateLimited])
async def export_docx_link(
    request: ExportDocxRequest,
    principal: InvokerPrincipal,
    session: DbSession,
    service: ExportServiceDep,
) -> ExportLinkResponse:
    """Store the `.docx` in S3 and return a short-lived download link."""
    link = await service.export_link(request)
    await SowAuditor(session).exported(
        principal.user.id,
        session_id=link.session_id,
        opportunity_id=request.opportunity_id,
        persisted=True,
    )
    return ExportLinkResponse(
        download_url=link.download_url,
        filename=link.filename,
        expires_in=link.expires_in,
        session_id=link.session_id,
    )
