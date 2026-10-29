"""ingram-pipeline-api Lambda behind API Gateway (HTTP API): the orchestrator UI's API.

Open API: no sign-in, everyone shares one run history. Cost guards instead of accounts: API
Gateway throttling, RUNS_PER_DAY across all callers and RUNS_PER_IP_HOUR per caller IP, because
one run is five agents and a dozen model calls.

    GET  /session           quota -> {"ok", "runs_today", "runs_per_day", "stages": [...]}
    POST /runs              {"template_id", "upstream", "project_details"?, "sow_markdown"?,
                             "title"?, "stop_on_blocked"?} -> 202 {"run_id"}
    GET  /runs              run history, newest first -> {"runs": [...]}
    GET  /runs/{id}         -> {"run", "state", "log"}
    GET  /runs/{id}/sow     -> {"markdown"}        the generated SOW
    GET  /runs/{id}/diagram -> {"png_base64"}      the architecture diagram, when the stage has produced one
    GET  /runs/{id}/architecture   the design the architecture agent prepared: components, flows, decisions, ADR
    POST /runs/{id}/continue    override a stage that stopped for review -> 202 {"run_id"}
    POST /runs/{id}/sa-review   {"comment"?, "sign_off"?} the Solutions Architect's build checkpoint:
                            record a comment and/or approve the draft -> 200/202 {"run_id", "signed_off"}
    POST /runs/{id}/revise  {"target": "sow" | "architecture" | "cost", "instruction"} -> 202 {"run_id"}
                            ask the AI to change one of them; the stages after it are re-run
    POST /runs/{id}/export  the combined SOW as .docx: final SOW + architecture diagram + the real cost
                            estimate link -> {"download_url", "filename", "expires_in"}

POST /runs stores the request and starts the worker, which runs one stage per invocation and
re-invokes itself. The UI polls GET /runs/{id}.
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
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from common import SHARED_OWNER, TIME_FORMAT, log, meta_key, now, plain, table, update
from store import load, meta_fields, reopen, save

from orchestrator import adapters
from orchestrator.contracts import (MAX_REVISIONS_PER_RUN, REVISION_LABELS, REVISION_TARGETS,
                                    STAGE_AGENTS, STAGE_LABELS, STAGES, PipelineState, Stage, Status)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MAX_TITLE = 200
MAX_DETAILS = 50_000
MAX_SOW_CHARS = 400_000
MAX_UPSTREAM_JSON = 400_000
MAX_LOGS_RETURNED = 300
MAX_HISTORY = 50
# A full run is five stages, each re-invoking the worker; 40 minutes with no progress is dead.
STALE_AFTER = timedelta(minutes=40)
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "20"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "3"))
# A change is one to four model calls, against a run's dozen, so it is capped separately and higher.
REVISIONS_PER_DAY = int(os.environ.get("REVISIONS_PER_DAY", "60"))
REVISIONS_PER_IP_HOUR = int(os.environ.get("REVISIONS_PER_IP_HOUR", "10"))
MIN_INSTRUCTION = 5
MAX_INSTRUCTION = 2_000
MAX_COMMENT = 2_000
WEB_USER = "Web user"
HISTORY_FIELDS = ("run_id", "title", "status", "stage", "progress", "created_at", "finished_at",
                  "user_name", "calculator_url", "estimate_name")

_lambda = boto3.client("lambda")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(plain(body))}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _path(event: dict[str, Any], name: str) -> str:
    return str((event.get("pathParameters") or {}).get(name) or "")


def _caller(event: dict[str, Any]) -> dict[str, str]:
    ip = str(((event.get("requestContext") or {}).get("http") or {}).get("sourceIp") or "unknown")
    return {"user_name": WEB_USER, "ip_hash": hashlib.sha256(ip.encode()).hexdigest()[:32]}


# ---------------------------------------------------------------- cost guard
def _day_key() -> dict[str, str]:
    return {"pk": f"DAY#{datetime.now(timezone.utc):%Y-%m-%d}", "sk": "COUNT"}


def _runs_today() -> int:
    return int((table().get_item(Key=_day_key()).get("Item") or {}).get("n") or 0)


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
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    _take_slot({"pk": f"IP#{caller['ip_hash']}#{hour}", "sk": "COUNT"}, RUNS_PER_IP_HOUR, 2 * 3600,
               f"You can start up to {RUNS_PER_IP_HOUR} pipeline runs an hour. Try again later.")
    _take_slot(_day_key(), RUNS_PER_DAY, 3 * 86400,
               f"The daily limit of {RUNS_PER_DAY} pipeline runs is reached. Try again tomorrow.")


def _start_worker(run_id: str) -> None:
    _lambda.invoke(FunctionName=os.environ["WORKER_FUNCTION"], InvocationType="Event",
                   Payload=json.dumps({"run_id": run_id}).encode())


# ---------------------------------------------------------------- validation
def _upstream(body: dict[str, Any]) -> dict[str, Any]:
    upstream = body.get("upstream")
    if upstream in (None, "", {}):
        raise ApiError(400, "Upstream intake data is required to start a run.")
    if not isinstance(upstream, dict):
        raise ApiError(400, "Upstream data must be a JSON object.")
    if len(json.dumps(upstream)) > MAX_UPSTREAM_JSON:
        raise ApiError(400, "Upstream data is too large.")
    if not isinstance(upstream.get("intake"), dict) or not str(
            (upstream["intake"] or {}).get("customer_name") or "").strip():
        raise ApiError(400, "Upstream data needs intake.customer_name.")
    return upstream


def _template_id(body: dict[str, Any], upstream: dict[str, Any]) -> str:
    template_id = str(body.get("template_id") or (upstream.get("meta") or {}).get("template_id") or "")
    if not TEMPLATE_ID_RE.fullmatch(template_id):
        raise ApiError(400, "A template_id like 'aiml/ai-ml' is required.")
    return template_id


# ---------------------------------------------------------------- routes
def get_session(event: dict[str, Any]) -> dict[str, Any]:
    return _response(200, {
        "ok": True,
        "runs_today": _runs_today(),
        "runs_per_day": RUNS_PER_DAY,
        "runs_per_ip_hour": RUNS_PER_IP_HOUR,
        # The UI renders the sequence from here so it cannot drift from the code.
        "stages": [{"stage": s.value, "label": STAGE_LABELS[s], "agent": STAGE_AGENTS[s]}
                   for s in STAGES],
    })


def start_run(event: dict[str, Any]) -> dict[str, Any]:
    caller, body = _caller(event), _body(event)
    upstream = _upstream(body)
    template_id = _template_id(body, upstream)

    sow_markdown = str(body.get("sow_markdown") or "")
    if len(sow_markdown) > MAX_SOW_CHARS:
        raise ApiError(400, f"Keep the SOW under {MAX_SOW_CHARS:,} characters.")
    details = str(body.get("project_details") or "")[:MAX_DETAILS]
    stop_on_blocked = body.get("stop_on_blocked", True)
    if not isinstance(stop_on_blocked, bool):
        raise ApiError(400, "stop_on_blocked must be true or false.")

    _take_run_slots(caller)
    run_id, at = uuid.uuid4().hex, now()
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:MAX_TITLE] \
        or str((upstream.get("intake") or {}).get("customer_name") or "")[:MAX_TITLE]

    table().put_item(Item={
        **meta_key(run_id), "run_id": run_id, "owner": SHARED_OWNER, "created_at": at,
        "updated_at": at, "user_name": caller["user_name"], "title": title,
        "template_id": template_id, "status": "queued", "stage": STAGES[0].value, "progress": 0,
    })
    state = PipelineState(run_id=run_id, template_id=template_id, upstream=upstream,
                          project_details=details, sow_markdown=sow_markdown,
                          stop_on_blocked=stop_on_blocked)
    save(state)
    update(meta_key(run_id), status="queued", stage=STAGES[0].value)
    log(run_id, "queued", f"{caller['user_name']} started a pipeline run"
                          + (" from a supplied SOW" if sow_markdown.strip() else ""))
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
    """A worker killed by the Lambda timeout cannot write its own failure."""
    if meta.get("status") in ("queued", "running"):
        updated = datetime.strptime(str(meta.get("updated_at")), TIME_FORMAT).replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated > STALE_AFTER:
            message = "The run stopped without finishing (time limit). Start it again."
            update(meta_key(meta["run_id"]), status="failed", error=message, finished_at=now())
            log(meta["run_id"], "timeout", message, level="error")
            meta = {**meta, "status": "failed", "error": message}
    return meta


def list_runs(event: dict[str, Any]) -> dict[str, Any]:
    page = table().query(IndexName="owner-created", KeyConditionExpression=Key("owner").eq(SHARED_OWNER),
                         ScanIndexForward=False, Limit=MAX_HISTORY)
    return _response(200, {"runs": [{k: item.get(k) for k in HISTORY_FIELDS}
                                    for item in page.get("Items", [])]})


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
    logs = [{k: v for k, v in i.items() if k not in ("pk", "sk")}
            for i in items if str(i["sk"]).startswith("LOG#")][-MAX_LOGS_RETURNED:]

    try:
        state = _state_for_ui(load(run_id))
    except KeyError:
        state = None
    run = {k: v for k, v in meta.items() if k not in ("pk", "sk", "owner")}
    return _response(200, {"run": run, "state": state, "log": logs})


def _state_for_ui(state: PipelineState) -> dict[str, Any]:
    """The run state plus the Well-Architected findings, which are the point of stage 2.

    `PipelineState.to_json` stays agent-agnostic, so the findings are attached here rather than
    in the contract. The rest of each stage's output is too large for a poll response and is
    fetched on demand instead.
    """
    payload = state.to_json()
    review = (state.run(Stage.WA_REVIEW).output or {}).get("review") or {}
    for entry in payload["stages"]:
        if entry["stage"] == Stage.SA_REVIEW.value:
            entry["comments"] = state.sa_comments
            entry["signed_off"] = state.sa_signed_off
        elif entry["stage"] == Stage.WA_REVIEW.value:
            entry["findings"] = review.get("findings") or []
            entry["summary"] = review.get("summary") or {}
        elif entry["stage"] == Stage.COST.value:
            cost = state.run(Stage.COST).output or {}
            entry["calculator_url"] = cost.get("calculator_url")
        elif entry["stage"] == Stage.COMBINED_REVIEW.value:
            output = state.run(Stage.COMBINED_REVIEW).output or {}
            check = output.get("check") or {}
            entry["summary"] = check.get("summary") or {}
            entry["failed_rules"] = [
                {"id": r.get("id"), "name": r.get("name"), "severity": r.get("severity")}
                for r in (check.get("rules") or []) if r.get("status") == "fail"
            ]
            entry["sa_findings"] = output.get("sa_findings") or []
    return payload


def get_run_sow(event: dict[str, Any]) -> dict[str, Any]:
    """The generated SOW. Separate from GET /runs/{id} because it is large."""
    meta = _meta(_path(event, "id"))
    try:
        state = load(meta["run_id"])
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    if not state.sow_markdown:
        raise ApiError(404, "This run has no SOW yet.")
    return _response(200, {"markdown": state.sow_markdown, "scope": state.scope or None})


def get_run_diagram(event: dict[str, Any]) -> dict[str, Any]:
    """The architecture diagram PNG. Separate from GET /runs/{id} because it is far too large to poll."""
    meta = _meta(_path(event, "id"))
    try:
        state = load(meta["run_id"])
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    png = ((state.run(Stage.ARCHITECTURE).output or {}).get("diagram") or {}).get("png_base64")
    if not isinstance(png, str) or not png:
        raise ApiError(404, "This run has no architecture diagram yet.")
    return _response(200, {"png_base64": png})


def continue_run(event: dict[str, Any]) -> dict[str, Any]:
    """Override a stage that stopped for review and carry on.

    This is the deliberate human decision the block exists to force. It only reopens a BLOCKED
    stage; a failed stage has no usable output for the next stage to build on, so it cannot be
    waved through.
    """
    meta = _expire_if_stale(_meta(_path(event, "id")))
    run_id = meta["run_id"]
    try:
        state = load(run_id)
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    if state.status != Status.BLOCKED:
        raise ApiError(409, f"This run is {state.status.value}, not stopped for review.")
    blocked = [s.value for s in STAGES if state.run(s).status == Status.BLOCKED]
    save(reopen(state))
    log(run_id, "override", f"{_caller(event)['user_name']} chose to continue past "
                            f"{', '.join(blocked)} despite its findings", level="warning")
    _start_worker(run_id)
    return _response(202, {"run_id": run_id, "continued_past": blocked})


def sa_review_run(event: dict[str, Any]) -> dict[str, Any]:
    """The Solutions Architect's build checkpoint, before the Well-Architected review.

    The SA edits the SOW with AI through POST /runs/{id}/revise; this route is for the other half
    of how they work: leaving a comment of record, and approving the draft so the run carries on.
    A comment alone keeps the run stopped; `sign_off` lets it continue to the review.
    """
    meta = _expire_if_stale(_meta(_path(event, "id")))
    run_id = meta["run_id"]
    try:
        state = load(run_id)
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    if state.run(Stage.SA_REVIEW).status != Status.BLOCKED:
        raise ApiError(409, "This run is not waiting for the Solutions Architect's review.")

    body, caller = _body(event), _caller(event)
    comment = re.sub(r"[ \t]+", " ", str(body.get("comment") or "")).strip()[:MAX_COMMENT]
    sign_off = body.get("sign_off", False)
    if not isinstance(sign_off, bool):
        raise ApiError(400, "sign_off must be true or false.")
    if not comment and not sign_off:
        raise ApiError(400, "Add a comment or approve the draft to continue.")

    if comment:
        state.sa_comments.append({"text": comment, "by": caller["user_name"], "at": now()})
        log(run_id, Stage.SA_REVIEW.value, f"{caller['user_name']} commented: {comment[:300]}")

    if not sign_off:
        save(state)
        return _response(200, {"run_id": run_id, "signed_off": False,
                               "comments": len(state.sa_comments)})

    state.sa_signed_off = True
    sa = state.run(Stage.SA_REVIEW)
    sa.status = Status.COMPLETED
    n = len(state.sa_comments)
    sa.detail = (f"Approved by {caller['user_name']}" + (f" with {n} comment(s)" if n else "") + ".")
    save(state)
    log(run_id, Stage.SA_REVIEW.value,
        f"{caller['user_name']} approved the draft; continuing to the Well-Architected review")
    _start_worker(run_id)
    return _response(202, {"run_id": run_id, "signed_off": True, "comments": n})


def get_run_architecture(event: dict[str, Any]) -> dict[str, Any]:
    """The design the architecture agent prepared, as data the UI can lay out: components, flows,
    decisions, assumptions, and the ADR and usage assumptions as markdown.

    Separate from GET /runs/{id} (too large to poll) and from /diagram (the PNG is larger still).
    """
    meta = _meta(_path(event, "id"))
    try:
        state = load(meta["run_id"])
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    output = state.run(Stage.ARCHITECTURE).output or {}
    design = adapters.design_of(output)
    if not output or not design:
        raise ApiError(404, "This run has no architecture design yet.")

    components = [c for c in design.get("components") or [] if isinstance(c, dict)]
    names = {str(c.get("id")): str(c.get("name") or c.get("service") or c.get("id")) for c in components}
    usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
    def texts(items: Any) -> list[str]:
        """Plain strings from a list of strings or of {"text": ...} objects."""
        found = [str((i.get("text") if isinstance(i, dict) else i) or "").strip() for i in items or [] if i]
        return [t for t in found if t]

    return _response(200, {
        "title": design.get("title"),
        "region": design.get("region"),
        "zones": design.get("availability_zones") or ([design["availability_zone"]] if design.get("availability_zone") else []),
        "context": design.get("context"),
        "components": [{k: c.get(k) for k in ("id", "name", "service", "category", "role", "purpose",
                                              "sku_summary", "quantity", "assumed")} for c in components],
        "flows": [{"from": names.get(str(r.get("source")), r.get("source")),
                   "to": names.get(str(r.get("target")), r.get("target")),
                   "label": r.get("label"), "protocol": r.get("protocol")}
                  for r in design.get("relationships") or [] if isinstance(r, dict)],
        "decisions": [{k: d.get(k) for k in ("id", "title", "status", "decision", "context")}
                      for d in design.get("decisions") or [] if isinstance(d, dict)],
        "requirements": [{"id": r.get("id"), "text": r.get("text"), "kind": r.get("kind")}
                         for r in design.get("requirements") or [] if isinstance(r, dict)],
        "assumptions": texts(design.get("assumptions")),
        "security_operations": texts(design.get("security_operations")),
        "open_questions": texts(design.get("open_questions")),
        "adr_md": (output.get("adr") or {}).get("adr_md") or "",
        "usage_assumptions_md": usage.get("usage_assumptions_md") or "",
        "scope_warnings": output.get("scope_warnings") or [],
        "report_status": output.get("report_status"),
    })


def _reserve_for_revision(run_id: str) -> None:
    """Take the run for a change, atomically, so two changes cannot be queued on top of each other."""
    try:
        table().update_item(
            Key=meta_key(run_id),
            UpdateExpression="SET #status = :queued, updated_at = :at",
            ConditionExpression="#status IN (:completed, :failed, :blocked)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":queued": "queued", ":completed": "completed",
                                       ":failed": "failed", ":blocked": "blocked", ":at": now()})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(409, "This run is still working. Wait for it to finish, then ask for the change.") from exc
        raise


def _check_revisable(state: PipelineState, target: str) -> None:
    if state.revision:
        raise ApiError(409, "A change is already being applied to this run.")
    if len(state.revisions) >= MAX_REVISIONS_PER_RUN:
        raise ApiError(409, f"This run has had {MAX_REVISIONS_PER_RUN} changes. Start a new run to keep going.")
    if state.next_stage() is not None:
        raise ApiError(409, "This run has not finished, so there is nothing settled to change. "
                            "Let it finish, or start it again.")
    if target == "sow":
        if not state.sow_markdown.strip():
            raise ApiError(409, "This run has no SOW to change yet.")
        return
    architecture = state.run(Stage.ARCHITECTURE)
    if architecture.status != Status.COMPLETED or not adapters.design_of(architecture.output or {}):
        raise ApiError(409, "There is no architecture design to "
                            + ("change" if target == "architecture" else "price") + " yet. "
                            "Change the SOW first, or let the run reach the architecture stage.")


def revise_run(event: dict[str, Any]) -> dict[str, Any]:
    """Ask the AI to change the SOW, the architecture design or the cost estimate.

    Queues the change and starts the worker; the UI polls GET /runs/{id}. The change goes to the
    agent that owns the thing being changed, and every stage after it is re-run, because their
    inputs are now different.
    """
    body, caller = _body(event), _caller(event)
    target = str(body.get("target") or "").strip().lower()
    if target not in REVISION_TARGETS:
        raise ApiError(400, "target must be one of: " + ", ".join(REVISION_TARGETS) + ".")
    instruction = re.sub(r"[ \t]+", " ", str(body.get("instruction") or "")).strip()
    if len(instruction) < MIN_INSTRUCTION:
        raise ApiError(400, "Say what to change, in a sentence.")
    if len(instruction) > MAX_INSTRUCTION:
        raise ApiError(400, f"Keep the instruction under {MAX_INSTRUCTION:,} characters.")

    meta = _expire_if_stale(_meta(_path(event, "id")))
    run_id = meta["run_id"]
    try:
        state = load(run_id)
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    _check_revisable(state, target)

    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    _take_slot({"pk": f"REVIP#{caller['ip_hash']}#{hour}", "sk": "COUNT"}, REVISIONS_PER_IP_HOUR, 2 * 3600,
               f"You can ask for up to {REVISIONS_PER_IP_HOUR} changes an hour. Try again later.")
    _take_slot({"pk": f"REVDAY#{datetime.now(timezone.utc):%Y-%m-%d}", "sk": "COUNT"}, REVISIONS_PER_DAY,
               3 * 86400, f"The daily limit of {REVISIONS_PER_DAY} changes is reached. Try again tomorrow.")
    _reserve_for_revision(run_id)

    state.revision = {"target": target, "instruction": instruction, "requested_at": now(),
                      "requested_by": caller["user_name"]}
    save(state, refresh_meta=False)
    update(meta_key(run_id), stage=REVISION_TARGETS[target].value, progress=state.progress())
    log(run_id, "revision", f"{caller['user_name']} asked the AI to change the "
                            f"{REVISION_LABELS[target]}: {instruction[:300]}")
    _start_worker(run_id)
    return _response(202, {"run_id": run_id, "target": target})


DOWNLOAD_TTL = 900


def _export_caller():
    """The AgentCore caller for the SOW agent. A function so tests can replace it without AWS."""
    from orchestrator.agents import AgentCoreCaller

    return AgentCoreCaller(read_timeout=120)


def _presign(key: str, filename: str) -> str:
    safe = filename.replace('"', "")
    return boto3.client("s3").generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["SOW_DATA_BUCKET"], "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{safe}"'},
        ExpiresIn=DOWNLOAD_TTL)


def export_run(event: dict[str, Any]) -> dict[str, Any]:
    """The combined SOW as a branded .docx.

    Combined means the three agents' work in one document: the final SOW text (with the real cost
    estimate link already written into its Pricing section), the architecture diagram drawn from
    that SOW, and the scope. The SOW agent renders the .docx, so the Ingram cover, headings and
    acceptance block stay in one place.
    """
    from orchestrator.agents import AgentError, session_id
    from orchestrator.contracts import STAGE_AGENTS

    if not os.environ.get("SOW_DATA_BUCKET") or not os.environ.get("SOW_AGENT_RUNTIME_ARN"):
        raise ApiError(501, "Export is not configured: set SowAgentRuntimeArn and SowDataBucketName on the stack.")
    meta = _meta(_path(event, "id"))
    run_id = meta["run_id"]
    try:
        state = load(run_id)
    except KeyError:
        raise ApiError(404, "Run not found.") from None
    if not state.sow_markdown.strip():
        raise ApiError(409, "This run has no SOW to export yet.")
    if state.run(Stage.COST).status != Status.COMPLETED:
        raise ApiError(409, "The cost estimate has not finished, so the SOW has no cost link yet. "
                            "Export the combined SOW once the cost stage is complete.")

    diagram = ((state.run(Stage.ARCHITECTURE).output or {}).get("diagram") or {}).get("png_base64")
    images = ([{"filename": "architecture.png", "content_b64": f"data:image/png;base64,{diagram}"}]
              if isinstance(diagram, str) and diagram else [])
    intake = (state.upstream or {}).get("intake") or {}
    request = {
        "customer_name": str(intake.get("customer_name") or meta.get("title") or "Customer")[:200],
        "sow_markdown": state.sow_markdown,
        "doc_title": "Statement of Work",
        "opportunity_id": ((state.upstream or {}).get("meta") or {}).get("opportunity_id"),
        "session_id": run_id,
        "persist_output": True,
        "architecture_images": images,
    }
    try:
        result = _export_caller()(
            "sow", {"action": "export_docx", "request": request},
            session=session_id("sow", run_id, "export"))
    except AgentError as exc:
        raise ApiError(502, f"The SOW agent could not render the document: {exc}") from exc
    if result.get("status") != "completed":
        raise ApiError(502, str(result.get("error") or "The SOW agent could not render the document."))
    key, filename = str(result.get("key") or ""), str(result.get("filename") or "SOW.docx")
    prefix = os.environ.get("SOW_DATA_PREFIX", "sow").strip("/")
    if not key.startswith(prefix + "/"):
        raise ApiError(502, "The agent stored the document in an unexpected place.")
    log(run_id, "export", f"Combined SOW exported as {filename} ({'with' if images else 'without'} the architecture diagram)")
    return _response(200, {"download_url": _presign(key, filename), "filename": filename,
                           "expires_in": DOWNLOAD_TTL, "with_diagram": bool(images)})


ROUTES = {
    "GET /session": get_session,
    "POST /runs": start_run,
    "GET /runs": list_runs,
    "GET /runs/{id}": get_run,
    "GET /runs/{id}/sow": get_run_sow,
    "GET /runs/{id}/diagram": get_run_diagram,
    "GET /runs/{id}/architecture": get_run_architecture,
    "POST /runs/{id}/continue": continue_run,
    "POST /runs/{id}/sa-review": sa_review_run,
    "POST /runs/{id}/revise": revise_run,
    "POST /runs/{id}/export": export_run,
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
