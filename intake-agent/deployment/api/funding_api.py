"""Funding check API: opportunity form -> Microsoft Dynamics 365 -> funding agent (AgentCore Runtime).

Every route needs the browser session (Authorization: Bearer <session>) with Microsoft connected, because it
writes to Dynamics; records are owned by the Microsoft user (not the browser), so they follow the user.

    GET  /funding/config                       -> {mode: dynamics | demo, dynamics_host}
    POST /funding/opportunities {answers, check?}
                                               -> 201 {opportunity, check?}  creates the Dynamics opportunity
    GET  /funding/opportunities[?cursor=]      -> {opportunities, next_cursor}  created or checked by this user
    GET  /funding/dynamics[?search=&state=]    -> {mode, opportunities}  recent opportunities in Dynamics (or the demo
                                                  store), each with its latest decision (any user) and `running`
    GET  /funding/dynamics/{id}                -> {opportunity (with form answers, notes), latest, result, running}
    POST /funding/checks {opportunity_id}      -> 202 {check_id}   the agent fetches it from Dynamics and decides
    POST /funding/checks/batch {opportunity_ids}  -> 202 {checks}  up to 25, one agent invocation
    POST /funding/checks/status {check_ids}    -> {checks}  live status of up to 25 checks: stage, agent log, decision
    POST /funding/clear {opportunity_ids}      -> {cleared}  reset the desk's latest decisions (Dynamics notes stay)
    GET  /funding/checks/{id}                  -> {status, result, logs, error}
    POST /funding/demo/seed                    -> {seeded}  demo mode only: sample opportunities, one per framework branch

The Funding Desk (second Amplify app) uses the /funding/dynamics routes; funding.html uses the rest.

DynamoDB (same table as the Intake runs):
    FUNDOPP#<opportunity id> / <owner>   the user's list entry (index owner-created), `latest` decision
    FUNDCHK#<check id>       / META      one funding check (status, result, logs); readable by its owner only
    FUNDLATEST#<opportunity id> / META   latest decision for everyone (written by the agent), `pending` while running
    DYNOPP#<opportunity id>  / META      demo store only (dynamics.py), when DYNAMICS_URL is not set
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from boto3.dynamodb.conditions import Key

import dynamics
import handler as core          # sessions, quotas, responses and the AgentCore client live in handler.py
import opportunity_form

logger = logging.getLogger()
PAGE = 20
CHECKS_PER_USER_HOUR = int(os.environ.get("FUNDING_CHECKS_PER_USER_HOUR", "300"))
CHECKS_PER_DAY = int(os.environ.get("FUNDING_CHECKS_PER_DAY", "1500"))
MAX_BATCH = 25
PENDING_STALE_S = 15 * 60   # a check still "running" after this is treated as lost
CREATES_PER_USER_HOUR = int(os.environ.get("FUNDING_CREATES_PER_USER_HOUR", "30"))


def _user(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    _, session = core._session(event)
    if not session.get("user_id"):
        raise core.HttpError(403, "Connect Microsoft first: funding checks write to Dynamics 365 on your behalf.",
                             microsoft=None)
    return f"MSUSER#{session['user_id']}", session


def _limit_user(kind: str, owner: str, limit: int, n: int = 1) -> None:
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    _limit_n(f"{kind}#{core._hash(owner)[:24]}", hour, limit, 2 * 3600, n)


def _limit_n(name: str, window: str, limit: int, ttl_seconds: int, n: int) -> None:
    """Like handler._limit, counting n hits at once (batch checks)."""
    item = core._table.update_item(
        Key={"pk": f"QUOTA#{name}#{window}", "sk": "META"},
        UpdateExpression="ADD hits :n SET #t = if_not_exists(#t, :ttl)", ExpressionAttributeNames={"#t": "ttl"},
        ExpressionAttributeValues={":n": n, ":ttl": int(time.time()) + ttl_seconds}, ReturnValues="UPDATED_NEW")["Attributes"]
    if int(item["hits"]) > limit:
        raise core.HttpError(429, "Too many funding checks. Please wait a while and try again.")


def _dynamics_error(exc: dynamics.DynamicsError) -> dict[str, Any]:
    return core._response(exc.status if exc.status in (400, 404) else 502, {"error": str(exc)})


def _entry_key(opportunity_id: str, owner: str) -> dict[str, str]:
    return {"pk": f"FUNDOPP#{opportunity_id}", "sk": owner}


def _upsert_entry(owner: str, session: dict[str, Any], opp: dict[str, Any]) -> None:
    core._table.update_item(
        Key=_entry_key(opp["id"], owner),
        UpdateExpression="SET #o = :o, created_at = if_not_exists(created_at, :t), opportunity_id = :i, #n = :n, "
                         "customer = :c, #s = :s, #u = :u, request_type = :r, user_email = :e, updated_at = :t",
        ExpressionAttributeNames={"#o": "owner", "#n": "name", "#s": "source", "#u": "url"},
        ExpressionAttributeValues={":o": owner, ":t": core._now(), ":i": opp["id"], ":n": opp.get("name", "")[:300],
                                   ":c": opp.get("customer", "")[:300], ":s": opp["source"], ":u": opp.get("url") or "",
                                   ":r": opp.get("request_type", ""), ":e": session.get("user_email", "")})


def get_config(event: dict[str, Any]) -> dict[str, Any]:
    core._session(event)
    host = urlparse(os.environ.get("DYNAMICS_URL", "")).hostname or ""
    return core._response(200, {"mode": dynamics.mode(), "flavour": dynamics.flavour(), "dynamics_host": host})


def _start_checks(owner: str, session: dict[str, Any], opportunity_ids: list[str]) -> list[dict[str, Any]]:
    """Queue one check per opportunity and start them all with one agent invocation (funding_batch)."""
    n = len(opportunity_ids)
    _limit_user("fundcheck", owner, CHECKS_PER_USER_HOUR, n)
    _limit_n("fundcheck-day", datetime.now(timezone.utc).strftime("%Y%m%d"), CHECKS_PER_DAY, 2 * 86400, n)
    now = core._now()
    checks = [{"check_id": uuid.uuid4().hex, "opportunity_id": o} for o in opportunity_ids]
    for c in checks:
        core._table.put_item(Item={"pk": f"FUNDCHK#{c['check_id']}", "sk": "META", "check_id": c["check_id"],
                                   "check_owner": owner, "opportunity_id": c["opportunity_id"], "status": "QUEUED",
                                   "created_at": now, "logs": []})
        core._table.update_item(Key=_latest_key(c["opportunity_id"]), UpdateExpression="SET pending = :p",
                                ExpressionAttributeValues={":p": {"check_id": c["check_id"], "at": now,
                                                                  "epoch": int(time.time())}})
    payload = {"action": "funding_batch", "checks": checks, "owner": owner,
               "checked_by": session.get("user_email") or session.get("user_name") or ""}
    try:
        resp = core._agentcore.invoke_agent_runtime(
            agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"], runtimeSessionId=f"funding-check-{checks[0]['check_id']}",
            contentType="application/json", accept="application/json", payload=json.dumps(payload).encode())
        result = json.loads(resp["response"].read() or b"{}")
    except Exception as exc:
        logger.exception("checks %s: could not reach the agent", checks[0]["check_id"])
        result = {"ok": False, "error": f"Could not reach the funding agent: {type(exc).__name__}"}
    if not result.get("ok"):
        error = str(result.get("error", "Funding agent did not respond"))[:1000]
        for c in checks:
            core._table.update_item(Key={"pk": f"FUNDCHK#{c['check_id']}", "sk": "META"},
                                    UpdateExpression="SET #s = :s, #e = :e",
                                    ExpressionAttributeNames={"#s": "status", "#e": "error"},
                                    ExpressionAttributeValues={":s": "FAILED", ":e": error})
            core._table.update_item(Key=_latest_key(c["opportunity_id"]), UpdateExpression="REMOVE pending")
        raise core.HttpError(502, error)
    return [{**c, "status": "RUNNING"} for c in checks]


def _start_check(owner: str, opportunity_id: str, session: dict[str, Any] | None = None) -> dict[str, Any]:
    c = _start_checks(owner, session or {}, [opportunity_id])[0]
    return {"check_id": c["check_id"], "status": "RUNNING"}


def _latest_key(opportunity_id: str) -> dict[str, str]:
    return {"pk": f"FUNDLATEST#{opportunity_id}", "sk": "META"}


def _running(item: dict[str, Any] | None) -> bool:
    pending = (item or {}).get("pending")
    return bool(pending) and int(pending.get("epoch", 0)) > time.time() - PENDING_STALE_S


def _started_at(body: dict[str, Any]) -> str:
    """When the user opened the form (the UI sends it), as Microsoft Forms records 'Start time'."""
    value = str(body.get("started_at") or "")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""
    except ValueError:
        return ""


def create_opportunity(event: dict[str, Any]) -> dict[str, Any]:
    owner, session = _user(event)
    body = core._body(event)
    try:
        answers = opportunity_form.validate(body.get("answers"))
    except opportunity_form.FormError as exc:
        return core._response(400, {"error": "Check the highlighted answers.", "fields": exc.errors})
    _limit_user("fundcreate", owner, CREATES_PER_USER_HOUR)

    arr = opportunity_form.parse_amount(answers.get("fund_arr_usd")) or opportunity_form.parse_amount(answers.get("estimated_arr"))
    mrr = opportunity_form.parse_amount(answers.get("fund_mrr_usd"))
    fields = {"name": opportunity_form.title_of(answers), "customer": answers.get("ec_name", ""),
              "description": opportunity_form.description_of(answers),
              "estimated_value": mrr * 12 if mrr else arr}
    try:
        now = core._now()[:19] + "Z"
        meta = {"_form_response_id": uuid.uuid4().hex[:12], "_form_start_time": _started_at(body) or now,
                "_form_completion_time": now, "_submitted_by": session.get("user_email", ""),
                "_submitted_name": session.get("user_name", "")}
        created = dynamics.create_opportunity(fields, {**answers, **{k: v for k, v in meta.items() if v}})
    except dynamics.DynamicsError as exc:
        return _dynamics_error(exc)
    opp = {"id": created["id"], "url": created["url"], "source": created["source"], "name": fields["name"],
           "customer": fields["customer"], "request_type": answers.get("request_type", "")}
    _upsert_entry(owner, session, opp)
    logger.info("funding opportunity %s created in %s by %s", opp["id"], opp["source"], session.get("user_id"))
    out: dict[str, Any] = {"opportunity": opp}
    if body.get("check"):
        try:
            out["check"] = _start_check(owner, opp["id"], session)
        except core.HttpError as exc:   # the opportunity exists; report the check problem separately
            out["check_error"] = str(exc)
    return core._response(201, out)


def list_opportunities(event: dict[str, Any]) -> dict[str, Any]:
    owner, _ = _user(event)
    kwargs: dict[str, Any] = {"IndexName": core.OWNER_INDEX, "KeyConditionExpression": Key("owner").eq(owner),
                              "ScanIndexForward": False, "Limit": PAGE}
    cursor = ((event.get("queryStringParameters") or {}).get("cursor") or "")[:1000]
    if cursor:
        start = core._cursor(cursor)
        if not start or start.get("owner") != owner:
            return core._response(400, {"error": "bad cursor"})
        kwargs["ExclusiveStartKey"] = start
    page = core._table.query(**kwargs)
    keys = [{"pk": i["pk"], "sk": i["sk"]} for i in page["Items"] if i["pk"].startswith("FUNDOPP#")]
    items: list[dict[str, Any]] = []
    if keys:
        got = core._table.meta.client.batch_get_item(RequestItems={core._table.name: {"Keys": keys}})
        items = got["Responses"].get(core._table.name, [])
    order = {k["pk"]: n for n, k in enumerate(keys)}
    items.sort(key=lambda i: order.get(i["pk"], 0))
    opportunities = [{"id": i["opportunity_id"], "name": i.get("name", ""), "customer": i.get("customer", ""),
                      "source": i.get("source"), "url": i.get("url") or None, "request_type": i.get("request_type", ""),
                      "created_at": i.get("created_at"), "latest": i.get("latest")} for i in items]
    last = page.get("LastEvaluatedKey")
    next_cursor = (core.base64.urlsafe_b64encode(json.dumps(last, default=core._json_default).encode()).decode()
                   if last else None)
    return core._response(200, {"opportunities": opportunities, "next_cursor": next_cursor})


def _latest_items(ids: list[str], full: bool = False) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    names = {"#l": "latest", "#p": "pending", "#e": "last_error", "#k": "pk"}
    for i in range(0, len(ids), 100):
        request: dict[str, Any] = {"Keys": [_latest_key(x) for x in ids[i:i + 100]]}
        if not full:
            request.update(ProjectionExpression="#k, #l, #p, #e", ExpressionAttributeNames=names)
        got = core._table.meta.client.batch_get_item(RequestItems={core._table.name: request})
        for item in got["Responses"].get(core._table.name, []):
            out[item["pk"].split("#", 1)[1]] = item
    return out


def search_dynamics(event: dict[str, Any]) -> dict[str, Any]:
    _user(event)
    q = event.get("queryStringParameters") or {}
    state = q.get("state") if q.get("state") in ("open", "won", "lost", "all") else "all"
    try:
        opportunities = dynamics.search_opportunities(q.get("search") or "", 100, state)
    except dynamics.DynamicsError as exc:
        return _dynamics_error(exc)
    latest = _latest_items([o["id"] for o in opportunities])
    for o in opportunities:
        item = latest.get(o["id"]) or {}
        o.update(latest=item.get("latest"), running=_running(item), last_error=item.get("last_error"))
    return core._response(200, {"mode": dynamics.mode(), "flavour": dynamics.flavour(), "opportunities": opportunities})


def get_dynamics(event: dict[str, Any]) -> dict[str, Any]:
    _user(event)
    opportunity_id = (event.get("pathParameters") or {}).get("id", "")
    try:
        opp = dynamics.get_opportunity(opportunity_id)
    except dynamics.DynamicsError as exc:
        return _dynamics_error(exc)
    item = _latest_items([opportunity_id], full=True).get(opportunity_id) or {}
    return core._response(200, {"mode": dynamics.mode(), "opportunity": opp, "latest": item.get("latest"),
                                "result": item.get("result"), "running": _running(item),
                                "last_error": item.get("last_error")})


def start_batch(event: dict[str, Any]) -> dict[str, Any]:
    owner, session = _user(event)
    ids = core._body(event).get("opportunity_ids")
    if not isinstance(ids, list) or not ids:
        return core._response(400, {"error": "opportunity_ids must be a non-empty list"})
    ids = list(dict.fromkeys(str(i).strip() for i in ids))
    if len(ids) > MAX_BATCH:
        return core._response(400, {"error": f"At most {MAX_BATCH} opportunities per batch."})
    if not all(dynamics.is_guid(i) for i in ids):
        return core._response(400, {"error": "Every opportunity ID must be a Dynamics opportunity ID (GUID)."})
    latest = _latest_items(ids)
    todo = [i for i in ids if not _running(latest.get(i))]   # skip the ones already being checked
    checks = _start_checks(owner, session, todo) if todo else []
    return core._response(202, {"checks": checks, "skipped_running": len(ids) - len(todo)})


def _stage(item: dict[str, Any]) -> str:
    """What the agent is doing now, from its last log line (agent/funding/pipeline.py)."""
    if item.get("status") in ("COMPLETED", "FAILED"):
        return "Done" if item["status"] == "COMPLETED" else "Failed"
    if item.get("status") == "QUEUED":
        return "Queued"
    logs = item.get("logs") or []
    if not logs:
        return "Starting"
    step, message = logs[-1]["step"], str(logs[-1]["message"])
    if step == "fetch":
        return "Fetching from Dynamics" if message.startswith("Fetching") else "Reading form columns"
    if step == "facts":
        return "Processing" if message.startswith(("Processing", "Claude is reading")) else "Applying the framework"
    return "Recording the decision"


def check_status(event: dict[str, Any]) -> dict[str, Any]:
    """Live view of a batch: one call for up to 25 checks (the desk polls this every second while a run is on)."""
    owner, _ = _user(event)
    ids = core._body(event).get("check_ids")
    if not isinstance(ids, list) or not 0 < len(ids) <= MAX_BATCH or not all(isinstance(i, str) and len(i) == 32 for i in ids):
        return core._response(400, {"error": f"check_ids must be a list of 1-{MAX_BATCH} check IDs"})
    got = core._table.meta.client.batch_get_item(RequestItems={core._table.name: {
        "Keys": [{"pk": f"FUNDCHK#{i}", "sk": "META"} for i in dict.fromkeys(ids)],
        "ProjectionExpression": "check_id, check_owner, opportunity_id, #s, logs, #e, #r.decision, finished_at",
        "ExpressionAttributeNames": {"#s": "status", "#e": "error", "#r": "result"}}})
    out = []
    for item in got["Responses"].get(core._table.name, []):
        if item.get("check_owner") != owner:
            continue
        d = (item.get("result") or {}).get("decision") or {}
        out.append({"check_id": item["check_id"], "opportunity_id": item["opportunity_id"], "status": item["status"],
                    "stage": _stage(item), "error": item.get("error"), "finished_at": item.get("finished_at"),
                    "logs": [{"at": l["at"], "step": l["step"], "level": l["level"], "message": l["message"]}
                             for l in (item.get("logs") or [])],
                    "decision": {k: d.get(k) for k in ("status", "program", "funding", "total_usd")} if d else None})
    return core._response(200, {"checks": out})


def clear_results(event: dict[str, Any]) -> dict[str, Any]:
    """Reset the latest decision of these opportunities on the desk. Check history (FUNDCHK#) and the decision notes
    in Dynamics are kept; opportunities with a check still running are left alone."""
    _user(event)
    ids = core._body(event).get("opportunity_ids")
    if not isinstance(ids, list) or not 0 < len(ids) <= 100 or not all(dynamics.is_guid(str(i)) for i in ids):
        return core._response(400, {"error": "opportunity_ids must be a list of 1-100 Dynamics opportunity IDs"})
    latest = _latest_items([str(i) for i in ids])
    cleared = skipped = 0
    for i in dict.fromkeys(str(x) for x in ids):
        if _running(latest.get(i)):
            skipped += 1
            continue
        core._table.delete_item(Key=_latest_key(i))
        cleared += 1
    return core._response(200, {"cleared": cleared, "skipped_running": skipped})


def seed_demo(event: dict[str, Any]) -> dict[str, Any]:
    _user(event)
    if dynamics.mode() != "demo":
        return core._response(409, {"error": "Dynamics 365 is connected: sample data is only for the demo store."})
    import demo_seed

    records = demo_seed.sample_records()
    base = time.time()
    for n, (opp_id, fields, answers) in enumerate(records):
        created = datetime.fromtimestamp(base - n * 3600 * 7, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        dynamics.demo_put(opp_id, fields, answers, created)
        core._table.delete_item(Key=_latest_key(opp_id))   # reset: the samples start unchecked
    return core._response(200, {"seeded": len(records)})


def start_check(event: dict[str, Any]) -> dict[str, Any]:
    owner, session = _user(event)
    opportunity_id = str(core._body(event).get("opportunity_id") or "").strip()
    if not dynamics.is_guid(opportunity_id):
        return core._response(400, {"error": "opportunity_id must be a Dynamics opportunity ID (GUID)."})
    try:
        opp = dynamics.get_opportunity(opportunity_id)   # confirms it exists and that we can read it
    except dynamics.DynamicsError as exc:
        return _dynamics_error(exc)
    _upsert_entry(owner, session, {**opp, "request_type": (opp.get("answers") or {}).get("request_type", "")})
    return core._response(202, {"opportunity_id": opportunity_id, **_start_check(owner, opportunity_id, session)})


def get_check(event: dict[str, Any]) -> dict[str, Any]:
    owner, _ = _user(event)
    check_id = (event.get("pathParameters") or {}).get("id", "")
    item = core._table.get_item(Key={"pk": f"FUNDCHK#{check_id}", "sk": "META"}).get("Item") if check_id else None
    if not item or item.get("check_owner") != owner:
        return core._response(404, {"error": "check not found"})
    return core._response(200, {"check_id": check_id, "opportunity_id": item["opportunity_id"], "status": item["status"],
                                "created_at": item["created_at"], "finished_at": item.get("finished_at"),
                                "error": item.get("error"), "result": item.get("result"),
                                "logs": item.get("logs", [])[-30:]})


ROUTES = {
    "GET /funding/config": get_config,
    "POST /funding/opportunities": create_opportunity,
    "GET /funding/opportunities": list_opportunities,
    "GET /funding/dynamics": search_dynamics,
    "GET /funding/dynamics/{id}": get_dynamics,
    "POST /funding/checks/batch": start_batch,
    "POST /funding/checks/status": check_status,
    "POST /funding/clear": clear_results,
    "POST /funding/demo/seed": seed_demo,
    "POST /funding/checks": start_check,
    "GET /funding/checks/{id}": get_check,
}
