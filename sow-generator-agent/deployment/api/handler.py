"""ingram-sow-api Lambda behind API Gateway (HTTP API): the web UI's API.

Open API: no sign-in, everyone shares one history. Cost guards instead of accounts: API Gateway throttling,
RUNS_PER_DAY across all callers and RUNS_PER_IP_HOUR per caller IP (generations, AI edits and AI reviews).

    POST /wa-reviews   Well-Architected review of a SOW draft, before the architecture diagram.
                       Synchronous: {"review": {...}}. "verify": true adds one model call.
    GET  /session                       quota -> {"ok", "runs_today", "runs_per_day", "runs_per_ip_hour"}
    GET  /templates                     -> {"templates": [{"id", "category", "filename", "label"}]}
    GET  /templates/{category}/{stem}   -> {"id", "markdown"}
    POST /generations                   GenerateRequest fields -> 202 {"job_id"}
    POST /edits                         {"draft_id"?, AgentEditRequest fields} -> 202 {"job_id"}
    POST /checks                        {"draft_id"?, "sow_markdown", "upstream"?, "verify"?} -> 202 {"job_id"}
    GET  /jobs                          recent generations (history), newest first
    GET  /jobs/{id}                     {"job": {kind, status, stage, progress, markdown | proposals | check, error},
                                         "log": [...]}
    POST /exports/docx                  {"draft_id"?, ExportDocxRequest fields} -> {"download_url", "filename", "expires_in"}

Generations, edits and checks write the JOB item first (queued) and start the agent on the AgentCore Runtime,
which answers at once and keeps working in a background thread, writing progress to DynamoDB (the UI polls
GET /jobs/{id}). The .docx export is synchronous (pure python-docx, a second or two): the runtime stores the
file in S3 and this Lambda presigns the download.
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
from pathlib import PurePath
from collections.abc import Callable
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from common import (SHARED_OWNER, data_prefix, get_object, job_key, log, now, parse_time, presign_download, table,
                    update)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_BODY_CHARS = 4_718_592            # 4.5 MiB request body (files arrive base64 inside the JSON)
MAX_CUSTOMER = 200
MAX_OPPORTUNITY = 100
MAX_PROJECT_DETAILS = 100_000
MAX_MARKDOWN = 400_000
MAX_INSTRUCTION = 4_000
MAX_SELECTED = 20_000
MAX_SECTIONS_JSON = 1_500_000
MAX_CHAT_TURNS = 20
MAX_TRANSCRIPTS = 10
MAX_IMAGES = 6
MAX_UPSTREAM_JSON = 1_000_000
MAX_HISTORY = 50
MAX_LOGS_RETURNED = 200
DOWNLOAD_TTL = 900
STALE_AFTER = timedelta(minutes=20)   # no update for this long while queued/running -> the job has died
EDIT_CHECK_TTL_DAYS = 30
TRANSCRIPT_EXT = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
OPPORTUNITY_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
DATA_URL_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,", re.IGNORECASE)
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "60"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "15"))
ACTIVE = {"queued", "running"}
TEMPLATE_CACHE_SECONDS = 600

_agentcore = boto3.client("bedrock-agentcore", config=Config(
    read_timeout=25, connect_timeout=5, retries={"max_attempts": 1, "mode": "standard"}))
_templates_cache: dict[str, Any] = {}


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
    if len(raw) > MAX_BODY_CHARS * (4 / 3 if event.get("isBase64Encoded") else 1):
        raise ApiError(413, "The request is too large: keep files under 4.5 MB in total.")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "The request body must be JSON.") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "The request body must be a JSON object.")
    return body


def _path(event: dict[str, Any], name: str) -> str:
    return str((event.get("pathParameters") or {}).get(name) or "")


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_json_default))


# ---------------------------------------------------------------- input checks
def _text(body: dict[str, Any], name: str, limit: int, *, required: bool = False, label: str | None = None) -> str:
    value = body.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(400, f"{label or name} must be text.")
    value = value.strip()
    if required and not value:
        raise ApiError(400, f"{label or name} is required.")
    if len(value) > limit:
        raise ApiError(400, f"{label or name}: up to {limit:,} characters.")
    return value


def _list(body: dict[str, Any], name: str, limit: int) -> list[Any]:
    value = body.get(name) or []
    if not isinstance(value, list):
        raise ApiError(400, f"{name} must be a list.")
    if len(value) > limit:
        raise ApiError(400, f"{name}: up to {limit} items.")
    return value


def _opportunity(body: dict[str, Any]) -> str | None:
    value = _text(body, "opportunity_id", MAX_OPPORTUNITY, label="Opportunity id")
    if value and not OPPORTUNITY_RE.fullmatch(value):
        raise ApiError(400, "Opportunity id: letters, digits, '.', '_' and '-' only.")
    return value or None


def _template_id(body: dict[str, Any]) -> str:
    value = _text(body, "template_id", 129, required=True, label="Template")
    if not TEMPLATE_ID_RE.fullmatch(value):
        raise ApiError(400, "Template must look like 'aiml/ai-ml'.")
    return value


def _b64(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, f"{label}: the file content is missing.")
    payload = value.split(",", 1)[1] if DATA_URL_RE.match(value) else value
    try:
        base64.b64decode(payload[:4096] + "=" * (-len(payload[:4096]) % 4), validate=True)
    except ValueError as exc:
        raise ApiError(400, f"{label}: the file could not be read.") from exc
    return value


def _file_name(value: Any, allowed: set[str], label: str) -> str:
    name = PurePath(str(value or "")).name.strip()[:120]
    if PurePath(name).suffix.lower() not in allowed:
        raise ApiError(400, f"{label}: use {', '.join(sorted(allowed))} files.")
    return name


def _image(value: Any, label: str) -> str:
    image = _b64(value, label)
    m = DATA_URL_RE.match(image)
    if m and m.group(1).lower() not in ("image/png", "image/jpeg", "image/jpg"):
        raise ApiError(400, f"{label}: architecture images must be PNG or JPEG.")
    return image


def _upstream(body: dict[str, Any]) -> dict[str, Any] | None:
    upstream = body.get("upstream")
    if upstream in (None, "", {}):
        return None
    if not isinstance(upstream, dict):
        raise ApiError(400, "Upstream data must be a JSON object.")
    if len(json.dumps(upstream)) > MAX_UPSTREAM_JSON:
        raise ApiError(400, "Upstream data is too large.")
    return upstream


def _generation_request(body: dict[str, Any]) -> dict[str, Any]:
    transcripts = [{"name": _file_name((f or {}).get("name"), TRANSCRIPT_EXT, "Transcript"),
                    "content_b64": _b64((f or {}).get("content_b64"), f"Transcript {i + 1}")}
                   for i, f in enumerate(_list(body, "transcript_files", MAX_TRANSCRIPTS))]
    images = [_image(img, f"Architecture image {i + 1}")
              for i, img in enumerate(_list(body, "architecture_image_b64s", MAX_IMAGES))]
    if body.get("architecture_image_b64"):
        images.insert(0, _image(body["architecture_image_b64"], "Architecture image"))
    if len(images) > MAX_IMAGES:
        raise ApiError(400, f"Up to {MAX_IMAGES} architecture images.")
    names = [_file_name(n, IMAGE_EXT, "Architecture image") for n in _list(body, "arch_image_filenames", MAX_IMAGES)]
    detail_images = [{"name": _file_name((f or {}).get("name"), IMAGE_EXT, "Project image"),
                      "content_b64": _image((f or {}).get("content_b64"), f"Project image {i + 1}")}
                     for i, f in enumerate(_list(body, "project_detail_images", MAX_IMAGES))]
    answers = body.get("clarification_answers") or {}
    if not isinstance(answers, dict) or len(answers) > 50 or not all(
            isinstance(k, str) and isinstance(v, str) and len(v) <= 4000 for k, v in answers.items()):
        raise ApiError(400, "clarification_answers must map up to 50 questions to text answers.")
    request = {
        "customer_name": _text(body, "customer_name", MAX_CUSTOMER, required=True, label="Customer name"),
        "template_id": _template_id(body),
        "opportunity_id": _opportunity(body),
        "project_details": _text(body, "project_details", MAX_PROJECT_DETAILS, label="Project details"),
        "transcript_files": transcripts,
        "architecture_image_b64s": images,
        "arch_image_filenames": names,
        "project_detail_images": detail_images,
        "clarification_answers": answers,
        "upstream": _upstream(body),
    }
    if not (request["project_details"] or transcripts or request["upstream"]):
        raise ApiError(400, "Add project details, a transcript or upstream data so the agent has something to draft from.")
    return request


def _edit_request(body: dict[str, Any]) -> dict[str, Any]:
    sections = body.get("sections_map") or {}
    if not isinstance(sections, dict) or not sections:
        raise ApiError(400, "sections_map (the draft, section by section) is required.")
    if len(json.dumps(sections)) > MAX_SECTIONS_JSON:
        raise ApiError(400, "The draft is too large for the editor.")
    history = []
    for turn in _list(body, "chat_history", MAX_CHAT_TURNS):
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            raise ApiError(400, "chat_history items need role 'user' or 'assistant'.")
        history.append({"role": turn["role"], "content": str(turn.get("content") or "")[:MAX_SELECTED]})
    outline = [str(h)[:300] for h in _list(body, "document_outline", 400)]
    issue = body.get("resolving_issue")
    if issue is not None and not isinstance(issue, dict):
        raise ApiError(400, "resolving_issue must be an object.")
    instruction = _text(body, "instruction", MAX_INSTRUCTION, required=True, label="Instruction")
    if len(instruction) < 3:
        raise ApiError(400, "Say what to change in the draft.")
    return {
        "instruction": instruction,
        "customer_name": _text(body, "customer_name", MAX_CUSTOMER),
        "template_id": _text(body, "template_id", 129),
        "selected_text": _text(body, "selected_text", MAX_SELECTED),
        "heading_breadcrumb": _text(body, "heading_breadcrumb", 500),
        "document_outline": outline,
        "sections_map": sections,
        "chat_history": history,
        "resolving_issue": {k: str(v)[:2000] for k, v in issue.items()} if issue else None,
    }


def _markdown(body: dict[str, Any]) -> str:
    return _text(body, "sow_markdown", MAX_MARKDOWN, required=True, label="The draft (sow_markdown)")


def _draft_id(body: dict[str, Any]) -> str | None:
    draft_id = str(body.get("draft_id") or "")
    if not draft_id:
        return None
    if not JOB_ID_RE.fullmatch(draft_id):
        raise ApiError(400, "draft_id must be a generation job id.")
    return draft_id


# ---------------------------------------------------------------- caller (no sign-in) + cost guard
def _caller(event: dict[str, Any]) -> dict[str, str]:
    """No accounts: the source IP is used only for the hourly cost guard and stored only as a SHA-256 hash."""
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp") or "unknown")
    return {"ip_hash": hashlib.sha256(ip.encode()).hexdigest()[:32]}


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
               f"You can start up to {RUNS_PER_IP_HOUR} AI runs an hour. Try again later.")
    _take_slot(_day_key(), RUNS_PER_DAY, 3 * 86400,
               f"The daily limit of {RUNS_PER_DAY} AI runs is reached. Try again tomorrow.")


# ---------------------------------------------------------------- runtime
def _session_id(kind: str, ident: str) -> str:
    """runtimeSessionId must be >= 33 characters. One session per draft keeps its microVM warm for edits/exports."""
    sid = f"sow-{kind}-{ident}"
    return sid if len(sid) >= 33 else (sid + "-" + "0" * 33)[:40]


def _invoke(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = _agentcore.invoke_agent_runtime(
            agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"], qualifier="DEFAULT", runtimeSessionId=session_id,
            contentType="application/json", accept="application/json",
            payload=json.dumps(payload).encode("utf-8"))
        raw = resp["response"].read()
    except (BotoCoreError, ClientError) as exc:
        logger.exception("InvokeAgentRuntime failed (%s)", payload.get("action"))
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        if code in ("ThrottlingException", "ServiceQuotaExceededException"):
            raise ApiError(429, "The agent is busy. Try again in a minute.") from exc
        raise ApiError(502, "The agent could not be reached. Try again.") from exc
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise ApiError(502, "The agent returned an unreadable reply.") from exc
    if not isinstance(result, dict):
        raise ApiError(502, "The agent returned an unreadable reply.")
    return result


def _agent_error(result: dict[str, Any]) -> ApiError:
    status = int(result.get("status_code") or 502)
    status = status if status in (400, 404, 409, 413, 429) else 502
    return ApiError(status, str(result.get("error") or "The agent could not complete the request."))


def _start_job(kind: str, request: dict[str, Any], session_for: Callable[[str], str], **meta: Any) -> dict[str, Any]:
    """Write the JOB item (queued), then start the agent; a rejected or failed start marks the job failed."""
    job_id, at = uuid.uuid4().hex, now()
    ttl = int(time.time()) + EDIT_CHECK_TTL_DAYS * 86400 if kind != "generation" else None
    item = {**job_key(job_id), "job_id": job_id, "kind": kind, "created_at": at, "updated_at": at,
            "status": "queued", "stage": "queued", "progress": 0, **{k: v for k, v in meta.items() if v is not None}}
    if kind == "generation":
        item["owner"] = SHARED_OWNER                      # only generations appear in the history (GSI)
    if ttl:
        item["ttl"] = ttl
    table().put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    log(job_id, "queued", f"{kind.capitalize()} queued", ttl=ttl)
    action = {"generation": "generate", "edit": "edit", "check": "check"}[kind]
    try:
        result = _invoke(session_for(job_id), {"action": action, "job_id": job_id, "request": request})
    except ApiError as exc:
        update(job_key(job_id), status="failed", stage="start", progress=100, error=str(exc), finished_at=now())
        raise
    if result.get("status") != "accepted":
        err = _agent_error(result)
        update(job_key(job_id), status="failed", stage="start", progress=100, error=str(err), finished_at=now())
        log(job_id, "start", f"The agent rejected the request: {err}", level="error", ttl=ttl)
        raise err
    return _response(202, {"job_id": job_id, "kind": kind, "status": "queued"})


def _session_for(draft: dict[str, Any] | None) -> Callable[[str], str]:
    """The draft's runtime session when the job belongs to a draft (warm), else one session per job."""
    if draft:
        return lambda _job_id: _session_id("draft", draft["job_id"])
    return lambda job_id: _session_id("job", job_id)


