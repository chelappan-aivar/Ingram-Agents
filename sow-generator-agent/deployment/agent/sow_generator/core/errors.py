"""Application errors with an HTTP-style status, a stable code and a message that is safe to show."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "The request could not be completed."

    def __init__(self, message: str | None = None, *, headers: dict[str, str] | None = None, **_: Any) -> None:
        self.message = message or self.default_message
        self.headers = headers or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "status_code": self.status_code}


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    default_message = "The request is not valid."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Not found."


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many requests. Try again later."


class BadGatewayError(AppError):
    status_code = 502
    code = "bad_gateway"
    default_message = "An upstream service failed."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "The service is not available."
