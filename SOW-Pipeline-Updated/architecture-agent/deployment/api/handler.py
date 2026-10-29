"""ingram-architecture-api Lambda behind API Gateway (HTTP API): the web UI's API.

Open API: no sign-in, everyone shares one run history. Cost guards instead of accounts: API Gateway throttling,
RUNS_PER_DAY across all callers and RUNS_PER_IP_HOUR per caller IP (runs + revisions; each is 2 Bedrock calls).

    GET  /session                      quota -> {"ok", "runs_today", "runs_per_day"}
    POST /runs                         {"title"?, "srs_text"? | "file": {"name", "content_base64"}} -> 202 {"run_id"}
    GET  /runs                         run history, newest first
    GET  /runs/{id}                    run, its versions (ADR / diagram / usage summaries) and the activity log
    POST /runs/{id}/revise             {"instruction"} -> 202 {"run_id", "version"}: a new ADR version
    GET  /runs/{id}/files/{name}[?version=n]   one output file (adr.md, diagram.png, usage.json, ...)

POST /runs and /revise store the input and start the worker Lambda asynchronously (the agent needs 3-5 minutes;
API Gateway waits 30 s at most). The UI polls GET /runs/{id}.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import PurePath
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import (RUN_FILES, SHARED_OWNER, TEXT_TYPES, get_object, log, meta_key, now, put_object, run_prefix,
                    table, update, version_key, vkey)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_TEXT_CHARS = 200_000           # pasted SRS text
MAX_FILE_BYTES = 4 * 1024 * 1024   # uploaded SRS file (Lambda's 6 MB request limit, after base64)
MAX_TITLE = 200
MAX_INSTRUCTION = 4_000
MAX_VERSIONS = 10
MAX_LOGS_RETURNED = 200
MAX_HISTORY = 50
MAX_INLINE_FILE = 4_500_000        # bytes returned through the API (Lambda's 6 MB response limit, after base64)
STALE_AFTER = timedelta(minutes=17)  # worker timeout is 15 min: a run "running" longer than this has died
ALLOWED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
FILE_RE = re.compile(r"[^\w.() -]")
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "40"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "6"))
WEB_USER = "Web user"
DONE = {"completed", "partial", "failed"}

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


def _qs(event: dict[str, Any], name: str) -> str:
    return str((event.get("queryStringParameters") or {}).get(name) or "")


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
               f"You can start up to {RUNS_PER_IP_HOUR} runs an hour. Try again later.")
    _take_slot(_day_key(), RUNS_PER_DAY, 3 * 86400,
               f"The daily limit of {RUNS_PER_DAY} runs is reached. Try again tomorrow.")


def _start_worker(run_id: str, version: int, mode: str, **extra: Any) -> None:
    _lambda.invoke(FunctionName=os.environ["WORKER_FUNCTION"], InvocationType="Event",
                   Payload=json.dumps({"run_id": run_id, "version": version, "mode": mode, **extra}).encode())


# ---------------------------------------------------------------- routes
def get_session(event: dict[str, Any]) -> dict[str, Any]:
    return _response(200, {"ok": True, "runs_today": _runs_today(), "runs_per_day": RUNS_PER_DAY,
                           "runs_per_ip_hour": RUNS_PER_IP_HOUR})


def _read_input(body: dict[str, Any]) -> tuple[str, str, bytes]:
    """-> (input_kind, file name, bytes). Pasted text wins over nothing; a file wins over pasted text."""
    file = body.get("file")
    if isinstance(file, dict) and file.get("content_base64"):
        name = FILE_RE.sub("_", PurePath(str(file.get("name") or "")).name).strip()[:120] or "srs.md"
        if PurePath(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ApiError(400, "Upload a .md, .txt, .docx or .pdf file.")
        try:
            data = base64.b64decode(str(file["content_base64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiError(400, "The file could not be read.") from exc
        if not data:
            raise ApiError(400, "The file is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise ApiError(400, f"Files up to {MAX_FILE_BYTES // (1024 * 1024)} MB.")
        return "file", name, data
    text = body.get("srs_text")
    if not isinstance(text, str) or len(text.strip()) < 200:
        raise ApiError(400, "Paste the requirements (at least 200 characters) or upload a file.")
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(400, f"Paste up to {MAX_TEXT_CHARS:,} characters, or upload the file instead.")
    return "text", "srs.md", text.encode("utf-8")


def start_run(event: dict[str, Any]) -> dict[str, Any]:
    caller = _caller(event)
    body = _body(event)
    kind, name, data = _read_input(body)
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:MAX_TITLE]
    _take_run_slots(caller)
    run_id, at = uuid.uuid4().hex, now()
    prefix = run_prefix(run_id)
    if kind == "file":
        put_object(f"{prefix}/input/{name}", data, "application/octet-stream")
    else:
        put_object(f"{prefix}/srs.txt", data, "text/plain; charset=utf-8")
    table().put_item(Item={
        **meta_key(run_id), "run_id": run_id, "owner": SHARED_OWNER, "created_at": at, "updated_at": at,
        "user_name": caller["user_name"], "title": title, "srs_filename": name, "input_kind": kind,
        "input_bytes": len(data), "input_chars": len(data) if kind == "text" else None,
        "status": "queued", "stage": "queued", "progress": 0, "version": 1,
    })
    table().put_item(Item={**version_key(run_id, 1), "version": 1, "kind": "new", "status": "queued",
                           "created_at": at, "user_name": caller["user_name"]})
    log(run_id, "queued", f"{caller['user_name']} started a run from {name if kind == 'file' else 'pasted text'}",
        version=1)
    _start_worker(run_id, 1, "new")
    return _response(202, {"run_id": run_id, "version": 1})


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
        updated = datetime.strptime(str(meta.get("updated_at")), "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated > STALE_AFTER:
            message = "The run stopped without finishing (time limit). Start it again."
            update(meta_key(meta["run_id"]), status="failed", stage="timeout", progress=100, error=message)
            update(version_key(meta["run_id"], int(meta.get("version") or 1)), status="failed", error=message)
            log(meta["run_id"], "timeout", message, level="error", version=int(meta.get("version") or 1))
            meta = {**meta, "status": "failed", "stage": "timeout", "error": message}
    return meta


def list_runs(event: dict[str, Any]) -> dict[str, Any]:
    page = table().query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(SHARED_OWNER),
                         ScanIndexForward=False, Limit=MAX_HISTORY)
    runs = [{k: item.get(k) for k in ("run_id", "title", "status", "stage", "created_at", "finished_at", "version",
                                      "user_name", "srs_filename", "input_kind")} for item in page.get("Items", [])]
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
    versions = sorted((i for i in items if i["sk"].startswith("V#")), key=lambda i: int(i["version"]))
    logs = [i for i in items if i["sk"].startswith("LOG#")][-MAX_LOGS_RETURNED:]
    for item in versions + logs:
        item.pop("pk", None)
        item.pop("sk", None)
    run = {k: v for k, v in meta.items() if k not in ("pk", "sk", "owner")}
    return _response(200, {"run": _plain(run), "versions": _plain(versions), "log": _plain(logs)})


def revise(event: dict[str, Any]) -> dict[str, Any]:
    caller = _caller(event)
    meta = _expire_if_stale(_meta(_path(event, "id")))
    run_id = meta["run_id"]
    instruction = str(_body(event).get("instruction") or "").strip()
    if len(instruction) < 5:
        raise ApiError(400, "Say what to change in the design.")
    if len(instruction) > MAX_INSTRUCTION:
        raise ApiError(400, f"Keep the instruction under {MAX_INSTRUCTION:,} characters.")
    if meta.get("status") not in DONE:
        raise ApiError(409, "This run is still working. Wait until it finishes.")
    base = int(meta.get("latest_complete_version") or 0)
    if not base:
        raise ApiError(409, "There is no design to revise yet. Start a new run.")
    version = int(meta.get("version") or 1) + 1
    if version > MAX_VERSIONS:
        raise ApiError(409, f"A run can have up to {MAX_VERSIONS} versions. Start a new run.")
    _take_run_slots(caller)
    at = now()
    table().put_item(Item={**version_key(run_id, version), "version": version, "kind": "revise", "status": "queued",
                           "created_at": at, "user_name": caller["user_name"], "instruction": instruction,
                           "base_version": base})
    update(meta_key(run_id), version=version, status="queued", stage="queued", progress=0, error=None,
           finished_at=None)
    log(run_id, "queued", f"{caller['user_name']} asked for version {version} (from version {base})", version=version)
    _start_worker(run_id, version, "revise", instruction=instruction, base_version=base)
    return _response(202, {"run_id": run_id, "version": version})


def get_file(event: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(_path(event, "id"))
    name = _path(event, "name")
    if name == "srs.txt":
        key, content_type = f"{run_prefix(meta['run_id'])}/srs.txt", "text/plain"
    elif name in RUN_FILES:
        asked = _qs(event, "version")
        version = int(asked) if asked.isdigit() else int(meta.get("latest_complete_version") or meta.get("version") or 1)
        if not 1 <= version <= int(meta.get("version") or 1):
            raise ApiError(404, "Version not found.")
        key, content_type = f"{run_prefix(meta['run_id'])}/{vkey(version)}/{name}", RUN_FILES[name]
    else:
        raise ApiError(404, "File not found.")
    try:
        data = get_object(key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise ApiError(404, "File not found.") from exc
        raise
    if len(data) > MAX_INLINE_FILE:
        raise ApiError(413, "The file is too large to send through the API.")
    if content_type in TEXT_TYPES or content_type == "text/plain":
        return _response(200, {"name": name, "content_type": content_type, "encoding": "utf-8",
                               "data": data.decode("utf-8", errors="replace")})
    return _response(200, {"name": name, "content_type": content_type, "encoding": "base64",
                           "data": base64.b64encode(data).decode("ascii")})


ROUTES = {
    "GET /session": get_session,
    "POST /runs": start_run,
    "GET /runs": list_runs,
    "GET /runs/{id}": get_run,
    "POST /runs/{id}/revise": revise,
    "GET /runs/{id}/files/{name}": get_file,
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
