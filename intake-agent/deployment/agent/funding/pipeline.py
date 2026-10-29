"""One funding check: fetch the opportunity from Dynamics, classify the facts, apply the decision framework.

    1. fetch     the opportunity and its form answers from Dynamics 365 (or the demo store)
    2. facts     form answers first; Claude reads the free text for anything still unknown (quotes verified)
    3. decide    funding/rules.py, deterministic, with the path that led to the decision
    4. record    the result in DynamoDB (FUNDCHK#<id>), the opportunity's shared latest decision
                 (FUNDLATEST#<opportunity id>, read by the Funding Desk) and a note on the Dynamics opportunity

Called by main.py in a background thread; the UI polls GET /funding/checks/{id}.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

import dynamics
import opportunity_form
from funding import facts as facts_mod
from funding.rules import decide

logger = logging.getLogger("funding")
MIN_TEXT_FOR_MODEL = 40
MAX_TEXT_CHARS = 30_000
PROGRAM_TEXT = {"decided": "Funding determined"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dynamo(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


class CheckStore:
    def __init__(self, check_id: str, table=None) -> None:
        self.key = {"pk": f"FUNDCHK#{check_id}", "sk": "META"}
        self._table = table or boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])

    def _set(self, **fields: Any) -> None:
        fields["updated_at"] = _now()
        self._table.update_item(
            Key=self.key, UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": _dynamo(v) for k, v in fields.items()})

    def log(self, step: str, message: str, level: str = "INFO") -> None:
        self._table.update_item(
            Key=self.key, UpdateExpression="SET logs = list_append(if_not_exists(logs, :e), :l)",
            ExpressionAttributeValues={":e": [], ":l": [{"at": _now(), "step": step, "level": level,
                                                         "message": message[:1000]}]})

    def running(self) -> None:
        self._set(status="RUNNING", started_at=_now())

    def done(self, result: dict[str, Any]) -> None:
        self._set(status="COMPLETED", result=result, finished_at=_now())

    def fail(self, error: str, opportunity_id: str = "") -> None:
        self._set(status="FAILED", error=error[:1000], finished_at=_now())
        self.log("error", error, "ERROR")
        if opportunity_id:
            self._table.update_item(
                Key=latest_key(opportunity_id), UpdateExpression="SET last_error = :e REMOVE pending",
                ExpressionAttributeValues={":e": _dynamo({"check_id": self.key["pk"][8:], "error": error[:500], "at": _now()})})

    def shared_latest(self, opportunity_id: str, result: dict[str, Any], checked_by: str) -> None:
        """The opportunity's latest decision for everyone (Funding Desk); clears the running marker."""
        d = result["decision"]
        # owner + created_at put every checked opportunity in the owner-created index, newest check first, so other
        # agents (Live Assist) can list them without a scan. The value cannot clash with a session hash.
        self._table.update_item(
            Key=latest_key(opportunity_id),
            UpdateExpression="SET latest = :l, #r = :r, #o = :o, created_at = :t REMOVE pending, last_error",
            ExpressionAttributeNames={"#r": "result", "#o": "owner"},
            ExpressionAttributeValues={":o": LATEST_INDEX_OWNER, ":t": result["checked_at"], ":r": _dynamo(result), ":l": _dynamo({
                "check_id": result["check_id"], "status": d["status"], "program": d["program"], "funding": d["funding"],
                "total_usd": d["total_usd"], "arr_usd": d["arr_usd"], "at": result["checked_at"], "by": checked_by,
                "assumed": [a["fact"] for a in d.get("assumptions", [])]})})

    def latest(self, opportunity_id: str, owner: str, check_id: str, decision: dict[str, Any]) -> None:
        """Latest decision on the opportunity's list entry (FUNDOPP#, created by the API)."""
        self._table.update_item(
            Key={"pk": f"FUNDOPP#{opportunity_id}", "sk": owner},
            UpdateExpression="SET latest = :l",
            ExpressionAttributeValues={":l": _dynamo({
                "check_id": check_id, "status": decision["status"], "program": decision["program"],
                "funding": decision["funding"], "total_usd": decision["total_usd"], "at": _now()})})


LATEST_INDEX_OWNER = "FUNDING#LATEST"   # index key of FUNDLATEST# items (owner-created index)


def latest_key(opportunity_id: str) -> dict[str, str]:
    return {"pk": f"FUNDLATEST#{opportunity_id}", "sk": "META"}


def opportunity_text(opp: dict[str, Any]) -> str:
    """The free text Claude reads: name, account, description, the free-text form answers (details, drivers,
    remarks) and the notes. Structured answers (choices, numbers) are not sent: they come from the columns."""
    parts = [f"Opportunity name: {opp.get('name', '')}"]
    if opp.get("customer"):
        parts.append(f"Customer account: {opp['customer']}")
    if opp.get("description"):
        parts.append(f"Description:\n{opp['description']}")
    free = {k: v for k, v in (opp.get("answers") or {}).items()
            if k in opportunity_form.FIELDS and opportunity_form.FIELDS[k]["type"] in ("textarea", "text")
            and isinstance(v, str) and len(v) > 25}
    if free:
        parts.append("Free-text form answers:\n" + opportunity_form.answers_text(free))
    for n in opp.get("notes") or []:
        parts.append(f"Note \"{n.get('subject', '')}\":\n{n.get('text', '')}")
    return "\n\n".join(parts)[:MAX_TEXT_CHARS]


