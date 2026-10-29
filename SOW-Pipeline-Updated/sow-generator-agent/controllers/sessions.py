"""Mints the server-owned session id the UI reuses for generate and export."""

from fastapi import APIRouter

from app.modules.sow_generator.controllers.dependencies import InvokerPrincipal
from app.modules.sow_generator.schemas import SessionResponse
from app.modules.sow_generator.services.session_service import mint_session_id

router = APIRouter()


@router.post("/sessions")
async def create_session(_: InvokerPrincipal) -> SessionResponse:
    return SessionResponse(session_id=mint_session_id())
