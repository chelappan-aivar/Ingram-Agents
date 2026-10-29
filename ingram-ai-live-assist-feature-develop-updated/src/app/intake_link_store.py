"""Call history map: which Live Assist calls were held for which Intake Agent opportunity.

Stored in the timeline table, next to accounts and opportunities:

    session_id = "intake#<intake opportunity id>", sk = "link"
        the Live Assist account and opportunity that stand for it (created on first use; no TTL, names only),
        call_count, last_call_at, last_session_id
    session_id = "intake#<intake opportunity id>", sk = "call#<created_at>#<session_id>"
        one call: session id, title, source, when, and the funding decision the opportunity had at that time
        (expires with the call data, DATA_RETENTION_DAYS)
    session_id = "intake#customer#<slug>", sk = "link"
        the Live Assist account used for this Intake customer, so one customer keeps one account and memory
    session_id = "intake#<intake opportunity id>", sk = "discovery#<started_at>#<run_id>"
        a Discovery agent run for this opportunity: engagement, run id, the calls it read, and once it finished a
        short summary of its report (no TTL: a small pointer to the Discovery agent's own record)

Linking a call to the Live Assist account/opportunity also turns on the existing memory across calls.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.account_store import AccountStore
from app.timeline_store import DEFAULT_TIMELINE_TABLE, _expires_at, plain

PK_PREFIX = "intake#"
LINK_SK = "link"
CALL_SK_PREFIX = "call#"
DISCOVERY_SK_PREFIX = "discovery#"
MAX_DISCOVERY_LISTED = 20
MAX_CALLS_LISTED = 100
SNAPSHOT_FIELDS = ("status", "program", "funding", "total_usd")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _customer_slug(name: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", name.lower()))[:60] or "unknown"


class IntakeLinkStore:
    def __init__(self, accounts: AccountStore, table_name: str = DEFAULT_TIMELINE_TABLE,
                 region_name: str | None = None, resource: Any | None = None) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)
        self._accounts = accounts

    # --- link: Intake opportunity -> Live Assist account + opportunity -----------------------

    def get_link(self, intake_id: str) -> dict | None:
        item = self._table.get_item(Key={"session_id": f"{PK_PREFIX}{intake_id}", "sk": LINK_SK}).get("Item")
        return _link(plain(item)) if item else None

    def get_links(self, intake_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        client = self._table.meta.client
        for i in range(0, len(intake_ids), 100):
            keys = [{"session_id": f"{PK_PREFIX}{x}", "sk": LINK_SK} for x in intake_ids[i:i + 100]]
            got = client.batch_get_item(RequestItems={self._table.name: {"Keys": keys}})
            for item in got.get("Responses", {}).get(self._table.name, []):
                link = _link(plain(item))
                out[link["intake_opportunity_id"]] = link
        return out

    def ensure_link(self, intake_id: str, name: str, customer: str) -> dict:
        """The Live Assist account/opportunity for this Intake opportunity, created the first time."""
        existing = self.get_link(intake_id)
        if existing:
            return existing
        account_id = self._account_for(customer or name)
        opportunity = self._accounts.create_opportunity(account_id, name[:100] or "Opportunity")
        item = {"session_id": f"{PK_PREFIX}{intake_id}", "sk": LINK_SK, "intake_opportunity_id": intake_id,
                "account_id": account_id, "opportunity_id": opportunity["opportunity_id"], "name": name[:300],
                "customer": customer[:300], "created_at": _now_iso(), "call_count": 0}
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self.get_link(intake_id)  # Another request linked it first; use theirs.
        return _link(item)

    def _account_for(self, customer: str) -> str:
        key = {"session_id": f"{PK_PREFIX}customer#{_customer_slug(customer)}", "sk": LINK_SK}
        item = self._table.get_item(Key=key).get("Item")
        if item:
            return item["account_id"]
        account = self._accounts.create_account(" ".join(customer.split())[:100] or "Customer")
        try:
            self._table.put_item(Item={**key, "account_id": account["account_id"], "name": customer[:300],
                                       "created_at": _now_iso()}, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self._table.get_item(Key=key)["Item"]["account_id"]
        return account["account_id"]

    # --- call history map -----------------------------------------------------------------

    def record_call(self, intake_id: str, meta: dict, decision: dict | None) -> dict:
        """Add a call to the opportunity's history. Idempotent per call (same sort key)."""
        created_at = meta.get("created_at") or _now_iso()
        entry = {"session_id": f"{PK_PREFIX}{intake_id}", "sk": f"{CALL_SK_PREFIX}{created_at}#{meta['session_id']}",
                 "call_session_id": meta["session_id"], "title": meta.get("title"), "source": meta.get("source"),
                 "created_at": created_at, "expires_at": _expires_at(),
                 "decision": {k: (decision or {}).get(k) for k in SNAPSHOT_FIELDS} if decision else None}
        try:
            self._table.put_item(Item=entry, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return _call(entry)  # Already recorded.
            raise
        self._table.update_item(
            Key={"session_id": f"{PK_PREFIX}{intake_id}", "sk": LINK_SK},
            UpdateExpression="ADD call_count :one SET last_call_at = :t, last_session_id = :s",
            ExpressionAttributeValues={":one": 1, ":t": created_at, ":s": meta["session_id"]})
        return _call(entry)

    def list_calls(self, intake_id: str) -> list[dict]:
        """The opportunity's calls, newest first."""
        response = self._table.query(
            KeyConditionExpression=Key("session_id").eq(f"{PK_PREFIX}{intake_id}") & Key("sk").begins_with(CALL_SK_PREFIX),
            ScanIndexForward=False, Limit=MAX_CALLS_LISTED)
        return [_call(plain(i)) for i in response.get("Items", [])]


    # --- Discovery agent runs -------------------------------------------------------------

    def add_discovery_run(self, intake_id: str, engagement: str, run_id: str, started_at: str,
                          call_ids: list[str]) -> dict:
        """Register a Discovery run for the opportunity. Idempotent per run id."""
        item = {"session_id": f"{PK_PREFIX}{intake_id}", "sk": f"{DISCOVERY_SK_PREFIX}{started_at}#{run_id}",
                "engagement": engagement, "run_id": run_id, "started_at": started_at,
                "call_ids": call_ids[:MAX_CALLS_LISTED], "state": "starting"}
        existing = self._find_discovery(intake_id, run_id)
        if existing:
            return _discovery(existing)
        self._table.put_item(Item=item)
        return _discovery(item)

    def list_discovery_runs(self, intake_id: str) -> list[dict]:
        response = self._table.query(
            KeyConditionExpression=Key("session_id").eq(f"{PK_PREFIX}{intake_id}")
            & Key("sk").begins_with(DISCOVERY_SK_PREFIX), ScanIndexForward=False, Limit=MAX_DISCOVERY_LISTED)
        return [{**_discovery(plain(i)), "sk": i["sk"]} for i in response.get("Items", [])]

    def update_discovery_run(self, intake_id: str, sk: str, **fields: Any) -> None:
        self._table.update_item(
            Key={"session_id": f"{PK_PREFIX}{intake_id}", "sk": sk},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": _dynamo(v) for k, v in fields.items()})

    def _find_discovery(self, intake_id: str, run_id: str) -> dict | None:
        return next((r for r in self.list_discovery_runs(intake_id) if r["run_id"] == run_id), None)


def _dynamo(value: Any) -> Any:
    """DynamoDB takes Decimal, not float."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def _discovery(item: dict) -> dict:
    return {k: item.get(k) for k in ("engagement", "run_id", "started_at", "call_ids", "state", "steps_done",
                                      "error", "finished_at", "summary")}


def _link(item: dict) -> dict:
    return {k: item.get(k) for k in ("intake_opportunity_id", "account_id", "opportunity_id", "name", "customer",
                                      "created_at", "last_call_at", "last_session_id")} | \
        {"call_count": int(item.get("call_count") or 0)}


def _call(item: dict) -> dict:
    return {"session_id": item.get("call_session_id"), "title": item.get("title"), "source": item.get("source"),
            "created_at": item.get("created_at"), "decision": item.get("decision")}
