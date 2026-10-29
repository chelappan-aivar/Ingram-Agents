"""Lambda entry point for API Gateway HTTP APIs (payload format 2.0)."""

from __future__ import annotations

import base64
import logging
import os

from api.app import Api, Request, services_from_env

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

_api: Api | None = None


def handler(event: dict, context) -> dict:
    global _api
    if _api is None:
        _api = Api(services_from_env(dict(os.environ)))

    http = event.get("requestContext", {}).get("http", {})
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    request = Request(
        method=http.get("method", "GET").upper(),
        path=event.get("rawPath") or http.get("path") or "/",
        headers={k.lower(): v for k, v in (event.get("headers") or {}).items()},
        query=dict(event.get("queryStringParameters") or {}),
        body=raw,
    )

    status, headers, payload = _api.handle(request).encoded()
    return {
        "statusCode": status,
        "headers": headers,
        "body": payload.decode("utf-8"),
        "isBase64Encoded": False,
    }