def _job(job_id: str) -> dict[str, Any]:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ApiError(404, "Job not found.")
    item = table().get_item(Key=job_key(job_id)).get("Item")
    if not item:
        raise ApiError(404, "Job not found.")
    return item


def _draft(draft_id: str | None) -> dict[str, Any] | None:
    if not draft_id:
        return None
    item = _job(draft_id)
    if item.get("kind") != "generation":
        raise ApiError(400, "draft_id must be a generation job id.")
    return item


# ---------------------------------------------------------------- routes
def get_session(event: dict[str, Any]) -> dict[str, Any]:
    return _response(200, {"ok": True, "runs_today": _runs_today(), "runs_per_day": RUNS_PER_DAY,
                           "runs_per_ip_hour": RUNS_PER_IP_HOUR})


def _catalog_session() -> str:
    return _session_id("catalog", f"{datetime.now(timezone.utc):%Y%m%d%H}-templates")


def list_templates(event: dict[str, Any]) -> dict[str, Any]:
    cached = _templates_cache.get("list")
    if cached and time.time() - cached[0] < TEMPLATE_CACHE_SECONDS:
        return _response(200, {"templates": cached[1]})
    result = _invoke(_catalog_session(), {"action": "templates"})
    if result.get("status") != "completed":
        raise _agent_error(result)
    templates = result.get("templates") or []
    _templates_cache["list"] = (time.time(), templates)
    return _response(200, {"templates": templates})


