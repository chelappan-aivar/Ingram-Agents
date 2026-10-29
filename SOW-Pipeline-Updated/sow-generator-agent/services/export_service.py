"""Renders the branded `.docx` and optionally stores it."""

import asyncio
import logging
from dataclasses import dataclass

from app.core.config import SowSettings
from app.integrations.aws.s3 import StorageError
from app.modules.sow_generator.errors import (
    ExportFailedError,
    ExportStorageError,
    ExportStorageUnavailableError,
)
from app.modules.sow_generator.repositories.sow_storage import (
    DOWNLOAD_URL_TTL_SECONDS,
    SowStorage,
    new_session_id,
)
from app.modules.sow_generator.schemas import ExportDocxRequest
from app.modules.sow_generator.services.agent_inputs import safe_file_stem
from app.modules.sow_generator.views.docx import build_sow_docx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExportedDocument:
    content: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class ExportLink:
    download_url: str
    filename: str
    expires_in: int
    session_id: str


class ExportService:
    def __init__(self, settings: SowSettings, storage: SowStorage) -> None:
        self._settings = settings
        self._storage = storage

    async def _render(self, request: ExportDocxRequest) -> bytes:
        try:
            return await asyncio.to_thread(
                build_sow_docx,
                request.customer_name,
                request.sow_markdown,
                request.doc_title,
                request.architecture_images,
                company=self._settings.company,
            )
        except Exception as error:
            logger.exception("docx render failed")
            raise ExportFailedError from error

    async def export(self, request: ExportDocxRequest) -> ExportedDocument:
        """Render the document; also store it when the request asks to persist the output."""
        content = await self._render(request)
        stem = safe_file_stem(request.customer_name)
        if request.persist_output and request.session_id:
            await self._storage.save_outputs(
                request.opportunity_id,
                request.session_id,
                f"{stem}_SOW",
                request.sow_markdown,
                content,
            )
        return ExportedDocument(content=content, filename=f"{stem}_SOW.docx")

    async def export_link(self, request: ExportDocxRequest) -> ExportLink:
        """Render and store the document, and return a short-lived download link."""
        if not self._storage.enabled:
            raise ExportStorageUnavailableError
        stem = safe_file_stem(request.customer_name)
        session_id = request.session_id or new_session_id()
        content = await self._render(request)
        try:
            link = await self._storage.upload_docx_for_download(
                request.opportunity_id, session_id, f"{stem}_SOW", content
            )
            if request.persist_output:
                await self._storage.save_outputs(
                    request.opportunity_id, session_id, f"{stem}_SOW", request.sow_markdown, b""
                )
        except StorageError as error:
            raise ExportStorageError from error
        return ExportLink(
            download_url=link.url,
            filename=f"{stem}_SOW.docx",
            expires_in=DOWNLOAD_URL_TTL_SECONDS,
            session_id=session_id,
        )
