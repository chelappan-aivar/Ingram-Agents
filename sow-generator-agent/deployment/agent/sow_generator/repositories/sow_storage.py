"""Persists SOW inputs and outputs to S3 under `<root>/<opportunity>/<session>/<inputs|outputs>`."""

import asyncio
import base64
import binascii
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sow_generator.core.config import SowSettings
from sow_generator.core.s3 import S3Client, StorageError
from sow_generator.schemas import GenerateRequest

logger = logging.getLogger(__name__)

DOWNLOAD_URL_TTL_SECONDS: Final = 900

_SAFE_SEGMENT: Final = re.compile(r"[^A-Za-z0-9._-]+")
_DOCX_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@dataclass(frozen=True, slots=True)
class DownloadLink:
    url: str
    filename: str


@dataclass(frozen=True, slots=True)
class StoredDocument:
    key: str
    filename: str


def new_session_id() -> str:
    """Mint a fresh server-owned session id."""
    return uuid.uuid4().hex


def _sanitize(segment: str, fallback: str) -> str:
    """Reduce an id to an S3-key-safe path segment, or fall back when empty."""
    cleaned = _SAFE_SEGMENT.sub("-", (segment or "").strip()).strip("-")
    return cleaned or fallback


def _decode(content_b64: str) -> bytes:
    """Decode a data-URL-or-raw base64 string to bytes."""
    payload = content_b64
    if content_b64.startswith("data:") and ";base64," in content_b64:
        payload = content_b64.split(";base64,", 1)[1]
    return base64.b64decode(payload)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class SowStorage:
    """The SOW key layout over an S3 client; disabled (a no-op) when no bucket is configured.

    `save_inputs` and `save_outputs` are best-effort on purpose: a storage outage must not fail
    a generation the user is waiting on, so failures are logged and swallowed.
    `upload_docx_for_download` raises because the download link is the response itself.
    """

    def __init__(self, client: S3Client | None, *, root_prefix: str, misc_folder: str) -> None:
        self._client = client
        self._root_prefix = root_prefix
        self._misc_folder = misc_folder

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _prefix(self, opportunity_id: str | None, session_id: str, leaf: str) -> str:
        opportunity = _sanitize(opportunity_id or "", self._misc_folder)
        session = _sanitize(session_id, "unknown-session")
        return f"{self._root_prefix}/{opportunity}/{session}/{leaf}"

    async def _put_quietly(self, key: str, body: bytes, content_type: str) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.put_object(key, body, content_type)
        except StorageError:
            logger.warning("object skipped after a failed upload", extra={"key": key})
            return False
        return True

    async def save_inputs(
        self, opportunity_id: str | None, session_id: str, request: GenerateRequest
    ) -> None:
        """Persist the request and every uploaded attachment under `inputs/`."""
        if not self.enabled:
            logger.warning("storage disabled; skipping inputs", extra={"session_id": session_id})
            return
        prefix = self._prefix(opportunity_id, session_id, "inputs")
        uploads: list[tuple[str, bytes, str]] = []
        payload = request.model_dump(mode="json", exclude_none=True)
        uploads.append(
            (f"{prefix}/request.json", json.dumps(payload, indent=2).encode(), "application/json")
        )

        attachments: list[tuple[str, str, str]] = [
            (f"{prefix}/transcripts/{_sanitize(f.name, 'file')}", f.content_b64, "")
            for f in request.transcript_files
        ]
        attachments += [
            (
                f"{prefix}/project-detail-images/{_sanitize(img.name, f'image-{i}')}",
                img.content_b64,
                "",
            )
            for i, img in enumerate(request.project_detail_images)
        ]
        architecture = (
            [request.architecture_image_b64] if request.architecture_image_b64 else []
        ) + list(request.architecture_image_b64s)
        names = request.arch_image_filenames
        attachments += [
            (
                f"{prefix}/architecture-images/"
                f"{_sanitize(names[i] if i < len(names) else '', f'arch-{i}.png')}",
                b64,
                "image/png",
            )
            for i, b64 in enumerate(architecture)
        ]
        for key, content_b64, content_type in attachments:
            try:
                uploads.append(
                    (key, _decode(content_b64), content_type or "application/octet-stream")
                )
            except (binascii.Error, ValueError):
                logger.warning("attachment skipped: not valid base64", extra={"key": key})
        await asyncio.gather(*(self._put_quietly(key, body, ctype) for key, body, ctype in uploads))

    async def save_outputs(
        self,
        opportunity_id: str | None,
        session_id: str,
        base_name: str,
        markdown: str,
        docx_bytes: bytes,
    ) -> list[str]:
        """Persist the generated markdown and `.docx` under a timestamped `outputs/` name.

        Returns the keys that were stored (best effort: a failed upload is logged and left out).
        """
        if not self.enabled:
            logger.warning("storage disabled; skipping outputs", extra={"session_id": session_id})
            return []
        prefix = self._prefix(opportunity_id, session_id, "outputs")
        stem = f"{_sanitize(base_name, 'SOW')}_{_timestamp()}"
        stored: list[str] = []
        if markdown:
            key = f"{prefix}/{stem}.md"
            if await self._put_quietly(key, markdown.encode(), "text/markdown"):
                stored.append(key)
        if docx_bytes:
            key = f"{prefix}/{stem}.docx"
            if await self._put_quietly(key, docx_bytes, _DOCX_CONTENT_TYPE):
                stored.append(key)
        return stored

    async def upload_docx_for_download(
        self, opportunity_id: str | None, session_id: str, base_name: str, docx_bytes: bytes
    ) -> DownloadLink:
        """Store a `.docx` under `outputs/` and return a short-lived download link.

        Raises StorageError when storage is disabled or the upload fails.
        """
        stored = await self.upload_docx(opportunity_id, session_id, base_name, docx_bytes)
        assert self._client is not None
        url = self._client.presign_download(
            stored.key, filename=stored.filename, expires_in=DOWNLOAD_URL_TTL_SECONDS
        )
        return DownloadLink(url=url, filename=stored.filename)

    async def upload_docx(
        self, opportunity_id: str | None, session_id: str, base_name: str, docx_bytes: bytes
    ) -> "StoredDocument":
        """Store a `.docx` under `outputs/` and return its key (the API Lambda presigns the download).

        Raises StorageError when storage is disabled or the upload fails.
        """
        if self._client is None:
            raise StorageError("Storage is not configured.")
        prefix = self._prefix(opportunity_id, session_id, "outputs")
        filename = f"{_sanitize(base_name, 'SOW')}_{_timestamp()}.docx"
        key = f"{prefix}/{filename}"
        await self._client.put_object(key, docx_bytes, _DOCX_CONTENT_TYPE)
        return StoredDocument(key=key, filename=filename)


def create_sow_storage(settings: SowSettings) -> SowStorage:
    client = (
        S3Client(
            bucket=settings.sow_s3_bucket,
            region=settings.aws_region,
            profile=settings.aws_profile,
        )
        if settings.storage_enabled
        else None
    )
    return SowStorage(
        client, root_prefix=settings.sow_s3_root_prefix, misc_folder=settings.sow_s3_misc_folder
    )
