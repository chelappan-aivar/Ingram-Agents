"""Request-time providers shared by the SOW controllers."""

import math
from typing import Annotated, cast

from fastapi import Depends, Request

from app.core.config import SowSettings, get_app_sow_settings
from app.core.errors import RateLimitedError
from app.core.security import Permission, SlidingWindowRateLimiter
from app.modules.auth.dependencies import Principal, require_permission
from app.modules.sow_generator.repositories.sow_storage import SowStorage
from app.modules.sow_generator.services.editor_service import EditorService
from app.modules.sow_generator.services.export_service import ExportService
from app.modules.sow_generator.services.generation_service import GenerationService
from app.modules.sow_generator.services.review_service import ReviewService


def get_sow_storage(request: Request) -> SowStorage:
    return cast(SowStorage, request.app.state.sow_storage)


SowSettingsDep = Annotated[SowSettings, Depends(get_app_sow_settings)]
SowStorageDep = Annotated[SowStorage, Depends(get_sow_storage)]

# Permissions are declared on each route; a route without one is denied (see ADR 0003).
ReaderPrincipal = Annotated[Principal, Depends(require_permission(Permission.AGENT_READ))]
InvokerPrincipal = Annotated[Principal, Depends(require_permission(Permission.AGENT_INVOKE))]


def get_generation_service(settings: SowSettingsDep, storage: SowStorageDep) -> GenerationService:
    return GenerationService(settings, storage)


def get_editor_service(settings: SowSettingsDep) -> EditorService:
    return EditorService(settings)


def get_review_service(settings: SowSettingsDep) -> ReviewService:
    return ReviewService(settings)


def get_export_service(settings: SowSettingsDep, storage: SowStorageDep) -> ExportService:
    return ExportService(settings, storage)


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]
EditorServiceDep = Annotated[EditorService, Depends(get_editor_service)]
ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
ExportServiceDep = Annotated[ExportService, Depends(get_export_service)]


async def enforce_sow_rate_limit(request: Request, principal: InvokerPrincipal) -> None:
    """Limit each user separately for each costly (LLM or rendering) endpoint."""
    limiter = cast(SlidingWindowRateLimiter, request.app.state.sow_rate_limiter)
    retry_after = limiter.hit(f"{principal.user.id}|{request.url.path}")
    if retry_after is not None:
        raise RateLimitedError(headers={"Retry-After": str(max(1, math.ceil(retry_after)))})


RateLimited = Depends(enforce_sow_rate_limit)