def get_template(event: dict[str, Any]) -> dict[str, Any]:
    category, stem = _path(event, "category"), _path(event, "stem")
    if not (SEGMENT_RE.fullmatch(category) and SEGMENT_RE.fullmatch(stem)):
        raise ApiError(404, "Template not found.")
    result = _invoke(_catalog_session(), {"action": "template", "id": f"{category}/{stem}"})
    if result.get("status") != "completed":
        raise _agent_error(result)
    return _response(200, {"id": result.get("id"), "markdown": result.get("markdown") or ""})


def start_generation(event: dict[str, Any]) -> dict[str, Any]:
    caller, body = _caller(event), _body(event)
    request = _generation_request(body)
    _take_run_slots(caller)
    upstream = request["upstream"] or {}
    opportunity = request["opportunity_id"] or str((upstream.get("meta") or {}).get("opportunity_id") or "")[:100] or None
    # The job id doubles as the SOW session id: inputs and outputs land under sow/<opportunity>/<job id>/ in S3,
    # and one runtime session per draft keeps its microVM warm for the edits, checks and exports that follow.
    return _start_job("generation", request, lambda job_id: _session_id("draft", job_id),
                      customer_name=request["customer_name"], template_id=request["template_id"],
                      opportunity_id=opportunity,
                      inputs={"transcripts": [t["name"] for t in request["transcript_files"]],
                              "architecture_images": len(request["architecture_image_b64s"]),
                              "project_details_chars": len(request["project_details"]),
                              "upstream": bool(request["upstream"])})


