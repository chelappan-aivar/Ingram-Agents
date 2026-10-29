"""Helpers shared by the API Lambda (handler.py) and the worker Lambda (worker.py).

Single-table design (DynamoDB, pk/sk, TTL attribute `ttl`):
    RUN#<id> / META                  run: title, input, status, stage, progress, latest version (owner = "shared")
    RUN#<id> / V#<nnn>               one ADR version: counts, tokens, diagram report, usage counts, file sizes
    RUN#<id> / LOG#<ts>#<rand>       one activity line
    DAY#<yyyy-mm-dd> / COUNT         runs + revisions started that day (cost guard)
Large outputs live in S3 (DATA_BUCKET): runs/<id>/srs.txt, runs/<id>/input/<file>, runs/<id>/v<nnn>/<file>.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

SHARED_OWNER = "shared"          # one history for everyone who has the access key
RUN_FILES = {                    # name -> content type; everything a version can hold in S3
    "adr.md": "text/markdown",
    "spec.json": "application/json",
    "design.json": "application/json",
    "usage.json": "application/json",
    "usage_assumptions.md": "text/markdown",
    "diagram.png": "image/png",
    "diagram.svg": "image/svg+xml",
    "diagram.drawio": "application/xml",
    "report.json": "application/json",
}
TEXT_TYPES = {"text/markdown", "application/json", "application/xml"}

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
        _s3 = boto3.client("s3")
    return _s3


def bucket() -> str:
    return os.environ["DATA_BUCKET"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def vkey(version: int) -> str:
    return f"v{int(version):03d}"


def dynamo_safe(value: Any) -> Any:
    """JSON round trip: floats -> Decimal (DynamoDB has no float type), anything odd -> str."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def update(key: dict[str, str], **fields: Any) -> None:
    fields = {**fields, "updated_at": now()}
    table().update_item(
        Key=key,
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": dynamo_safe(v) for k, v in fields.items()},
    )


def meta_key(run_id: str) -> dict[str, str]:
    return {"pk": f"RUN#{run_id}", "sk": "META"}


def version_key(run_id: str, version: int) -> dict[str, str]:
    return {"pk": f"RUN#{run_id}", "sk": f"V#{int(version):03d}"}


def log(run_id: str, stage: str, message: str, level: str = "info", version: int | None = None) -> None:
    at = now()
    table().put_item(Item=dynamo_safe({
        "pk": f"RUN#{run_id}", "sk": f"LOG#{at}#{secrets.token_hex(3)}", "at": at,
        "stage": stage, "message": message[:1000], "level": level, "version": version,
    }))
    print(json.dumps({"run_id": run_id, "stage": stage, "level": level, "message": message[:1000]}), flush=True)


# ---------------------------------------------------------------- S3 objects
def run_prefix(run_id: str) -> str:
    return f"runs/{run_id}"


def put_object(key: str, body: bytes | str, content_type: str) -> int:
    data = body.encode("utf-8") if isinstance(body, str) else body
    s3().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=content_type)
    return len(data)


def get_object(key: str) -> bytes:
    return s3().get_object(Bucket=bucket(), Key=key)["Body"].read()
