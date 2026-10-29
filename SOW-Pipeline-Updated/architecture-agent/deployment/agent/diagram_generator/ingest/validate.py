"""Upload validation: extension, size and magic bytes (per file)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class UploadError(Exception):
    """Raised with an HTTP status hint."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def safe_filename(name: str | None) -> str:
    base = Path((name or "upload").replace("\\", "/")).name.strip()
    cleaned = "".join(ch if (ch.isalnum() or ch in "._- ()") else "_" for ch in base).strip(" .")
    return cleaned or "upload"


def validate_upload(filename: str, data: bytes, settings) -> None:
    """Raise UploadError when the file type, size or content is not acceptable."""
    ext = Path(filename).suffix.lower()
    max_bytes = settings.max_upload_bytes
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError(f"Unsupported file type '{ext or filename}'. Accepted: .pdf, .docx, .txt, .md", 415)
    if len(data) == 0:
        raise UploadError(f"Uploaded file '{filename}' is empty", 400)
    if len(data) > max_bytes:
        raise UploadError(f"File '{filename}' is too large (max {max_bytes // (1024 * 1024)} MB)", 413)

    if ext == ".pdf":
        if not data.lstrip()[:5].startswith(b"%PDF"):
            raise UploadError(f"'{filename}' is not a valid PDF", 415)
    elif ext == ".docx":
        if data[:2] != b"PK":
            raise UploadError(f"'{filename}' is not a valid DOCX", 415)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise UploadError(f"'{filename}' is not a valid DOCX (word/document.xml missing)", 415)
        except zipfile.BadZipFile:
            raise UploadError(f"'{filename}' is not a valid DOCX", 415)
    else:  # txt / md
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadError(f"Text file '{filename}' must be UTF-8 encoded", 415)
