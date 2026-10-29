"""One small storage interface over a local directory (development, tests) and S3 (AgentCore Runtime)."""
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

from .. import settings


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def list_keys(self, prefix: str) -> list[str]: ...
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def list_keys(self, prefix: str) -> list[str]:
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3Storage:
    """Objects are written with SSE-KMS (the bucket's default key unless `kms_key_id` is given)."""

    def __init__(self, bucket: str, client=None, kms_key_id: str | None = None):
        self.bucket, self.kms_key_id = bucket, kms_key_id
        self.client = client or settings.aws_session().client("s3")

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", []) if not o["Key"].endswith("/")]
        return sorted(keys)

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def write_bytes(self, key: str, data: bytes) -> None:
        extra = {"ServerSideEncryption": "aws:kms"}
        if self.kms_key_id:
            extra["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, **extra)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except ClientError as e:
            # A missing key is a 404 only when the caller may list the prefix; the runtime role's s3:ListBucket is
            # limited by an s3:prefix condition, so a 403 on HEAD is treated as "missing" too.
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound", "403", "AccessDenied"):
                return False
            raise


def default_storage() -> Storage:
    """S3 in the runtime (or when USE_S3_LOCALLY is set); the local folder on a development machine."""
    on_dev_machine = settings.has_local_profile()
    if settings.S3_BUCKET and (settings.USE_S3_ON_DEV_MACHINE or not on_dev_machine):
        return S3Storage(settings.S3_BUCKET, kms_key_id=settings.S3_KMS_KEY_ID)
    if not on_dev_machine:  # local files would vanish with the container
        raise RuntimeError("DATA_BUCKET is not set (template.yaml sets it on the runtime)")
    return LocalStorage(settings.LOCAL_ROOT)
