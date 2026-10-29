"""intake-api Lambda behind API Gateway (HTTP API).

No sign-in page: the browser gets an anonymous session that owns its runs. Connecting Microsoft
(Outlook) is optional and attaches the user's delegated Graph refresh token to that session
(device-code flow, so the Entra app needs only "Allow public client flows" = Yes and delegated
Mail.Read / User.Read). CRM records and seller notes work without Microsoft.

    POST /session                          -> {session, expires_in}            new anonymous session
    GET  /session                          -> {microsoft: {name, email} | null}
    POST /auth/device/start                -> {login_id, user_code, verification_uri, interval}
    POST /auth/device/poll   {"login_id"}  -> {status: pending | done, user}   connects Outlook
    POST /auth/disconnect                  -> removes the Microsoft connection from the session
    POST /runs  {"outlook": {"days": 1|7|30} | null, "crm_files": [...], "notes_files": [...],
                 "notes_text": "..."}       -> 202 {run_id}; files are {"name", "content_base64"}
    GET  /runs[?cursor=...]                -> {runs, next_cursor}  this browser's previous runs, newest first
    GET  /runs/{id}                        -> status, phase, counts, consolidated opportunities, logs

Any one source is enough. Every call except POST /session needs "Authorization: Bearer <session>".
DynamoDB keeps only the SHA-256 of the session key; the session lives SESSION_DAYS (renewed while
used) so its run history stays available, the Microsoft connection only MS_HOURS. Runs are kept
(no TTL) and readable only with the session that started them. Because the API is open, runs are rate limited per caller IP and per day (cost guard).
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

import dynamics
import uploads

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ALLOWED_DAYS = {1, 7, 30}
MAX_DYNAMICS_OPPORTUNITIES = 50   # open Dynamics opportunities read per run
MAX_LOGS_RETURNED = 30
SESSION_DAYS = 90          # the browser's identity: owns its run history; renewed while in use
MS_HOURS = 12              # the Microsoft (Outlook) connection expires sooner than the session
HISTORY_PAGE = 20
OWNER_INDEX = "owner-created"
SESSIONS_PER_IP_HOUR = int(os.environ.get("SESSIONS_PER_IP_HOUR", "30"))
RUNS_PER_IP_HOUR = int(os.environ.get("RUNS_PER_IP_HOUR", "15"))
RUNS_PER_DAY = int(os.environ.get("RUNS_PER_DAY", "300"))
GRAPH_SCOPES = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access"
DEFAULT_VERIFY_URI = "https://microsoft.com/devicelogin"
MS_FIELDS = ("user_id", "user_email", "user_name", "refresh_token", "ms_expires")

_table = boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])
# The agent answers within seconds; keep well under API Gateway's 30 s limit.
_agentcore = boto3.client("bedrock-agentcore", config=Config(read_timeout=25, retries={"max_attempts": 2}))


class Unauthorized(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.extra = status, extra


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, default=_json_default)}


def _body(event: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _bearer_token(event: dict[str, Any]) -> str:
    header = (event.get("headers") or {}).get("authorization", "")
    return header.split(" ", 1)[1].strip() if header.lower().startswith("bearer ") else ""


def _source_ip(event: dict[str, Any]) -> str:
    return ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp", "unknown")


def _limit(name: str, window: str, limit: int, ttl_seconds: int) -> None:
    """Atomic counter per window; raises 429 once the limit is passed."""
    item = _table.update_item(
        Key={"pk": f"QUOTA#{name}#{window}", "sk": "META"},
        UpdateExpression="ADD hits :one SET #t = if_not_exists(#t, :ttl)",
        ExpressionAttributeNames={"#t": "ttl"},
        ExpressionAttributeValues={":one": 1, ":ttl": int(time.time()) + ttl_seconds},
        ReturnValues="UPDATED_NEW",
    )["Attributes"]
    if int(item["hits"]) > limit:
        logger.warning("quota %s exceeded (%s > %d)", name, item["hits"], limit)
        raise HttpError(429, "Too many requests. Please wait a while and try again.")


def _limit_ip(kind: str, event: dict[str, Any], limit: int) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    _limit(f"{kind}#{_hash(_source_ip(event))[:24]}", hour, limit, 2 * 3600)


# ---------------------------------------------------------------- Microsoft Entra / Graph (no SDK needed)

def _entra(path: str, data: dict[str, str]) -> dict[str, Any]:
    """POST to the tenant's v2.0 OAuth endpoint. Returns the JSON body, including OAuth error bodies."""
    url = f"https://login.microsoftonline.com/{os.environ['ENTRA_TENANT_ID']}/oauth2/v2.0/{path}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except ValueError:
            return {"error": f"http_{exc.code}"}


