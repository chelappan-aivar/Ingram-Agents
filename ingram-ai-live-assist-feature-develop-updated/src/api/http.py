"""HTTP primitives shared by the API route modules: request, response, errors, cursors."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

MAX_BODY_BYTES = 64 * 1024
MAX_CALL_BODY_BYTES = 256 * 1024  # POST /calls can carry a pasted transcript.


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list[dict] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)  # Lowercase names.
    query: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self, max_bytes: int = MAX_BODY_BYTES) -> dict:
        """The body as a JSON object; empty bodies are ``{}``."""
        if not self.body:
            return {}
        if len(self.body) > max_bytes:
            raise ApiError(413, "body_too_large", f"Body must be at most {max_bytes} bytes")
        try:
            data = json.loads(self.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "invalid_json", "Body must be valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_json", "Body must be a JSON object")
        return data


@dataclass
class Response:
    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    def encoded(self) -> tuple[int, dict[str, str], bytes]:
        headers = {"Cache-Control": "no-store", **self.headers}
        if self.body is None:
            return self.status, headers, b""
        if isinstance(self.body, str):  # Pre-rendered text; the handler sets Content-Type.
            return self.status, headers, self.body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
        return self.status, headers, json.dumps(self.body, default=str).encode("utf-8")


def error_response(status: int, code: str, message: str, details: list[dict] | None = None) -> Response:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details  # e.g. [{"path": "general.fields.2.id", "message": "..."}]
    return Response(status, {"error": error})


def encode_cursor(key: dict | None) -> str | None:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(key, default=str).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, json.JSONDecodeError):
        raise ApiError(400, "invalid_request", "'cursor' is malformed") from None
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ApiError(400, "invalid_request", "'cursor' is malformed")
    return data


def parse_limit(query: dict[str, str], default: int = 20, maximum: int = 100) -> int:
    try:
        limit = int(query.get("limit", default))
    except ValueError:
        raise ApiError(400, "invalid_request", "'limit' must be an integer") from None
    if not 1 <= limit <= maximum:
        raise ApiError(400, "invalid_request", f"'limit' must be 1-{maximum}")
    return limit