def start_edit(event: dict[str, Any]) -> dict[str, Any]:
    caller, body = _caller(event), _body(event)
    request = _edit_request(body)
    draft = _draft(_draft_id(body))
    _take_run_slots(caller)
    return _start_job("edit", request, _session_for(draft), draft_id=draft["job_id"] if draft else None,
                      instruction=request["instruction"][:500])


def start_check(event: dict[str, Any]) -> dict[str, Any]:
    caller, body = _caller(event), _body(event)
    verify = body.get("verify", False)
    if not isinstance(verify, bool):
        raise ApiError(400, "verify must be true or false.")
    request = {"sow_markdown": _markdown(body), "upstream": _upstream(body), "verify": verify}
    draft = _draft(_draft_id(body))
    if verify:                       # the rules alone cost no model call
        _take_run_slots(caller)
    return _start_job("check", request, _session_for(draft), draft_id=draft["job_id"] if draft else None,
                      verify=verify)


def start_wa_review(event: dict[str, Any]) -> dict[str, Any]:
    """Well-Architected review of the SOW, run before the architecture diagram is generated.

    Synchronous, unlike check: the rules are deterministic text checks that finish in
    milliseconds, so there is no job to poll. `verify: true` adds one model call and therefore
    takes a run slot.
    """
    caller, body = _caller(event), _body(event)
    verify = body.get("verify", False)
    if not isinstance(verify, bool):
        raise ApiError(400, "verify must be true or false.")
    template_id = str(body.get("template_id") or "").strip() or None
    if template_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9_-]{0,63}", template_id):
        raise ApiError(400, "template_id must look like 'cva/full'.")
    request = {"sow_markdown": _markdown(body), "upstream": _upstream(body), "verify": verify,
               "template_id": template_id}
    draft = _draft(_draft_id(body))
    if verify:                       # the rules alone cost no model call
        _take_run_slots(caller)
    session = _session_id("draft", draft["job_id"]) if draft else _session_id("wa", uuid.uuid4().hex)
    result = _invoke(session, {"action": "wa_review", "request": request})
    if result.get("status") != "completed":
        raise _agent_error(result)
    return _response(200, {"review": result.get("review") or {}, "seconds": result.get("seconds")})


