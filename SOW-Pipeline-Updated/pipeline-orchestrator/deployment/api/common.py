"""Helpers shared by the API Lambda (handler.py) and the worker Lambda (worker.py).

Single-table design (DynamoDB, pk/sk, TTL attribute `ttl`):
    RUN#<id> / META                 run: title, status, stage, progress, per-stage status (owner = "shared")
    RUN#<id> / STATE                the PipelineState: inputs, scope, and each stage's output
    RUN#<id> / LOG#<ts>#<rand>      one activity line
    DAY#<yyyy-mm-dd> / COUNT        runs started that day (cost guard, TTL)
    IP#<sha256>#<yyyy-mm-ddThh> / COUNT   runs one caller IP started that hour (cost guard, TTL)

A full run is five agents' worth of output, so the STATE item offloads anything large (the SOW
markdown, the architecture reply with its diagram PNG) to S3 and keeps a pointer. DynamoDB items
max out at 400 KB and a diagram alone can pass that.
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
INLINE_LIMIT = 120_000           # bytes of JSON kept on the item; anything bigger goes to S3

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
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


def dynamo_safe(value: Any) -> Any:
    """JSON round trip: floats -> Decimal (DynamoDB has no float type), anything odd -> str."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def plain(value: Any) -> Any:
    """The inverse: Decimal -> int/float, for JSON responses."""
    def default(v: Any) -> Any:
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        if isinstance(v, set):
            return sorted(v)
        raise TypeError(f"not serialisable: {type(v).__name__}")

    return json.loads(json.dumps(value, default=default))


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


def state_key(run_id: str) -> dict[str, str]:
    return {"pk": f"RUN#{run_id}", "sk": "STATE"}


def log(run_id: str, stage: str, message: str, level: str = "info") -> None:
    at = now()
    table().put_item(Item=dynamo_safe({
        "pk": f"RUN#{run_id}", "sk": f"LOG#{at}#{secrets.token_hex(3)}", "at": at,
        "stage": stage, "message": str(message)[:1000], "level": level,
    }))
    print(json.dumps({"run_id": run_id, "stage": stage, "level": level,
                      "message": str(message)[:1000]}), flush=True)


# ---------------------------------------------------------------- large payloads
def data_prefix(run_id: str) -> str:
    return f"runs/{run_id}"


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))


def put_blob(run_id: str, name: str, value: Any) -> str:
    key = f"{data_prefix(run_id)}/{name}.json"
    s3().put_object(Bucket=bucket(), Key=key,
                    Body=json.dumps(value, default=str).encode("utf-8"),
                    ContentType="application/json", ServerSideEncryption="AES256")
    return key


def get_blob(key: str) -> Any:
    return json.loads(s3().get_object(Bucket=bucket(), Key=key)["Body"].read())


def offload(run_id: str, name: str, value: Any) -> dict[str, Any]:
    """`value` inline when small, else `{"s3_key": ...}`. Keeps the STATE item under the limit."""
    if value in (None, "", {}, []) or _size(value) <= INLINE_LIMIT:
        return {"inline": value}
    return {"s3_key": put_blob(run_id, name, value)}


def reload(holder: dict[str, Any] | None) -> Any:
    if not isinstance(holder, dict):
        return None
    if "s3_key" in holder:
        return get_blob(str(holder["s3_key"]))
    return plain(holder.get("inline"))
