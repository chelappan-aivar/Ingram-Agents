"""The SOW generator's HTTP controllers, mounted together under `/sow`."""

from fastapi import APIRouter, Depends

from app.core.security import require_csrf
from app.modules.sow_generator.controllers import (
    editor,
    export,
    generation,
    review,
    sessions,
    templates,
)

router = APIRouter(prefix="/sow", tags=["sow"], dependencies=[Depends(require_csrf)])
for _controller in (sessions, templates, generation, editor, review, export):
    router.include_router(_controller.router)

__all__ = ["router"]
