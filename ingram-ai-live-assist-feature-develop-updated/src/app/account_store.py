"""Accounts (customer companies) and their opportunities, stored in the timeline table.

    session_id = "account#<account_id>", sk = "meta"          the account
    session_id = "account#<account_id>", sk = "opp#<opp_id>"  one opportunity

Unlike call data these items have no TTL: they are names, not call content.
Accounts are listed through the ``calls-by-created`` index with ``gsi1pk = "account"``.
Ids are generated here, so they always match ``events.ENTITY_ID_RE``.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.timeline_store import CALLS_INDEX, CALLS_INDEX_PK, DEFAULT_TIMELINE_TABLE, plain

ACCOUNT_PK_PREFIX = "account#"
OPPORTUNITY_SK_PREFIX = "opp#"
_ACCOUNTS_INDEX_VALUE = "account"
MAX_NAME_CHARS = 100
MAX_OPPORTUNITIES_LISTED = 100


class AccountNotFoundError(KeyError):
    """Raised when an account or opportunity does not exist."""


def new_entity_id(name: str) -> str:
    """A readable, unique id: the name's words plus 6 random hex characters."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    slug = "-".join(words)[:30].strip("-")
    suffix = secrets.token_hex(3)
    return f"{slug}-{suffix}" if slug else suffix


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pk(account_id: str) -> str:
    return f"{ACCOUNT_PK_PREFIX}{account_id}"


class AccountStore:
    def __init__(
        self,
        table_name: str = DEFAULT_TIMELINE_TABLE,
        region_name: str | None = None,
        resource: Any | None = None,
    ) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)

    # --- accounts -----------------------------------------------------------------

    def create_account(self, name: str) -> dict:
        account_id = new_entity_id(name)
        item = {
            "session_id": _pk(account_id), "sk": "meta", "kind": "account",
            "account_id": account_id, "name": name, "created_at": _now_iso(),
            CALLS_INDEX_PK: _ACCOUNTS_INDEX_VALUE,
        }
        self._put_new(item)
        return _account(item)

    def get_account(self, account_id: str) -> dict | None:
        item = self._table.get_item(Key={"session_id": _pk(account_id), "sk": "meta"}).get("Item")
        return _account(plain(item)) if item else None

    def require_account(self, account_id: str) -> dict:
        account = self.get_account(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    def list_accounts(self, limit: int = 20, cursor: dict | None = None) -> tuple[list[dict], dict | None]:
        """Accounts, newest first, and the cursor for the next page."""
        kwargs: dict[str, Any] = {
            "IndexName": CALLS_INDEX,
            "KeyConditionExpression": Key(CALLS_INDEX_PK).eq(_ACCOUNTS_INDEX_VALUE),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        response = self._table.query(**kwargs)
        return [_account(plain(i)) for i in response.get("Items", [])], response.get("LastEvaluatedKey")

    # --- opportunities ------------------------------------------------------------

    def create_opportunity(self, account_id: str, name: str, opportunity_type: str | None = None) -> dict:
        self.require_account(account_id)
        opportunity_id = new_entity_id(name)
        item = {
            "session_id": _pk(account_id), "sk": f"{OPPORTUNITY_SK_PREFIX}{opportunity_id}",
            "kind": "opportunity", "account_id": account_id, "opportunity_id": opportunity_id,
            "name": name, "opportunity_type": opportunity_type, "created_at": _now_iso(),
        }
        self._put_new(item)
        return _opportunity(item)

    def get_opportunity(self, account_id: str, opportunity_id: str) -> dict | None:
        item = self._table.get_item(
            Key={"session_id": _pk(account_id), "sk": f"{OPPORTUNITY_SK_PREFIX}{opportunity_id}"}
        ).get("Item")
        return _opportunity(plain(item)) if item else None

    def list_opportunities(self, account_id: str) -> list[dict]:
        response = self._table.query(
            KeyConditionExpression=Key("session_id").eq(_pk(account_id))
            & Key("sk").begins_with(OPPORTUNITY_SK_PREFIX),
            Limit=MAX_OPPORTUNITIES_LISTED,
        )
        items = [_opportunity(plain(i)) for i in response.get("Items", [])]
        return sorted(items, key=lambda o: o["created_at"], reverse=True)

    # --- forget -------------------------------------------------------------------

    def mark_forgotten(self, account_id: str, opportunity_id: str | None = None) -> str:
        """Record that memory was erased now. Earlier calls before this time stop feeding new calls."""
        now = _now_iso()
        sk = f"{OPPORTUNITY_SK_PREFIX}{opportunity_id}" if opportunity_id else "meta"
        self._table.update_item(
            Key={"session_id": _pk(account_id), "sk": sk},
            UpdateExpression="SET memory_forgotten_at = :now",
            ConditionExpression="attribute_exists(sk)",
            ExpressionAttributeValues={":now": now},
        )
        return now

    def forgotten_at(self, account_id: str, opportunity_id: str | None = None) -> str | None:
        """Latest erase that applies to this scope: the account's, or the opportunity's if later."""
        times = [(self._raw(account_id, "meta") or {}).get("memory_forgotten_at")]
        if opportunity_id:
            times.append((self._raw(account_id, f"{OPPORTUNITY_SK_PREFIX}{opportunity_id}") or {})
                         .get("memory_forgotten_at"))
        return max((t for t in times if t), default=None)

    def _raw(self, account_id: str, sk: str) -> dict | None:
        return self._table.get_item(Key={"session_id": _pk(account_id), "sk": sk}).get("Item")

    def _put_new(self, item: dict) -> None:
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            # A random-suffix collision; vanishingly rare, so surface it rather than loop.
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise RuntimeError("generated id already exists; retry") from None
            raise


def _account(item: dict) -> dict:
    return {k: item.get(k) for k in ("account_id", "name", "created_at", "memory_forgotten_at")}


def _opportunity(item: dict) -> dict:
    return {k: item.get(k) for k in (
        "opportunity_id", "account_id", "name", "opportunity_type", "created_at", "memory_forgotten_at")}