def _expire_if_stale(job: dict[str, Any]) -> dict[str, Any]:
    """A runtime that died (MaxLifetime, crash) cannot write its own failure: mark the job failed on read."""
    if job.get("status") in ACTIVE and datetime.now(timezone.utc) - parse_time(str(job["updated_at"])) > STALE_AFTER:
        message = "The job stopped without finishing (no progress for 20 minutes). Start it again."
        update(job_key(job["job_id"]), status="failed", stage="timeout", progress=100, error=message, finished_at=now())
        log(job["job_id"], "timeout", message, level="error", ttl=job.get("ttl"))
        job = {**job, "status": "failed", "stage": "timeout", "progress": 100, "error": message}
    return job


def _load_s3_json(key: str | None) -> Any:
    if not key or not str(key).startswith(data_prefix() + "/"):
        return None
    return json.loads(get_object(str(key)))


def get_job(event: dict[str, Any]) -> dict[str, Any]:
    job = _expire_if_stale(_job(_path(event, "id")))
    if job.get("markdown_in_s3") and job.get("markdown_key") and str(job["markdown_key"]).startswith(data_prefix() + "/"):
        job["markdown"] = get_object(str(job["markdown_key"])).decode("utf-8")
    for name in ("proposals", "check"):
        if job.get(f"{name}_key"):
            job[name] = _load_s3_json(job[f"{name}_key"])
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"JOB#{job['job_id']}") & Key("sk").begins_with("LOG#")}
    while True:
        page = table().query(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    logs = [{k: i.get(k) for k in ("at", "stage", "message", "level")} for i in items][-MAX_LOGS_RETURNED:]
    out = {k: v for k, v in job.items() if k not in ("pk", "sk", "owner", "ttl")}
    return _response(200, {"job": _plain(out), "log": _plain(logs)})


def list_jobs(event: dict[str, Any]) -> dict[str, Any]:
    page = table().query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(SHARED_OWNER),
                         ScanIndexForward=False, Limit=MAX_HISTORY)
    jobs = [{k: item.get(k) for k in ("job_id", "kind", "status", "stage", "created_at", "finished_at", "customer_name",
                                      "template_id", "opportunity_id", "chars")} for item in page.get("Items", [])]
    return _response(200, {"jobs": _plain(jobs)})