def _settled_by_form(form_facts: dict[str, Any]) -> bool:
    """True when the form answers alone settle the decision: no best-fit assumption had to be made, so there is
    nothing left for Claude to read out of the opportunity text."""
    return not decide(facts_mod.merge(form_facts, {})[0])["assumptions"]


def decision_note(result: dict[str, Any]) -> tuple[str, str]:
    d = result["decision"]
    subject = d["program"] or PROGRAM_TEXT[d["status"]]
    lines = [f"{d['framework']} — {PROGRAM_TEXT[d['status']]}", "", d["rationale"], f"Program: {d['program'] or '-'}",
             f"Funding: {d['funding']}"]
    if d.get("total_usd") is not None:
        lines.append(f"Estimated funding: {d['total_usd']:,} USD")
    lines += ["", "Decision path:"] + [f"  {p['step']}: {p['value']}" for p in d["path"]]
    if d.get("assumptions"):
        lines += ["", "Best-fit assumptions (confirm with the seller):"] + [
            f"  - {a['fact'].replace('_', ' ').capitalize()}: assumed {a['assumed']}. {a['question']}" for a in d["assumptions"]]
    if d["notes"] or result["conflicts"]:
        lines += ["", "Notes:"] + [f"  - {n}" for n in d["notes"] + result["conflicts"]]
    lines += ["", f"Checked {result['checked_at']} by the Ingram funding agent ({result['check_id']})."]
    return subject, "\n".join(lines)


def run_funding_check(store: CheckStore, check_id: str, opportunity_id: str, owner: str = "",
                      checked_by: str = "") -> dict[str, Any]:
    store.running()
    where = "Dynamics 365" if dynamics.mode() == "dynamics" else "the demo store (Dynamics not connected yet)"
    store.log("fetch", f"Fetching opportunity {opportunity_id} from {where}")
    opp = dynamics.get_opportunity(opportunity_id)
    store.log("fetch", f"Fetched '{opp['name'][:120]}' with {len(opp.get('answers') or {})} form answers")

    labels = {fid: f["label"] for fid, f in opportunity_form.FIELDS.items()}
    from_columns = opp.get("answers_from") == "columns"
    columns = {fid: c["column"] for fid, c in opportunity_form.COLUMN_OF.items()} if from_columns else {}
    form_facts = facts_mod.from_form(opp.get("answers") or {}, labels, columns)
    if opp.get("answers"):
        store.log("fetch", f"{len(form_facts)} funding facts from the intake form answers "
                           f"({'Dynamics ingr_ columns' if from_columns else 'answers note'})")
    text = opportunity_text(opp)
    agent_facts, summary, model_error = {}, "", None
    if _settled_by_form(form_facts):
        store.log("facts", "The intake form columns answer everything the framework needs")
    elif len(text) >= MIN_TEXT_FOR_MODEL:
        open_facts = [facts_mod.FACT_LABEL[f].lower() for f in facts_mod.FACTS if f not in form_facts]
        store.log("facts", "Processing the opportunity text for: " + ", ".join(open_facts))
        try:
            bedrock = boto3.client("bedrock-runtime", config=Config(retries={"mode": "adaptive", "max_attempts": 6},
                                                                    read_timeout=120))
            agent_facts, summary = facts_mod.from_text(text, bedrock, os.environ["BEDROCK_MODEL_ID"])
            store.log("facts", f"Found {len(agent_facts)} facts in the opportunity text "
                               f"({sum(f['verified'] for f in agent_facts.values())} with verified quotes)")
        except Exception as exc:   # the form answers alone can still decide
            logger.exception("fact classification failed")
            model_error = f"{type(exc).__name__}: {exc}"[:300]
            store.log("facts", f"Could not classify the text ({model_error}); using the form answers only", "WARN")

    facts, rows, conflicts = facts_mod.merge(form_facts, agent_facts)
    decision = decide(facts)
    store.log("decide", f"{PROGRAM_TEXT[decision['status']]}: {decision['rationale']}")
    result = {"check_id": check_id, "checked_at": _now(), "summary": summary, "facts": rows, "conflicts": conflicts,
              "decision": decision, "model_error": model_error,
              "opportunity": {"id": opp["id"], "name": opp["name"], "customer": opp.get("customer")
                              or (opp.get("answers") or {}).get("ec_name", ""), "source": opp["source"], "url": opp.get("url")}}

    if os.environ.get("DYNAMICS_WRITE_BACK", "true").lower() == "true":
        try:
            dynamics.write_decision(opportunity_id, *decision_note(result))
            result["written_back"] = True
            store.log("record", "Funding decision added as a note on the opportunity")
        except Exception as exc:
            logger.exception("write-back failed")
            result["written_back"] = False
            store.log("record", f"Could not add the decision note to the opportunity: {exc}", "WARN")
    store.done(result)
    store.shared_latest(opportunity_id, result, checked_by)
    if owner:
        store.latest(opportunity_id, owner, check_id, decision)
    return result
