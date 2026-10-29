"""DynamoDB / S3 helpers for the API Lambda (handler.py).

Single-table design (DynamoDB, pk/sk, TTL attribute `ttl`):
    JOB#<id> / META                  one job: kind (generation | edit | check), status, stage, progress, result
                                     (generation META items carry owner = "shared" -> history GSI owner-created)
    JOB#<id> / LOG#<ts>#<rand>       one activity line (written by the API and by the runtime)
    DAY#<yyyy-mm-dd> / COUNT         generations + edits + AI reviews started that day (cost guard)
    IP#<sha256>#<yyyy-mm-ddThh> / COUNT   the same, per caller IP and hour (cost guard)
Files live in S3 (DATA_BUCKET) under sow/: inputs and outputs per draft (sow/<opportunity>/<draft id>/...)
and large job results (sow/jobs/<job id>/<name>.json). The runtime writes them; the API reads and presigns.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

SHARED_OWNER = "shared"          # open API: one history for everyone

_table = None
_s3 = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])
    return _table


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _s3


def bucket() -> str:
    return os.environ["DATA_BUCKET"]


def data_prefix() -> str:
    return os.environ.get("DATA_PREFIX", "sow").strip("/")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def dynamo_safe(value: Any) -> Any:
    """JSON round trip: floats -> Decimal (DynamoDB has no float type), anything odd -> str."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def job_key(job_id: str) -> dict[str, str]:
    return {"pk": f"JOB#{job_id}", "sk": "META"}


def update(key: dict[str, str], **fields: Any) -> None:
    fields = {**fields, "updated_at": now()}
    table().update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": dynamo_safe(v) for k, v in fields.items()},
    )


def log(job_id: str, stage: str, message: str, level: str = "info", ttl: int | None = None) -> None:
    at = now()
    item = {"pk": f"JOB#{job_id}", "sk": f"LOG#{at}#{secrets.token_hex(3)}", "at": at,
            "stage": stage, "message": message[:1000], "level": level}
    if ttl:
        item["ttl"] = ttl
    table().put_item(Item=dynamo_safe(item))
    print(json.dumps({"job_id": job_id, "stage": stage, "level": level, "message": message[:1000]}), flush=True)


def get_object(key: str) -> bytes:
    return s3().get_object(Bucket=bucket(), Key=key)["Body"].read()


def presign_download(key: str, filename: str, expires_in: int) -> str:
    safe = filename.replace('"', "")
    return s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket(), "Key": key, "ResponseContentDisposition": f'attachment; filename="{safe}"'},
        ExpiresIn=expires_in,
    )
