"""ingram-cost-api Lambda behind API Gateway (HTTP API): the web UI's API.

Open API: no sign-in, everyone shares one run history. Cost guards instead of accounts: API Gateway throttling,
RUNS_PER_DAY across all callers and RUNS_PER_IP_HOUR per caller IP (each run is several Bedrock calls).

    GET  /session      quota -> {"ok", "runs_today", "runs_per_day", "runs_per_ip_hour"}
    POST /runs         {"requirements": str (30..20,000 chars), "title"?} -> 202 {"run_id"}
    GET  /runs         run history, newest first -> {"runs": [...]}
    GET  /runs/{id}    -> {"run", "result" (estimate, checklist, coverage) | null, "log"}

POST /runs stores the request and starts the worker Lambda asynchronously (an estimate takes a few minutes;
API Gateway waits 30 s at most). The UI polls GET /runs/{id}.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import SHARED_OWNER, TIME_FORMAT, log, meta_key, now, result_key, table, update

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MIN_CHARS, MAX_CHARS = 30, 20_000
MAX_TITLE = 200
MAX_LOGS_RETURNED = 200
MAX_HISTORY = 50
STALE_AFTER = timedelta(minutes=17)  # worker timeout is 15 min: a run "running" longer than this has died
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "40"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "6"))
WEB_USER = "Web user"
HISTORY_FIELDS = ("run_id", "title", "status", "stage", "created_at", "finished_at", "user_name",
                  "estimate_name", "services_count")

_lambda = boto3.client("lambda")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=_json_default)}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _path(event: dict[str, Any], name: str) -> str:
    return str((event.get("pathParameters") or {}).get(name) or "")


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


# ---------------------------------------------------------------- caller (no sign-in)
def _caller(event: dict[str, Any]) -> dict[str, str]:
    """No accounts: every caller is the same web user. The source IP is used only for the hourly cost guard and
    stored only as a SHA-256 hash."""
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp") or "unknown")
    return {"user_name": WEB_USER, "ip_hash": hashlib.sha256(ip.encode()).hexdigest()[:32]}


# ---------------------------------------------------------------- cost guard
def _day_key() -> dict[str, str]:
    return {"pk": f"DAY#{datetime.now(timezone.utc):%Y-%m-%d}", "sk": "COUNT"}


def _runs_today() -> int:
    item = table().get_item(Key=_day_key()).get("Item") or {}
    return int(item.get("n") or 0)


def _take_slot(key: dict[str, str], cap: int, ttl_seconds: int, message: str) -> None:
    try:
        table().update_item(
            Key=key, UpdateExpression="ADD n :one SET #ttl = :ttl",
            ConditionExpression="attribute_not_exists(n) OR n < :cap",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":one": 1, ":cap": cap, ":ttl": int(time.time()) + ttl_seconds})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(429, message) from exc
        raise


def _take_run_slots(caller: dict[str, str]) -> None:
    """Per-IP hourly cap first (so one caller cannot use up the day), then the daily cap for everyone."""
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    _take_slot({"pk": f"IP#{caller['ip_hash']}#{hour}", "sk": "COUNT"}, RUNS_PER_IP_HOUR, 2 * 3600,
               f"You can start up to {RUNS_PER_IP_HOUR} estimates an hour. Try again later.")
    _take_slot(_day_key(), RUNS_PER_DAY, 3 * 86400,
               f"The daily limit of {RUNS_PER_DAY} estimates is reached. Try again tomorrow.")


def _start_worker(run_id: str) -> None:
    _lambda.invoke(FunctionName=os.environ["WORKER_FUNCTION"], InvocationType="Event",
                   Payload=json.dumps({"run_id": run_id}).encode())


# ---------------------------------------------------------------- routes
def get_session(event: dict[str, Any]) -> dict[str, Any]:
    return _response(200, {"ok": True, "runs_today": _runs_today(), "runs_per_day": RUNS_PER_DAY,
                           "runs_per_ip_hour": RUNS_PER_IP_HOUR})


def start_run(event: dict[str, Any]) -> dict[str, Any]:
    caller = _caller(event)
    body = _body(event)
    text = body.get("requirements")
    if not isinstance(text, str) or len(text.strip()) < MIN_CHARS:
        raise ApiError(400, f"Describe the infrastructure to price (at least {MIN_CHARS} characters).")
    text = text.strip()
    if len(text) > MAX_CHARS:
        raise ApiError(400, f"Keep the description under {MAX_CHARS:,} characters.")
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:MAX_TITLE]
    _take_run_slots(caller)
    run_id, at = uuid.uuid4().hex, now()
    table().put_item(Item={
        **meta_key(run_id), "run_id": run_id, "owner": SHARED_OWNER, "created_at": at, "updated_at": at,
        "user_name": caller["user_name"], "title": title, "requirements": text, "input_chars": len(text),
        "status": "queued", "stage": "queued",
    })
    log(run_id, "queued", f"{caller['user_name']} started an estimate ({len(text):,} characters)")
    _start_worker(run_id)
    return _response(202, {"run_id": run_id})


def _meta(run_id: str) -> dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ApiError(404, "Run not found.")
    meta = table().get_item(Key=meta_key(run_id)).get("Item")
    if not meta or meta.get("owner") != SHARED_OWNER:
        raise ApiError(404, "Run not found.")
    return meta


def _expire_if_stale(meta: dict[str, Any]) -> dict[str, Any]:
    """A worker that hit the Lambda timeout cannot write its own failure; mark the run failed when it is clearly dead."""
    if meta.get("status") in ("queued", "running"):
        updated = datetime.strptime(str(meta.get("updated_at")), TIME_FORMAT).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated > STALE_AFTER:
            message = "The estimate stopped without finishing (time limit). Start it again."
            update(meta_key(meta["run_id"]), status="failed", stage="timeout", error=message, finished_at=now())
            log(meta["run_id"], "timeout", message, level="error")
            meta = {**meta, "status": "failed", "stage": "timeout", "error": message}
    return meta


def list_runs(event: dict[str, Any]) -> dict[str, Any]:
    page = table().query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(SHARED_OWNER),
                         ScanIndexForward=False, Limit=MAX_HISTORY)
    runs = [{k: item.get(k) for k in HISTORY_FIELDS} for item in page.get("Items", [])]
    return _response(200, {"runs": _plain(runs)})


def get_run(event: dict[str, Any]) -> dict[str, Any]:
    meta = _expire_if_stale(_meta(_path(event, "id")))
    run_id = meta["run_id"]
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"RUN#{run_id}")}
    while True:
        page = table().query(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    result = next((i for i in items if i["sk"] == result_key(run_id)["sk"]), None)
    logs = [i for i in items if i["sk"].startswith("LOG#")][-MAX_LOGS_RETURNED:]
    for item in logs + ([result] if result else []):
        item.pop("pk", None)
        item.pop("sk", None)
    run = {k: v for k, v in meta.items() if k not in ("pk", "sk", "owner")}
    return _response(200, {"run": _plain(run), "result": _plain(result), "log": _plain(logs)})


ROUTES = {
    "GET /session": get_session,
    "POST /runs": start_run,
    "GET /runs": list_runs,
    "GET /runs/{id}": get_run,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    route = ROUTES.get(event.get("routeKey", ""))
    if route is None:
        return _response(404, {"error": "not found"})
    try:
        return route(event)
    except ApiError as exc:
        return _response(exc.status, {"error": str(exc)})
    except Exception:
        logger.exception("unhandled error on %s", event.get("routeKey"))
        return _response(500, {"error": "internal error"})
