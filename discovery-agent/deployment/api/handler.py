"""ingram-discovery-api Lambda behind API Gateway (HTTP API): the web UI's API.

Open API: no sign-in, everyone shares one run history. Cost guards instead of accounts: API Gateway throttling,
RUNS_PER_DAY across all callers and RUNS_PER_IP_HOUR per caller IP (a run is several Sonnet/Opus calls over the whole
corpus). Document bytes never pass through the Lambda: the browser PUTs them straight to S3 with presigned URLs.

    GET  /session                                  quota -> {"ok", "runs_today", "runs_per_day", "runs_per_ip_hour"}
    POST /engagements/{name}/upload-urls           {"files": [{"name", "size"}]} -> presigned PUT URLs (input/<folder>/)
    POST /runs                                     {"engagement", "instructions"?, "clarify"?, "narrative"?}
                                                   -> 202 {"run_id", "engagement", "status_key", ...}
    GET  /runs                                     run history (DynamoDB), newest first
    GET  /runs/{id}?engagement=                    status.json of the run (steps, state, error)
    GET  /runs/{id}/results?engagement=            presigned GET URLs for the outputs (once succeeded)
    GET  /runs/{id}/overview?engagement=           header, what was done, stakeholders by side, call timeline
    GET  /runs/{id}/report?engagement=             the finished report as data (discovery.json + narrative + context)
    GET  /runs/{id}/questions?engagement=          clarification questions (while awaiting_clarification)
    POST /runs/{id}/answers                        {"engagement", "answers": {id: text}} -> 202, resumes the run
    GET  /pipeline/overview[?scope=latest]         every Intake opportunity with its Discovery output (scope=latest: newest Intake run only)
    GET  /pipeline/opportunities                   Intake opportunities and their Live Assist calls (api/pipeline.py)
    POST /pipeline/opportunities/{id}/import       their documents into input/<engagement>/ (key: Secrets Manager)

The agent (AgentCore Runtime) writes status.json and the outputs to S3; that file is the source of truth. The run
history and the cost-guard counters live in one of two stores, chosen by the environment:

  RUNS_TABLE set    DynamoDB (single table pk/sk, TTL `ttl`):
      RUN#<folder>/<run_id> / META     engagement, run_id, created_at, owner "shared", status (cached from status.json)
      DAY#<yyyy-mm-dd> / COUNT         runs started that day (expires after 3 days)
      IP#<sha256>#<yyyy-mm-ddThh> / COUNT   runs one caller IP started in that hour (expires after 2 hours)
  RUNS_TABLE empty  the data bucket itself, so no extra AWS resource is needed:
      history/<10^10 - epoch>_<run_id>.json   one small record per run; S3 lists keys ascending, so newest first
      guard/DAY/<date>/<n>, guard/IP/<sha256>/<hour>/<n>   one object per run slot, written with If-None-Match: *
          (S3 conditional writes make taking a slot atomic); a bucket lifecycle rule expires guard/ after 3 days
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

import overview
import pipeline

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""   # Lambda always sets AWS_REGION
RUNS_TABLE = os.environ.get("RUNS_TABLE", "")
DATA_BUCKET = os.environ.get("DATA_BUCKET", "")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
KMS_KEY_ID = os.environ.get("KMS_KEY_ID") or None      # empty: the bucket default key (aws/s3)
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "20"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "4"))
PUT_EXPIRY_SECONDS = int(os.environ.get("PUT_EXPIRY_SECONDS", "600"))   # 10 minutes
GET_EXPIRY_SECONDS = int(os.environ.get("GET_EXPIRY_SECONDS", "300"))   # 5 minutes

MAX_FILES = 200                         # per upload-urls call
MAX_FILE_BYTES = 25 * 1024 * 1024       # per document (signed into the PUT URL as Content-Length)
MAX_TOTAL_BYTES = 100 * 1024 * 1024     # per upload-urls call
MAX_BODY_BYTES = 256 * 1024             # the routes carry only small JSON
MAX_INSTRUCTIONS = 4000
MAX_ANSWERS_BYTES = 20000
MAX_HISTORY = 50
STALE_RUNNING = timedelta(hours=2)      # status.json is rewritten after every step; no update for 2 h = dead run
STALE_STARTING = timedelta(minutes=15)  # the runtime writes status.json within seconds of the start
SHARED_OWNER = "shared"
TERMINAL = {"succeeded", "failed"}

# Same rules the runtime uses (discovery_agent/runtime/state.py and tools/load_documents.py).
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$")           # engagement name / run id
DOC_SUFFIXES = {"", ".txt", ".md", ".eml", ".docx", ".pdf"}
OUTPUT_FILES = ["discovery.json", "README.md", "discovery_report.docx", "narrative.json"]
CONTEXT_FILE = "_context.json"          # input/<folder>/: written by the Live Assist import, not a document

_table = None
_s3_client = None
_agentcore_client = None
_store = None


# ---------------------------------------------------------------- AWS clients (lazy, replaceable in tests)
def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(RUNS_TABLE)
    return _table


def make_s3_client():
    """Regional virtual-host URLs (https://<bucket>.s3.<region>.amazonaws.com/...): the exact origin the UI's CSP
    connect-src and the bucket CORS rule allow for the browser's direct PUTs."""
    return boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                                      retries={"max_attempts": 3, "mode": "standard"}))


