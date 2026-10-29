"""A small async S3 wrapper: blocking boto3 calls run in a worker thread."""

from __future__ import annotations

import asyncio
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from sow_generator.core import session as aws_session


class StorageError(RuntimeError):
    """An S3 operation failed or storage is not configured."""


class S3Client:
    def __init__(self, *, bucket: str, region: str | None = None, profile: str | None = None,
                 client: Any = None) -> None:
        self.bucket = bucket
        self._client = client or aws_session.build_boto_session(region=region, profile=profile).client(
            "s3", config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}))

    def put_object_sync(self, key: str, body: bytes, content_type: str) -> None:
        try:
            self._client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"put_object failed for {key}") from exc

    async def put_object(self, key: str, body: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.put_object_sync, key, body, content_type)

    def presign_download(self, key: str, *, filename: str, expires_in: int) -> str:
        safe = filename.replace('"', "")
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key,
                        "ResponseContentDisposition": f'attachment; filename="{safe}"'},
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"presign failed for {key}") from exc