def export_docx(event: dict[str, Any]) -> dict[str, Any]:
    body = _body(event)
    draft = _draft(_draft_id(body))
    images = []
    for i, img in enumerate(_list(body, "architecture_images", MAX_IMAGES)):
        if not isinstance(img, dict):
            raise ApiError(400, "architecture_images items need filename and content_b64.")
        images.append({"filename": _file_name(img.get("filename"), IMAGE_EXT, "Architecture image"),
                       "content_b64": _image(img.get("content_b64"), f"Architecture image {i + 1}")})
    persist = body.get("persist_output", True)
    request = {
        "customer_name": _text(body, "customer_name", MAX_CUSTOMER, required=True, label="Customer name"),
        "sow_markdown": _markdown(body),
        "doc_title": _text(body, "doc_title", 200) or "Statement of Work",
        "opportunity_id": _opportunity(body) or (draft or {}).get("opportunity_id"),
        "session_id": draft["job_id"] if draft else uuid.uuid4().hex,   # the .docx lands beside the draft's inputs
        "persist_output": bool(persist),
        "architecture_images": images,
    }
    session = _session_id("draft", draft["job_id"]) if draft else _session_id("export", request["session_id"])
    result = _invoke(session, {"action": "export_docx", "request": request})
    if result.get("status") != "completed":
        raise _agent_error(result)
    key, filename = str(result.get("key") or ""), str(result.get("filename") or "SOW.docx")
    if not key.startswith(data_prefix() + "/"):
        raise ApiError(502, "The agent stored the document in an unexpected place.")
    if draft:
        update(job_key(draft["job_id"]), last_export_key=key, last_export_at=now())
    return _response(200, {"download_url": presign_download(key, filename, DOWNLOAD_TTL), "filename": filename,
                           "expires_in": DOWNLOAD_TTL})


ROUTES = {
    "GET /session": get_session,
    "GET /templates": list_templates,
    "GET /templates/{category}/{stem}": get_template,
    "POST /generations": start_generation,
    "POST /edits": start_edit,
    "POST /wa-reviews": start_wa_review,
    "POST /checks": start_check,
    "GET /jobs": list_jobs,
    "GET /jobs/{id}": get_job,
    "POST /exports/docx": export_docx,
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