def _graph_me(access_token: str) -> dict[str, Any]:
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName",
                                 headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------- sessions

def _session_key(key: str) -> dict[str, str]:
    return {"pk": f"SESSION#{key}", "sk": "META"}


def _session(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    token = _bearer_token(event)
    if not token:
        raise Unauthorized("Session required.")
    key = _hash(token)
    item = _table.get_item(Key=_session_key(key)).get("Item")
    # TTL deletion can lag, so check expiry here too.
    now = time.time()
    if not item or int(item.get("ttl", 0)) < now:
        raise Unauthorized("Your session has expired.")
    # Sliding expiry: renewed at most once a day, so run history stays reachable while the browser is used.
    if int(item["ttl"]) < now + (SESSION_DAYS - 1) * 86400:
        item["ttl"] = int(now) + SESSION_DAYS * 86400
        _table.update_item(Key=_session_key(key), UpdateExpression="SET #t = :t",
                           ExpressionAttributeNames={"#t": "ttl"}, ExpressionAttributeValues={":t": item["ttl"]})
    # The Microsoft connection (refresh token) has its own, shorter lifetime.
    if item.get("refresh_token") and int(item.get("ms_expires", 0)) < now:
        _disconnect(key, item)
        item = {k: v for k, v in item.items() if k not in MS_FIELDS}
    return key, item


def _microsoft(session: dict[str, Any]) -> dict[str, str] | None:
    if not session.get("refresh_token"):
        return None
    return {"name": session.get("user_name", ""), "email": session.get("user_email", ""),
            "expires_at": int(session.get("ms_expires", 0))}


def _disconnect(key: str, session: dict[str, Any]) -> None:
    _table.put_item(Item={k: v for k, v in session.items() if k not in MS_FIELDS} | _session_key(key))


def _graph_token(key: str, session: dict[str, Any]) -> str:
    """Fresh Outlook (Graph) access token for this session; stores the rotated refresh token."""
    result = _entra("token", {"client_id": os.environ["ENTRA_CLIENT_ID"], "grant_type": "refresh_token",
                              "refresh_token": session["refresh_token"], "scope": GRAPH_SCOPES})
    if "access_token" not in result:
        logger.warning("refresh failed for %s: %s", session.get("user_id"), result.get("error"))
        _disconnect(key, session)
        raise HttpError(409, "Your Microsoft connection has expired. Connect Outlook again.", microsoft=None)
    if result.get("refresh_token"):
        _table.update_item(Key=_session_key(key), UpdateExpression="SET refresh_token = :r",
                           ExpressionAttributeValues={":r": result["refresh_token"]})
    return result["access_token"]


def create_session(event: dict[str, Any]) -> dict[str, Any]:
    _limit_ip("session", event, SESSIONS_PER_IP_HOUR)
    token = secrets.token_urlsafe(32)
    _table.put_item(Item={**_session_key(_hash(token)), "created_at": _now(),
                          "ttl": int(time.time()) + SESSION_DAYS * 86400})
    return _response(200, {"session": token, "expires_in": SESSION_DAYS * 86400})


def get_session(event: dict[str, Any]) -> dict[str, Any]:
    _, session = _session(event)
    return _response(200, {"microsoft": _microsoft(session), "expires_at": int(session["ttl"])})


def device_start(event: dict[str, Any]) -> dict[str, Any]:
    key, _ = _session(event)
    result = _entra("devicecode", {"client_id": os.environ["ENTRA_CLIENT_ID"], "scope": GRAPH_SCOPES})
    if "device_code" not in result:
        logger.warning("device code start failed: %s %s", result.get("error"), result.get("error_description"))
        return _response(502, {"error": "Microsoft sign-in could not start. Try again in a moment."})

    login_id = secrets.token_urlsafe(24)
    expires_in = int(result.get("expires_in", 900))
    # The device_code stays server-side; the browser only gets an opaque login_id tied to its session.
    _table.put_item(Item={"pk": f"LOGIN#{_hash(login_id)}", "sk": "META", "device_code": result["device_code"],
                          "login_owner": key, "ttl": int(time.time()) + expires_in, "created_at": _now()})
    verify = result.get("verification_uri", DEFAULT_VERIFY_URI)
    if not verify.startswith(("https://microsoft.com/", "https://login.microsoft.com/",
                              "https://www.microsoft.com/")):
        verify = DEFAULT_VERIFY_URI
    return _response(200, {"login_id": login_id, "user_code": result["user_code"], "verification_uri": verify,
                           "interval": int(result.get("interval", 5)), "expires_in": expires_in})


def device_poll(event: dict[str, Any]) -> dict[str, Any]:
    key, session = _session(event)
    login_id = str(_body(event).get("login_id") or "")
    login_key = {"pk": f"LOGIN#{_hash(login_id)}", "sk": "META"}
    login = _table.get_item(Key=login_key).get("Item") if login_id else None
    if not login or int(login.get("ttl", 0)) < time.time() or login.get("login_owner") != key:
        return _response(410, {"status": "expired", "error": "This sign-in code has expired. Start again."})

    result = _entra("token", {"client_id": os.environ["ENTRA_CLIENT_ID"], "device_code": login["device_code"],
                              "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    error = result.get("error")
    if error in ("authorization_pending", "slow_down"):
        return _response(200, {"status": "pending", "slow_down": error == "slow_down"})
    _table.delete_item(Key=login_key)
    if "access_token" not in result:
        logger.warning("device sign-in failed: %s %s", error, (result.get("error_description") or "")[:200])
        message = {"authorization_declined": "Sign-in was declined.",
                   "expired_token": "This sign-in code has expired. Start again."}.get(
            error, "Microsoft sign-in failed. Check that 'Allow public client flows' is enabled on the app.")
        return _response(401 if error else 502, {"status": "failed", "error": message})

    me = _graph_me(result["access_token"])
    user = {"user_id": me["id"], "user_email": me.get("mail") or me.get("userPrincipalName") or "",
            "user_name": me.get("displayName") or ""}
    _table.put_item(Item={**session, **user, "refresh_token": result["refresh_token"],
                          "ms_expires": int(time.time()) + MS_HOURS * 3600, **_session_key(key)})
    logger.info("outlook connected for %s", user["user_id"])
    return _response(200, {"status": "done", "user": {"name": user["user_name"], "email": user["user_email"]}})


def disconnect(event: dict[str, Any]) -> dict[str, Any]:
    key, session = _session(event)
    _disconnect(key, session)
    return _response(200, {"microsoft": None})


# ---------------------------------------------------------------- runs

def _mark_failed(run_id: str, error: str) -> None:
    _table.update_item(
        Key={"pk": f"RUN#{run_id}", "sk": "META"},
        UpdateExpression="SET #s = :s, #e = :e, phase = :p, updated_at = :t",
        ExpressionAttributeNames={"#s": "status", "#e": "error"},
        ExpressionAttributeValues={":s": "FAILED", ":e": error, ":p": "done", ":t": _now()},
    )


def _dynamics_documents(option: Any) -> list[dict[str, Any]]:
    """Open Dynamics 365 opportunities -> CRM-style documents, so they are consolidated like an uploaded CRM export."""
    limit = option.get("limit", 25) if isinstance(option, dict) else 25
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_DYNAMICS_OPPORTUNITIES:
        raise HttpError(400, f"dynamics.limit must be 1 to {MAX_DYNAMICS_OPPORTUNITIES}")
    try:
        rows = dynamics.search_opportunities("", limit, "open")
    except dynamics.DynamicsError as exc:
        raise HttpError(502, f"Could not read Dynamics 365: {exc}") from exc
    labels = (("Opportunity", "name"), ("Customer", "customer"), ("Description", "description"),
              ("Estimated value", "estimated_value"), ("Estimated close date", "close_date"),
              ("Request type", "request_type"), ("Workload", "workload"), ("Dynamics record", "url"))
    documents = []
    for row in rows:
        text = "\n".join(f"{label}: {row[key]}" for label, key in labels if row.get(key) not in (None, ""))
        documents.append({"source": "crm", "title": row.get("name") or "Dynamics opportunity", "text": text,
                          "file_name": "Dynamics 365"})
    return documents


def start_run(event: dict[str, Any]) -> dict[str, Any]:
    key, session = _session(event)
    body = _body(event)
    outlook = body.get("outlook")
    days = None
    if outlook:
        if not isinstance(outlook, dict) or outlook.get("days") not in ALLOWED_DAYS:
            return _response(400, {"error": "outlook.days must be 1, 7 or 30"})
        if not session.get("refresh_token"):
            return _response(409, {"error": "Connect Outlook first, or untick it.", "microsoft": None})
        days = outlook["days"]
    try:
        documents, inputs = uploads.parse_inputs(body.get("crm_files"), body.get("notes_files"), body.get("notes_text"))
    except uploads.UploadError as exc:
        return _response(400, {"error": str(exc)})
    if body.get("dynamics"):
        crm_docs = _dynamics_documents(body["dynamics"])
        documents += crm_docs
        inputs.append({"source": "crm", "name": "Dynamics 365 (open opportunities)", "type": "dynamics",
                       "records": len(crm_docs), "truncated": False})
    if not documents and not days:
        return _response(400, {"error": "Add at least one source: Outlook, Dynamics 365, CRM records or seller notes."})

    _limit_ip("runs", event, RUNS_PER_IP_HOUR)
    _limit("runs-day", datetime.now(timezone.utc).strftime("%Y%m%d"), RUNS_PER_DAY, 2 * 86400)
    graph_token = _graph_token(key, session) if days else None

    run_id = uuid.uuid4().hex
    created_at = _now()
    microsoft = _microsoft(session) or {}
    _table.put_item(Item={
        "pk": f"RUN#{run_id}", "sk": "META", "run_id": run_id, "owner": key,
        "user_email": microsoft.get("email", ""), "user_name": microsoft.get("name", ""),
        "days": days, "inputs": inputs, "status": "QUEUED", "phase": "queued", "counts": {},
        "created_at": created_at, "updated_at": created_at,
    })
    payload = {"run_id": run_id, "documents": documents,
               "submitted_by": microsoft.get("email") or "Intake UI user"}
    if graph_token:
        payload.update(graph_token=graph_token, days=days)
    try:
        resp = _agentcore.invoke_agent_runtime(
            agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
            runtimeSessionId=f"intake-run-{run_id}",   # one session per run (>= 33 chars)
            contentType="application/json", accept="application/json",
            payload=json.dumps(payload).encode(),
        )
        result = json.loads(resp["response"].read() or b"{}")
    except Exception as exc:
        logger.exception("run %s: could not reach the agent", run_id)
        result = {"ok": False, "error": f"Could not reach the Intake agent: {type(exc).__name__}"}
    if not result.get("ok"):
        error = str(result.get("error", "Intake agent did not respond"))[:1000]
        _mark_failed(run_id, error)
        return _response(502, {"run_id": run_id, "status": "FAILED", "error": error})

    logger.info("run %s started: outlook=%s, %d documents", run_id, days, len(documents))
    return _response(202, {"run_id": run_id, "status": "RUNNING", "inputs": inputs, "days": days})


def _cursor(value: str) -> dict[str, Any] | None:
    try:
        key = json.loads(base64.urlsafe_b64decode(value.encode()))
        return key if isinstance(key, dict) and set(key) <= {"pk", "sk", "owner", "created_at"} else None
    except (ValueError, TypeError):
        return None


def list_runs(event: dict[str, Any]) -> dict[str, Any]:
    """This browser's previous runs, newest first (DynamoDB index owner + created_at), HISTORY_PAGE at a time."""
    key, _ = _session(event)
    kwargs: dict[str, Any] = {"IndexName": OWNER_INDEX, "KeyConditionExpression": Key("owner").eq(key),
                              "ScanIndexForward": False, "Limit": HISTORY_PAGE}
    cursor = ((event.get("queryStringParameters") or {}).get("cursor") or "")[:1000]
    if cursor:
        start = _cursor(cursor)
        if not start or start.get("owner") != key:
            return _response(400, {"error": "bad cursor"})
        kwargs["ExclusiveStartKey"] = start
    page = _table.query(**kwargs)
    runs = []
    for m in page["Items"]:
        c = m.get("counts") or {}
        runs.append({"run_id": m["run_id"], "status": m.get("status"), "created_at": m.get("created_at"),
                     "finished_at": m.get("finished_at"), "days": m.get("days"),
                     "user_email": m.get("user_email") or "",
                     "inputs": [{"source": i.get("source"), "name": i.get("name"), "records": i.get("records")}
                                for i in m.get("inputs", [])],
                     "counts": {k: c.get(k) for k in ("emails", "crm_rows", "notes", "opportunities", "matched",
                                                      "unmatched", "failed") if k in c}})
    last = page.get("LastEvaluatedKey")
    next_cursor = base64.urlsafe_b64encode(json.dumps(last, default=_json_default).encode()).decode() if last else None
    return _response(200, {"runs": runs, "next_cursor": next_cursor})


def get_run(event: dict[str, Any]) -> dict[str, Any]:
    key, _ = _session(event)
    run_id = (event.get("pathParameters") or {}).get("id", "")
    meta = _table.get_item(Key={"pk": f"RUN#{run_id}", "sk": "META"}).get("Item") if run_id else None
    # Only the session that started a run can read it; don't reveal whether someone else's exists.
    if meta is None or meta.get("owner") != key:
        return _response(404, {"error": "run not found"})
    items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"RUN#{run_id}")}
    while True:
        page = _table.query(**kwargs)
        items += [i for i in page["Items"] if i["sk"].startswith(("OPP#", "LOG#"))]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    opportunities = [{"thread_key": i["thread_key"], "source": i["source"], "sources": i.get("sources", [i["source"]]),
                      "source_types": i.get("source_types", [i["source"]["type"]]), "matched": i.get("matched", False),
                      "missing_sources": i.get("missing_sources", []),
                      "dynamics_id": (i.get("dynamics") or {}).get("id"), "dynamics_url": (i.get("dynamics") or {}).get("url"),
                      "dynamics_action": (i.get("dynamics") or {}).get("action"),
                      "dynamics_reason": (i.get("dynamics") or {}).get("reason"), **i["intake"]}
                     for i in items if i["sk"].startswith("OPP#")]
    # Matched first, then by number of linked documents, then newest.
    opportunities.sort(key=lambda o: o["source"].get("received_at") or "", reverse=True)
    opportunities.sort(key=lambda o: (not o["matched"], -len(o["sources"])))
    logs = [{"at": i["at"], "step": i["step"], "level": i["level"], "message": i["message"]}
            for i in items if i["sk"].startswith("LOG#")][-MAX_LOGS_RETURNED:]

    return _response(200, {
        "run_id": run_id, "status": meta["status"], "phase": meta.get("phase"), "days": meta.get("days"),
        "inputs": meta.get("inputs", []), "sources_included": meta.get("sources_included", []),
        "counts": meta.get("counts", {}), "truncated": meta.get("truncated", False),
        "outlook_error": meta.get("outlook_error"), "error": meta.get("error"),
        "created_at": meta["created_at"], "finished_at": meta.get("finished_at"),
        "opportunities": opportunities, "logs": logs,
    })


ROUTES = {
    "POST /session": create_session,
    "GET /session": get_session,
    "POST /auth/device/start": device_start,
    "POST /auth/device/poll": device_poll,
    "POST /auth/disconnect": disconnect,
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
    except Unauthorized as exc:
        return _response(401, {"error": str(exc)})
    except HttpError as exc:
        return _response(exc.status, {"error": str(exc), **exc.extra})
    except Exception:
        logger.exception("unhandled error on %s", event.get("routeKey"))
        return _response(500, {"error": "internal error"})


# Funding check flow (api/funding_api.py). Imported last: it uses this module's session and quota helpers.
import funding_api  # noqa: E402

ROUTES.update(funding_api.ROUTES)
