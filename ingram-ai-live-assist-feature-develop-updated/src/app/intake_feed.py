"""Read-only view of the Intake Agent's funding-checked opportunities.

The Intake Agent (separate stack, table ``ingram-intake-runs``) keeps the latest funding decision of every
opportunity it checked as ``pk = FUNDLATEST#<Dynamics opportunity id>``, ``sk = META``. Those items carry
``owner = FUNDING#LATEST`` and ``created_at`` = time of the check, so the table's ``owner-created`` index lists them
newest first. This module only reads: Live Assist never writes to the Intake table.

Set ``INTAKE_TABLE_NAME`` to turn it on; without it the console simply shows no Intake card.
"""

from __future__ import annotations

import re
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from app.timeline_store import plain

INDEX = "owner-created"
INDEX_OWNER = "FUNDING#LATEST"
LATEST_PREFIX = "FUNDLATEST#"
MAX_LISTED = 100
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DECISION_FIELDS = ("status", "program", "funding", "total_usd", "arr_usd", "mrr_usd", "rationale")


def valid_intake_id(value: object) -> bool:
    return isinstance(value, str) and bool(GUID_RE.match(value))


class IntakeFeed:
    def __init__(self, table_name: str, region_name: str | None = None, resource: Any | None = None) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)
        self._client = self._table.meta.client

    def list_checked(self, limit: int = MAX_LISTED) -> list[dict]:
        """Checked opportunities, newest check first. Items whose check result is incomplete are skipped."""
        page = self._table.query(IndexName=INDEX, KeyConditionExpression=Key("owner").eq(INDEX_OWNER),
                                 ScanIndexForward=False, Limit=min(limit, MAX_LISTED))
        keys = [{"pk": i["pk"], "sk": i["sk"]} for i in page.get("Items", []) if str(i.get("pk", "")).startswith(LATEST_PREFIX)]
        if not keys:
            return []
        got = self._client.batch_get_item(RequestItems={self._table.name: {
            "Keys": keys,
            "ProjectionExpression": "pk, created_at, latest, #r.opportunity, #r.decision, #r.summary",
            "ExpressionAttributeNames": {"#r": "result"},
        }})
        items = [plain(i) for i in got.get("Responses", {}).get(self._table.name, [])]
        rows = [r for r in (_row(i) for i in items) if r]
        return sorted(rows, key=lambda r: r["checked_at"] or "", reverse=True)

    def get(self, intake_id: str) -> dict | None:
        """One checked opportunity with its decision path, open questions and the facts used."""
        if not valid_intake_id(intake_id):
            return None
        item = self._table.get_item(Key={"pk": f"{LATEST_PREFIX}{intake_id}", "sk": "META"},
                                    ProjectionExpression="pk, created_at, latest, #r",
                                    ExpressionAttributeNames={"#r": "result"}).get("Item")
        row = _row(plain(item)) if item else None
        if row is None:
            return None
        result = plain(item).get("result") or {}
        decision = result.get("decision") or {}
        # Every opportunity is mapped to a programme now; what the Intake Agent had to assume is what the seller
        # should confirm with the customer. Older results may still carry `missing` questions.
        assumptions = [{"fact": a.get("fact"), "assumed": a.get("assumed"), "question": a.get("question")}
                       for a in decision.get("assumptions") or [] if a.get("question")]
        questions = [m.get("question") for m in decision.get("missing") or [] if m.get("question")]
        questions += [a["question"] for a in assumptions if a["question"] not in questions]
        row["decision"].update(
            components=decision.get("components") or [],
            path=[{"step": p.get("step"), "value": p.get("value")} for p in decision.get("path") or []],
            missing=questions, assumptions=assumptions,
            notes=list(decision.get("notes") or []) + list(result.get("conflicts") or []),
        )
        row["facts"] = [{"label": f.get("label"), "value": f.get("display"), "source": f.get("source")}
                        for f in result.get("facts") or [] if f.get("used")]
        return row


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _row(item: dict) -> dict | None:
    result = item.get("result") or {}
    opp, decision = result.get("opportunity") or {}, result.get("decision") or {}
    intake_id = str(item.get("pk", ""))[len(LATEST_PREFIX):]
    if not decision or not valid_intake_id(intake_id):
        return None
    return {
        "intake_opportunity_id": intake_id,
        "name": opp.get("name") or "Opportunity",
        "customer": opp.get("customer") or "",
        "source": opp.get("source"),
        # Optional: only present when the Intake Agent stored them from the CRM record.
        "seller": _text(opp.get("owner") or opp.get("seller") or opp.get("owner_name")),
        "estimated_value_usd": _number(opp.get("estimated_value") or opp.get("estimatedvalue")),
        "crm_url": _text(opp.get("crm_url") or opp.get("url")),
        "summary": result.get("summary") or "",
        "checked_at": item.get("created_at") or (item.get("latest") or {}).get("at"),
        "decision": {k: decision.get(k) for k in DECISION_FIELDS},
    }
