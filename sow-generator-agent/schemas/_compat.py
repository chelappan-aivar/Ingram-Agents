"""Shared request-body compatibility helpers."""

from typing import Annotated, Any

from pydantic import AfterValidator


def accept_legacy_client_name(data: Any) -> Any:
    """Map the deprecated `client_name` key onto `customer_name` when only the old one is sent."""
    if isinstance(data, dict) and "customer_name" not in data and "client_name" in data:
        return {**data, "customer_name": data["client_name"]}
    return data


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]
