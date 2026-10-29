"""Local hosting of the console (``console/``) next to the API, for development.

* ``/``                static files from ``console/``
* ``/api/*``           the API, with the access key added here so it never reaches the page
* ``PUT /upload?url``  forwards a presigned upload to the upload bucket (the browser needs no CORS)

The server listens on 127.0.0.1, but any web page open in the same browser can still send
requests to it. So ``/api`` and ``/upload`` only answer requests whose ``Host`` and ``Origin`` are
this server's own and that carry the console's header (which a cross-site page cannot add
without a CORS preflight, and no preflight is ever answered).
"""

from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console"
CONSOLE_HEADER = "x-console"
MAX_UPLOAD_BYTES = 400 * 1024 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json"}


def same_origin(headers: Mapping[str, str], port: int) -> bool:
    """True for a request made by the console page served from this server."""
    own = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if headers.get("host", "") not in own:
        return False  # Blocks DNS rebinding: the Host of a rebound page is the attacker's name.
    origin = headers.get("origin")
    if origin is not None and urlsplit(origin).netloc not in own:
        return False
    return headers.get(CONSOLE_HEADER) == "1"


def static_file(url_path: str) -> tuple[int, dict[str, str], bytes]:
    """The console file for a URL path, 404 for anything outside ``console/``."""
    relative = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
    target = (CONSOLE_DIR / relative).resolve()
    if CONSOLE_DIR.resolve() not in target.parents or not target.is_file():
        return 404, {**SECURITY_HEADERS, "Content-Type": "text/plain; charset=utf-8"}, b"Not found"
    content_type = _TYPES.get(target.suffix) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return 200, {**SECURITY_HEADERS, "Content-Type": content_type}, target.read_bytes()


def allowed_upload_target(url: str, bucket: str | None, extra_hosts: set[str]) -> bool:
    """Only presigned URLs of the upload bucket (or a local S3 stand-in) may be forwarded."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        return False
    if parts.netloc in extra_hosts:
        return True
    return bool(bucket) and parts.scheme == "https" and host.startswith(f"{bucket}.s3.") and host.endswith(".amazonaws.com")


class BoundedBody:
    """Exactly ``length`` bytes of a stream, with a known length.

    Handed to ``requests`` it is sent with a ``Content-Length`` (a presigned S3 upload needs one,
    and a plain generator would be sent chunked) and a large file is never held in memory.
    """

    def __init__(self, stream, length: int) -> None:
        self._stream, self._remaining, self._length = stream, length, length

    def __len__(self) -> int:
        return self._length

    def read(self, size: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        size = self._remaining if size is None or size < 0 else min(size, self._remaining)
        chunk = self._stream.read(size)
        self._remaining -= len(chunk)
        if not chunk:
            self._remaining = 0  # The client stopped early; end instead of waiting forever.
        return chunk
