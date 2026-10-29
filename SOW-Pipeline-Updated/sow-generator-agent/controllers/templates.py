"""Template listing and preview."""

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import PlainTextResponse

from app.modules.sow_generator.controllers.dependencies import ReaderPrincipal
from app.modules.sow_generator.schemas import TemplateList
from app.modules.sow_generator.services.template_service import (
    get_template_markdown,
    list_template_options,
)

router = APIRouter()

_SEGMENT = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


@router.get("/templates")
async def list_templates(_: ReaderPrincipal) -> TemplateList:
    return TemplateList(templates=list_template_options())


@router.get("/templates/{category}/{stem}", response_class=PlainTextResponse)
async def get_template(
    category: Annotated[str, Path(pattern=_SEGMENT)],
    stem: Annotated[str, Path(pattern=_SEGMENT)],
    _: ReaderPrincipal,
) -> PlainTextResponse:
    return PlainTextResponse(get_template_markdown(category, stem), media_type="text/markdown")
