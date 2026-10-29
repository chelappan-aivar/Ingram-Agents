"""The one place boto3 sessions are created (local tests replace build_boto_session)."""

from __future__ import annotations

import boto3


def build_boto_session(*, region: str | None = None, profile: str | None = None) -> boto3.Session:
    kwargs: dict[str, str] = {}
    if region:
        kwargs["region_name"] = region
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)