def s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = make_s3_client()
    return _s3_client


def make_agentcore_client():
    # The runtime answers start/resume in about a second; API Gateway gives up after 30 s.
    return boto3.client("bedrock-agentcore", region_name=REGION,
                        config=Config(read_timeout=25, connect_timeout=5, retries={"max_attempts": 2, "mode": "standard"}))


def agentcore():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = make_agentcore_client()
    return _agentcore_client


def bucket() -> str:
    return DATA_BUCKET


# ---------------------------------------------------------------- helpers
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
        raw = base64.b64decode(raw)
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > MAX_BODY_BYTES:
        raise ApiError(413, "Request body too large.")
    try:
        data = json.loads(raw.decode() or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "The request body must be JSON.") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "The request body must be a JSON object.")
    return data


def _path(event: dict[str, Any], name: str) -> str:
    return urllib.parse.unquote(str((event.get("pathParameters") or {}).get(name) or ""))


def _qs(event: dict[str, Any], name: str) -> str:
    return str((event.get("queryStringParameters") or {}).get(name) or "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at: datetime | None = None) -> str:
    return (at or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any) -> datetime | None:
    try:
        text = str(value).replace("Z", "+00:00")
        at = datetime.fromisoformat(text)
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _folder(engagement: str) -> str:
    return engagement.replace(" ", "_")  # matches RunConfig: input/<folder>/, output/<folder>/, work/<folder>/


def _engagement(value: Any) -> str:
    name = str(value or "").strip()
    if not SAFE_NAME.fullmatch(name):
        raise ApiError(400, "Engagement name: letters, digits, space, . _ - (up to 101 characters).")
    return name


def _run_id(value: str) -> str:
    if not SAFE_NAME.fullmatch(value or ""):
        raise ApiError(404, "Run not found.")
    return value


def _file_name(name: Any) -> str:
    base = str(name or "").strip()
    if not base or "/" in base or "\\" in base or ".." in base or base.startswith((".", "_")) or len(base) > 200:
        raise ApiError(400, f"Invalid file name: {base[:80]!r}.")
    suffix = ("." + base.rsplit(".", 1)[1].lower()) if "." in base else ""
    if suffix not in DOC_SUFFIXES:
        raise ApiError(400, f"Unsupported file type for {base[:80]!r} (allowed: .txt .md .eml .docx .pdf).")
    return base


def _new_run_id() -> str:
    return f"{_now():%Y-%m-%dT%H-%M-%SZ}-{secrets.token_hex(3)}"


def _session_id(engagement: str, run_id: str, phase: str) -> str:
    # deterministic per run and phase; 42 characters (AgentCore needs >= 33)
    return "discovery-" + uuid.uuid5(uuid.NAMESPACE_URL, f"{engagement}/{run_id}/{phase}").hex


def _meta_key(engagement: str, run_id: str) -> dict[str, str]:
    return {"pk": f"RUN#{_folder(engagement)}/{run_id}", "sk": "META"}


def _status_key(engagement: str, run_id: str) -> str:
    return f"output/{_folder(engagement)}/{run_id}/status.json"


def _work_dir(engagement: str, run_id: str) -> str:
    return f"work/{_folder(engagement)}/{run_id}/"


def _sse() -> dict[str, str]:
    extra = {"ServerSideEncryption": "aws:kms"}
    if KMS_KEY_ID:
        extra["SSEKMSKeyId"] = KMS_KEY_ID
    return extra


def _get_json(key: str) -> dict | None:
    """An S3 JSON object, or None when it does not exist (S3 answers 403 for a missing key outside s3:ListBucket)."""
    try:
        return json.loads(s3().get_object(Bucket=bucket(), Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound", "AccessDenied", "403"):
            return None
        raise


def _exists(key: str) -> bool:
    try:
        s3().head_object(Bucket=bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound", "AccessDenied", "403"):
            return False
        raise


def _invoke(payload: dict[str, Any], session_id: str) -> dict[str, Any]:
    try:
        resp = agentcore().invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN, runtimeSessionId=session_id, qualifier="DEFAULT",
            contentType="application/json", accept="application/json", payload=json.dumps(payload).encode())
        answer = json.loads(resp["response"].read().decode() or "{}")
    except (ClientError, ValueError) as exc:
        code = exc.response.get("Error", {}).get("Code") if isinstance(exc, ClientError) else type(exc).__name__
        logger.error("invoke_failed run_id=%s code=%s", payload.get("run_id"), code)
        raise ApiError(502, "The agent could not be started. Try again in a minute.") from exc
    if answer.get("status") not in ("accepted", "already_started"):
        logger.error("invoke_rejected run_id=%s status=%s", payload.get("run_id"), answer.get("status"))
        raise ApiError(502, "The agent did not accept the run.")
    return answer


# ---------------------------------------------------------------- caller (no sign-in)
def _ip_hash(event: dict[str, Any]) -> str:
    """The source IP is used only for the hourly cost guard and stored only as a SHA-256 hash."""
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp") or "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- run history + cost guards: two interchangeable stores
_DAY_LIMIT_MSG = "The daily limit of {n} runs is reached. Try again tomorrow."
_IP_LIMIT_MSG = "You can start up to {n} runs an hour. Try again later."


class DynamoStore:
    """DynamoDB (RUNS_TABLE): run META items, per-day and per-IP-hour counters."""

    @staticmethod
    def _day_key() -> dict[str, str]:
        return {"pk": f"DAY#{_now():%Y-%m-%d}", "sk": "COUNT"}

    def runs_today(self) -> int:
        item = table().get_item(Key=self._day_key()).get("Item") or {}
        return int(item.get("n") or 0)

    @staticmethod
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

    def take_run_slots(self, ip_hash: str) -> None:
        """Per-IP hourly cap first (so one caller cannot use up the day), then the daily cap for everyone."""
        self._take_slot({"pk": f"IP#{ip_hash}#{_now():%Y-%m-%dT%H}", "sk": "COUNT"}, RUNS_PER_IP_HOUR, 2 * 3600,
                        _IP_LIMIT_MSG.format(n=RUNS_PER_IP_HOUR))
        self._take_slot(self._day_key(), RUNS_PER_DAY, 3 * 86400, _DAY_LIMIT_MSG.format(n=RUNS_PER_DAY))

    def get_meta(self, engagement: str, run_id: str) -> dict[str, Any] | None:
        return table().get_item(Key=_meta_key(engagement, run_id)).get("Item")

    def put_meta(self, engagement: str, run_id: str, item: dict[str, Any]) -> None:
        table().put_item(Item={**_meta_key(engagement, run_id), **item})

    def cache_status(self, meta: dict[str, Any], doc: dict[str, Any]) -> None:
        state = doc.get("state")
        steps = len(doc.get("completed_steps") or [])
        if meta.get("status") == state and int(meta.get("steps_done") or 0) == steps:
            return
        fields: dict[str, Any] = {"status": state, "steps_done": steps, "updated_at": _iso()}
        if state in TERMINAL:
            fields["finished_at"] = doc.get("updated_at") or _iso()
            fields["error"] = (doc.get("error") or "")[:500] or None
        table().update_item(
            Key={"pk": meta["pk"], "sk": meta["sk"]},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": v for k, v in fields.items()})

    def list_runs(self) -> list[dict[str, Any]]:
        page = table().query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(SHARED_OWNER),
                             ScanIndexForward=False, Limit=MAX_HISTORY)
        return page.get("Items", [])


class S3Store:
    """The data bucket as the store, for deployments without a DynamoDB table.

    history/<10^10 - epoch>_<run_id>.json   S3 lists keys ascending, so the newest run comes first. The key is derived
                                            from the run id (it starts with the start time), so a run is found by id.
    guard/DAY/<date>/<n>                    one empty object per run started that day
    guard/IP/<sha256>/<hour>/<n>            one per run a caller IP started that hour
    A slot is taken with a conditional write (If-None-Match: *), which S3 applies atomically: two concurrent requests
    can never both take slot n. A bucket lifecycle rule expires guard/ after 3 days.
    """

    _RUN_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})Z")
    _TAKEN = ("PreconditionFailed", "ConditionalRequestConflict", "412", "409")

    @classmethod
    def _history_key(cls, run_id: str) -> str:
        m = cls._RUN_TIME.match(run_id)
        epoch = int(datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp()) if m else int(_now().timestamp())
        return f"history/{10**10 - epoch:010d}_{run_id}.json"

    def _count(self, prefix: str, limit: int) -> int:
        return int(s3().list_objects_v2(Bucket=bucket(), Prefix=prefix, MaxKeys=limit).get("KeyCount") or 0)

    def runs_today(self) -> int:
        return self._count(f"guard/DAY/{_now():%Y-%m-%d}/", RUNS_PER_DAY + 1)

    def _take(self, prefix: str, cap: int, message: str) -> None:
        for n in range(self._count(prefix, cap) + 1, cap + 1):
            try:
                s3().put_object(Bucket=bucket(), Key=f"{prefix}{n}", Body=b"", IfNoneMatch="*", **_sse())
                return
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in self._TAKEN:
                    raise
                # slot n was taken by a concurrent request: try the next one
        raise ApiError(429, message)

    def take_run_slots(self, ip_hash: str) -> None:
        self._take(f"guard/IP/{ip_hash}/{_now():%Y-%m-%dT%H}/", RUNS_PER_IP_HOUR, _IP_LIMIT_MSG.format(n=RUNS_PER_IP_HOUR))
        self._take(f"guard/DAY/{_now():%Y-%m-%d}/", RUNS_PER_DAY, _DAY_LIMIT_MSG.format(n=RUNS_PER_DAY))

    def get_meta(self, engagement: str, run_id: str) -> dict[str, Any] | None:
        record = _get_json(self._history_key(run_id))
        return record if record and record.get("engagement") == engagement else None

    def put_meta(self, engagement: str, run_id: str, item: dict[str, Any]) -> None:
        s3().put_object(Bucket=bucket(), Key=self._history_key(run_id), ContentType="application/json", **_sse(),
                        Body=json.dumps({"run_id": run_id, "engagement": engagement, **item}, default=_json_default).encode())

    def cache_status(self, meta: dict[str, Any], doc: dict[str, Any]) -> None:
        """Nothing to cache: the history reads each run's status.json directly."""

    def list_runs(self) -> list[dict[str, Any]]:
        keys = [o["Key"] for o in s3().list_objects_v2(Bucket=bucket(), Prefix="history/", MaxKeys=MAX_HISTORY)
                .get("Contents", [])]

        def load(key: str) -> dict[str, Any] | None:
            record = _get_json(key)
            if not record:
                return None
            status = _get_json(_status_key(record["engagement"], record["run_id"])) or {}
            state = status.get("state") or record.get("status") or "starting"
            if state == "running" and _stale(status.get("updated_at"), STALE_RUNNING):
                state = "failed"
            elif state == "starting" and _stale(record.get("created_at"), STALE_STARTING):
                state = "failed"
            return {**record, "status": state, "steps_done": len(status.get("completed_steps") or []),
                    "finished_at": status.get("updated_at") if state in TERMINAL else None}

        with ThreadPoolExecutor(max_workers=10) as pool:
            return [r for r in pool.map(load, keys) if r]


def _stale(stamp: Any, limit: timedelta) -> bool:
    at = _parse_time(stamp)
    return bool(at and _now() - at > limit)


def store() -> DynamoStore | S3Store:
    """DynamoDB when RUNS_TABLE is set, otherwise the data bucket."""
    global _store
    wanted = DynamoStore if RUNS_TABLE else S3Store
    if not isinstance(_store, wanted):
        _store = wanted()
    return _store


# ---------------------------------------------------------------- run state (status.json + cached META)
def _run_status(engagement: str, run_id: str) -> dict[str, Any]:
    """status.json, or a synthetic doc for a run the API started but the agent has not written yet; 404 otherwise."""
    meta = store().get_meta(engagement, run_id)
    doc = _get_json(_status_key(engagement, run_id))
    if doc is None:
        if not meta:
            raise ApiError(404, "Run not found.")
        created = _parse_time(meta.get("created_at"))
        if created and _now() - created > STALE_STARTING:
            doc = {"state": "failed", "error": "The agent never started this run. Start it again."}
        else:
            doc = {"state": "starting"}
        doc = {"run_id": run_id, "engagement": engagement, "completed_steps": [], "steps": {},
               "instructions": meta.get("instructions") or "", "output_keys": [], "updated_at": meta.get("created_at"),
               **doc}
    elif doc.get("state") == "running":
        updated = _parse_time(doc.get("updated_at"))
        if updated and _now() - updated > STALE_RUNNING:
            doc = {**doc, "state": "failed",
                   "error": "The run stopped without finishing (no progress for 2 hours). Start it again."}
    if meta:
        store().cache_status(meta, doc)
    return doc


# ---------------------------------------------------------------- routes
def get_session(event: dict[str, Any]) -> dict[str, Any]:
    return _response(200, {"ok": True, "runs_today": store().runs_today(), "runs_per_day": RUNS_PER_DAY,
                           "runs_per_ip_hour": RUNS_PER_IP_HOUR})


def upload_urls(event: dict[str, Any]) -> dict[str, Any]:
    """One presigned PUT URL per file, locked to input/<folder>/<file>, its size and the SSE-KMS headers."""
    engagement = _engagement(_path(event, "name"))
    files = _body(event).get("files")
    if not isinstance(files, list) or not files:
        raise ApiError(400, 'Send "files": [{"name": "01_call.txt", "size": 1234}, ...].')
    if len(files) > MAX_FILES:
        raise ApiError(400, f"Up to {MAX_FILES} files per upload.")
    wanted: list[tuple[str, int]] = []
    for f in files:
        if not isinstance(f, dict):
            raise ApiError(400, 'Every file needs "name" and "size" (bytes).')
        size = f.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ApiError(400, f"File {str(f.get('name'))[:80]!r} is empty or has no size.")
        if size > MAX_FILE_BYTES:
            raise ApiError(400, f"Files up to {MAX_FILE_BYTES // (1024 * 1024)} MB each.")
        wanted.append((_file_name(f.get("name")), size))
    names = [n for n, _ in wanted]
    if len(set(names)) != len(names):
        raise ApiError(400, "File names must be unique.")
    if sum(s for _, s in wanted) > MAX_TOTAL_BYTES:
        raise ApiError(400, f"Up to {MAX_TOTAL_BYTES // (1024 * 1024)} MB per upload.")
    prefix = f"input/{_folder(engagement)}/"
    urls = {name: s3().generate_presigned_url(
        "put_object", Params={"Bucket": bucket(), "Key": prefix + name, "ContentLength": size, **_sse()},
        ExpiresIn=PUT_EXPIRY_SECONDS) for name, size in wanted}
    headers = {"x-amz-server-side-encryption": "aws:kms"}
    if KMS_KEY_ID:
        headers["x-amz-server-side-encryption-aws-kms-key-id"] = KMS_KEY_ID
    logger.info("upload_urls engagement=%s files=%d", engagement, len(urls))
    return _response(200, {"engagement": engagement, "input_prefix": prefix, "expires_in": PUT_EXPIRY_SECONDS,
                           "method": "PUT", "required_headers": headers, "urls": urls})


def start_run(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    engagement = _engagement(body.get("engagement"))
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    instructions = str(body.get("instructions") or options.get("instructions") or "").strip()
    if len(instructions) > MAX_INSTRUCTIONS:
        raise ApiError(400, f"Keep the instructions under {MAX_INSTRUCTIONS:,} characters.")
    clarify = bool(body.get("clarify", options.get("clarify", False)))
    narrative = bool(body.get("narrative", options.get("narrative", True)))
    folder = _folder(engagement)
    listing = s3().list_objects_v2(Bucket=bucket(), Prefix=f"input/{folder}/", MaxKeys=1)
    if not listing.get("KeyCount") and not listing.get("Contents"):
        raise ApiError(400, "Upload the engagement's documents first.")
    store().take_run_slots(_ip_hash(event))
    run_id = _new_run_id()
    payload = {"action": "start", "engagement": engagement, "run_id": run_id,
               "input_prefix": f"input/{folder}/", "output_prefix": f"output/{folder}/",
               "options": {"narrative": narrative, "instructions": instructions, "clarify": clarify}}
    session_id = _session_id(engagement, run_id, "start")
    answer = _invoke(payload, session_id)
    pipeline_id = str(body.get("pipeline_opportunity_id") or "")
    linked = None
    if pipeline_id:   # started from the pipeline card: report back to the opportunity in Live Assist
        try:
            linked = pipeline.register_run(pipeline.server_key(), pipeline_id, engagement, run_id,
                                           [str(c) for c in body.get("pipeline_call_ids") or []][:100])
        except pipeline.PipelineError:
            linked = False
    at = _iso()
    store().put_meta(engagement, run_id, {
        "run_id": run_id, "engagement": engagement, "owner": SHARED_OWNER, "created_at": at, "updated_at": at,
        "status": "starting", "steps_done": 0, "clarify": clarify, "narrative": narrative,
        "instructions": instructions or None, **({"pipeline_opportunity_id": pipeline_id} if pipeline_id else {})})
    logger.info("run_started engagement=%s run_id=%s clarify=%s", engagement, run_id, clarify)
    return _response(202, {"run_id": run_id, "engagement": engagement, "session_id": session_id,
                           "status_key": _status_key(engagement, run_id), "runtime_answer": answer,
                           **({"linked_to_live_assist": linked} if pipeline_id else {})})


def list_runs(event: dict[str, Any]) -> dict[str, Any]:
    fields = ("run_id", "engagement", "status", "steps_done", "created_at", "finished_at", "clarify", "instructions",
              "pipeline_opportunity_id")
    runs = [{k: item.get(k) for k in fields} for item in store().list_runs()]
    return _response(200, {"runs": json.loads(json.dumps(runs, default=_json_default))})


def get_run(event: dict[str, Any]) -> dict[str, Any]:
    engagement = _engagement(_qs(event, "engagement"))
    return _response(200, _run_status(engagement, _run_id(_path(event, "id"))))


def get_results(event: dict[str, Any]) -> dict[str, Any]:
    engagement = _engagement(_qs(event, "engagement"))
    run_id = _run_id(_path(event, "id"))
    doc = _run_status(engagement, run_id)
    if doc.get("state") != "succeeded":
        raise ApiError(409, f"The run is '{doc.get('state')}'; results are available once it has succeeded.")
    output_dir = f"output/{_folder(engagement)}/{run_id}/"
    urls = {}
    for name in OUTPUT_FILES:
        key = output_dir + name
        if _exists(key):  # narrative.json is optional
            urls[name] = s3().generate_presigned_url(
                "get_object", Params={"Bucket": bucket(), "Key": key,
                                      "ResponseContentDisposition": f'attachment; filename="{name}"'},
                ExpiresIn=GET_EXPIRY_SECONDS)
    return _response(200, {"run_id": run_id, "engagement": engagement, "expires_in": GET_EXPIRY_SECONDS, "urls": urls})


def _context(engagement: str, run_id: str) -> dict[str, Any]:
    """The run's own copy of the context (written by the agent), else the one the Live Assist import left."""
    folder = _folder(engagement)
    raw = _get_json(f"output/{folder}/{run_id}/context.json") or _get_json(f"input/{folder}/{CONTEXT_FILE}")
    return overview.clean_context(raw)


def _finished_report(engagement: str, run_id: str, doc: dict[str, Any]) -> tuple[dict | None, dict | None]:
    """(discovery.json, narrative.json) once the run has succeeded, else (None, None)."""
    if doc.get("state") != "succeeded":
        return None, None
    output_dir = f"output/{_folder(engagement)}/{run_id}/"
    return _get_json(output_dir + "discovery.json"), _get_json(output_dir + "narrative.json")


def get_overview(event: dict[str, Any]) -> dict[str, Any]:
    """Header, what has been done, stakeholders by side and the call timeline. Works while the run is still going."""
    engagement = _engagement(_qs(event, "engagement"))
    run_id = _run_id(_path(event, "id"))
    doc = _run_status(engagement, run_id)
    report, _ = _finished_report(engagement, run_id, doc)
    return _response(200, {"run_id": run_id, "engagement": engagement,
                           **overview.build(_context(engagement, run_id), report, doc.get("state"))})


def get_report(event: dict[str, Any]) -> dict[str, Any]:
    """The finished report as data for the page: discovery.json, the narrative and the context from earlier agents."""
    engagement = _engagement(_qs(event, "engagement"))
    run_id = _run_id(_path(event, "id"))
    doc = _run_status(engagement, run_id)
    if doc.get("state") != "succeeded":
        raise ApiError(409, f"The run is '{doc.get('state')}'; the report is available once it has succeeded.")
    report, narrative = _finished_report(engagement, run_id, doc)
    if report is None:
        raise ApiError(404, "The report file is missing for this run.")
    return _response(200, {"run_id": run_id, "engagement": engagement, "report": report, "narrative": narrative,
                           "context": _context(engagement, run_id)})


def get_questions(event: dict[str, Any]) -> dict[str, Any]:
    engagement = _engagement(_qs(event, "engagement"))
    run_id = _run_id(_path(event, "id"))
    doc = _run_status(engagement, run_id)
    if doc.get("state") != "awaiting_clarification":
        raise ApiError(409, f"The run is '{doc.get('state')}'; questions exist only while it awaits clarification.")
    clar = _get_json(f"{_work_dir(engagement, run_id)}clarifications.json") or {}
    return _response(200, {"run_id": run_id, "engagement": engagement, "questions": clar.get("questions", [])})


def submit_answers(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    engagement = _engagement(body.get("engagement") or _qs(event, "engagement"))
    run_id = _run_id(_path(event, "id"))
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ApiError(400, 'Send "answers": {"<question id>": "<answer>"}.')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in answers.items()):
        raise ApiError(400, "Every answer id and value must be a string.")
    if len(json.dumps(answers)) > MAX_ANSWERS_BYTES:
        raise ApiError(413, "The answers are too long.")
    doc = _run_status(engagement, run_id)
    if doc.get("state") != "awaiting_clarification":
        raise ApiError(409, f"The run is '{doc.get('state')}'; it is not waiting for answers.")
    answers_key = f"{_work_dir(engagement, run_id)}answers.json"
    if _exists(answers_key):
        raise ApiError(409, "Answers for this run were already submitted.")
    s3().put_object(Bucket=bucket(), Key=answers_key, Body=json.dumps({"answers": answers}).encode(),
                    ContentType="application/json", **_sse())
    folder = _folder(engagement)
    payload = {"action": "resume_with_answers", "engagement": engagement, "run_id": run_id,
               "input_prefix": f"input/{folder}/", "output_prefix": f"output/{folder}/", "options": {}}
    _invoke(payload, _session_id(engagement, run_id, "resume"))  # a second session for phase 2
    logger.info("run_resumed engagement=%s run_id=%s answered=%d", engagement, run_id, len(answers))
    return _response(202, {"run_id": run_id, "engagement": engagement, "resumed": True,
                           "status_key": _status_key(engagement, run_id)})


def _latest_runs(runs: list[dict[str, Any]], opp: dict[str, Any]) -> dict[str, Any] | None:
    """The run that belongs to an opportunity: registered with its id, or named like its Live Assist engagement.
    A finished run wins over a newer one that is still going or failed; runs arrive newest first."""
    mine = [r for r in runs if r.get("pipeline_opportunity_id") == opp["intake_opportunity_id"]
            or (not r.get("pipeline_opportunity_id") and r.get("engagement") == opp["engagement"])]
    return next((r for r in mine if r.get("status") == "succeeded"), mine[0] if mine else None)


def _discovery_output(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    # status.json is the source of truth; the history entry can lag behind it.
    state = _run_status(run["engagement"], run["run_id"]).get("state")
    out: dict[str, Any] = {"run_id": run["run_id"], "engagement": run["engagement"], "state": state,
                           "started_at": run.get("created_at")}
    if state == "succeeded":
        base = f"output/{_folder(run['engagement'])}/{run['run_id']}/"
        report = _get_json(base + "discovery.json")
        if report:
            out["summary"] = overview.summarise_report(report, _get_json(base + "narrative.json"))
        out["context"] = overview.clean_context(_get_json(base + "context.json")).get("header") or {}
    return out


def pipeline_overview(event: dict[str, Any]) -> dict[str, Any]:
    """Everything in one go: each Intake opportunity (decision, summary) with the output of its Discovery run."""
    opportunities = pipeline.list_opportunities(pipeline.server_key())["opportunities"]
    runs = store().list_runs()
    pairs = [(o, _latest_runs(runs, o)) for o in opportunities]
    if _qs(event, "scope") == "latest":   # optional: only the latest Intake run (plus older ones that have Discovery work)
        pairs = pipeline.latest_run_only(pairs)
    matched = pipeline.dedupe(pairs)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(lambda pair: _discovery_output(pair[1]), matched))
    rows = [{"intake": o, "discovery": d} for (o, _), d in zip(matched, outputs)]
    return _response(200, {"fetched_at": _iso(), "count": len(rows), "repeats_hidden": len(opportunities) - len(rows),
                           "with_discovery": sum(1 for r in rows if r["discovery"]), "rows": rows})


def pipeline_opportunities(event: dict[str, Any]) -> dict[str, Any]:
    out = pipeline.list_opportunities(pipeline.server_key())
    return _response(200, {**out, "connected": pipeline.enabled()})


def pipeline_import(event: dict[str, Any]) -> dict[str, Any]:
    """Write the opportunity's Intake record and call transcripts/minutes into the engagement's input folder."""
    key = pipeline.server_key()
    data = pipeline.export(key, _path(event, "id"))
    engagement = _engagement(data.get("engagement"))
    if not data["documents"]:
        raise ApiError(409, "Live Assist has nothing for this opportunity yet.")
    folder = _folder(engagement)
    written = []
    for doc in data["documents"]:
        name = _file_name(doc["name"])
        body = doc["text"].encode()
        s3().put_object(Bucket=bucket(), Key=f"input/{folder}/{name}", Body=body,
                        ContentType="text/markdown" if name.endswith(".md") else "text/plain", **_sse())
        written.append({"name": name, "kind": doc.get("kind"), "bytes": len(body)})
    # The run's context (customer, seller, program, participants, call list). The leading "_" keeps the agent from
    # reading it as a document; the agent copies it into the run's outputs and the overview reads it from there.
    s3().put_object(Bucket=bucket(), Key=f"input/{folder}/{CONTEXT_FILE}", ContentType="application/json", **_sse(),
                    Body=json.dumps(data["context"]).encode())
    logger.info("pipeline_import engagement=%s documents=%d calls=%d", engagement, len(written), len(data["call_ids"]))
    return _response(200, {"engagement": engagement, "input_prefix": f"input/{folder}/", "documents": written,
                           "call_ids": data["call_ids"], "opportunity_id": _path(event, "id")})


ROUTES = {
    "GET /pipeline/overview": pipeline_overview,
    "GET /pipeline/opportunities": pipeline_opportunities,
    "POST /pipeline/opportunities/{id}/import": pipeline_import,
    "GET /session": get_session,
    "POST /engagements/{name}/upload-urls": upload_urls,
    "POST /runs": start_run,
    "GET /runs": list_runs,
    "GET /runs/{id}": get_run,
    "GET /runs/{id}/results": get_results,
    "GET /runs/{id}/overview": get_overview,
    "GET /runs/{id}/report": get_report,
    "GET /runs/{id}/questions": get_questions,
    "POST /runs/{id}/answers": submit_answers,
}


def _normalise(event: dict[str, Any]) -> dict[str, Any]:
    """Accept routes declared with {run_id} (the manually created API) as well as {id} (template.yaml)."""
    params = event.get("pathParameters") or {}
    if "run_id" in params and "id" not in params:
        event = {**event, "pathParameters": {**params, "id": params["run_id"]}}
    key = event.get("routeKey", "")
    return {**event, "routeKey": key.replace("{run_id}", "{id}")} if "{run_id}" in key else event


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    event = _normalise(event)
    route = ROUTES.get(event.get("routeKey", ""))
    if route is None:
        return _response(404, {"error": "not found"})
    try:
        return route(event)
    except (ApiError, pipeline.PipelineError) as exc:
        return _response(exc.status, {"error": str(exc)})
    except Exception:  # noqa: BLE001  (never leak internals or document content)
        logger.exception("unhandled error on %s", event.get("routeKey"))
        return _response(500, {"error": "internal error"})
