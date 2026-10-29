"""Helpers shared by the API Lambda (handler.py) and the worker Lambda (worker.py).

Single-table design (DynamoDB, pk/sk, TTL attribute `ttl`):
    RUN#<id> / META                  run: title, requirements, status, stage, calculator_url, summary (owner = "shared")
    RUN#<id> / RESULT                full estimate JSON, requested-services checklist, coverage check
    RUN#<id> / LOG#<ts>#<rand>       one activity line
    DAY#<yyyy-mm-dd> / COUNT         runs started that day (cost guard, TTL)
    IP#<sha256>#<yyyy-mm-ddThh> / COUNT   runs one caller IP started that hour (cost guard, TTL)
No S3: the estimate JSON is small; anything above RESULT_LIMIT is stored trimmed (see worker.py).
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

SHARED_OWNER = "shared"          # open API: one history for everyone
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])
    return _table


def now() -> str:
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


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


def result_key(run_id: str) -> dict[str, str]:
    return {"pk": f"RUN#{run_id}", "sk": "RESULT"}


def log(run_id: str, stage: str, message: str, level: str = "info") -> None:
    at = now()
    table().put_item(Item=dynamo_safe({
        "pk": f"RUN#{run_id}", "sk": f"LOG#{at}#{secrets.token_hex(3)}", "at": at,
        "stage": stage, "message": message[:1000], "level": level,
    }))
    print(json.dumps({"run_id": run_id, "stage": stage, "level": level, "message": message[:1000]}), flush=True)
