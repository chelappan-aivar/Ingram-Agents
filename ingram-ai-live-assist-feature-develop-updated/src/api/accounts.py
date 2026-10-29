"""Account, opportunity and long-term memory routes (see docs/api.md).

TODO(auth): any holder of the shared access key can read and erase every
account's memory. Scope these routes per user once the org auth service lands.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from api.http import ApiError, Request, Response, decode_cursor, encode_cursor, parse_limit
from app.account_store import MAX_NAME_CHARS, AccountStore
from app.call_memory import previous_calls
from app.events import valid_entity_id
from app.guidance import ID_PATTERN
from app.memory import LongTermMemory, MemoryScope, account_delete_prefixes
from app.timeline_store import TimelineStore
from app.views import call_row

ACCOUNT_ID = r"(?P<account_id>[a-z0-9-]{1,40})"
MEMORY_FACTS_LISTED = 50

logger = logging.getLogger(__name__)


class AccountRoutes:
    def __init__(
        self,
        *,
        accounts: AccountStore | None,
        timeline: TimelineStore,
        memory: LongTermMemory | None,
    ) -> None:
        self.accounts = accounts
        self.timeline = timeline
        self.memory = memory

    def routes(self) -> list[tuple[str, str, Callable]]:
        base = rf"/accounts/{ACCOUNT_ID}"
        return [
            ("POST", r"/accounts", self.create_account),
            ("GET", r"/accounts", self.list_accounts),
            ("GET", base, self.get_account),
            ("POST", base + "/opportunities", self.create_opportunity),
            ("GET", base + "/opportunities", self.list_opportunities),
            ("GET", base + "/calls", self.list_calls),
            ("GET", base + "/memory", self.get_memory),
            ("DELETE", base + "/memory", self.delete_memory),
        ]

    # --- accounts and opportunities ---------------------------------------------------

    def create_account(self, req: Request) -> Response:
        name = _name(req.json())
        return Response(201, self._store().create_account(name))

    def list_accounts(self, req: Request) -> Response:
        items, next_key = self._store().list_accounts(
            limit=parse_limit(req.query), cursor=decode_cursor(req.query.get("cursor"))
        )
        return Response(200, {"accounts": items, "next_cursor": encode_cursor(next_key)})

    def get_account(self, req: Request, account_id: str) -> Response:
        account = self._account(account_id)
        return Response(200, {**account, "opportunities": self._store().list_opportunities(account_id)})

    def create_opportunity(self, req: Request, account_id: str) -> Response:
        self._account(account_id)
        body = req.json()
        name = _name(body)
        opportunity_type = body.get("opportunity_type")
        # Checked against the call's config when a call starts (profiles can differ).
        if opportunity_type is not None and (
            not isinstance(opportunity_type, str) or not re.match(ID_PATTERN, opportunity_type)
        ):
            raise ApiError(400, "invalid_request",
                           "'opportunity_type' must be a type id (e.g. cloud_migration) or null")
        return Response(201, self._store().create_opportunity(account_id, name, opportunity_type))

    def list_opportunities(self, req: Request, account_id: str) -> Response:
        self._account(account_id)
        return Response(200, {"opportunities": self._store().list_opportunities(account_id)})

    def list_calls(self, req: Request, account_id: str) -> Response:
        self._account(account_id)
        opportunity_id = self._opportunity_param(req, account_id)
        items, next_key = self.timeline.list_account_calls(
            account_id, opportunity_id=opportunity_id,
            limit=parse_limit(req.query), cursor=decode_cursor(req.query.get("cursor")),
        )
        return Response(200, {"calls": [call_row(m) for m in items], "next_cursor": encode_cursor(next_key)})

    # --- memory ---------------------------------------------------------------------

    def get_memory(self, req: Request, account_id: str) -> Response:
        self._account(account_id)
        scope = MemoryScope(account_id, self._opportunity_param(req, account_id))
        forgotten_at = self._store().forgotten_at(account_id, scope.opportunity_id)
        facts: list[dict[str, Any]] = []
        if self.memory is not None:
            try:
                facts = [f.to_dict() for f in self.memory.list_facts(scope, MEMORY_FACTS_LISTED)]
            except Exception:  # noqa: BLE001
                logger.exception("memory read failed account=%r", account_id)
                raise ApiError(502, "memory_unavailable", "Long-term memory did not answer") from None
        return Response(200, {
            "enabled": self.memory is not None,
            "account_id": account_id,
            "opportunity_id": scope.opportunity_id,
            "account": [f for f in facts if f["level"] == "account"],
            "opportunity": [f for f in facts if f["level"] == "opportunity"],
            "forgotten_at": forgotten_at,
            "previous_calls": previous_calls("", scope, self.timeline, since=forgotten_at),
        })

    def delete_memory(self, req: Request, account_id: str) -> Response:
        """Forget: erase remembered facts and stop feeding earlier calls' minutes into new calls.

        Past calls keep their own record (transcript, minutes, what they were told at
        the start) as call history. Calls already running keep what they loaded.
        """
        self._account(account_id)
        opportunity_id = self._opportunity_param(req, account_id)
        deleted = 0
        if self.memory is not None:
            try:
                deleted = self.memory.delete(account_delete_prefixes(account_id, opportunity_id))
            except Exception:  # noqa: BLE001
                logger.exception("memory delete failed account=%r", account_id)
                raise ApiError(502, "memory_unavailable", "Long-term memory did not answer") from None
        # Marked after the facts are gone, so a failed delete can simply be retried.
        forgotten_at = self._store().mark_forgotten(account_id, opportunity_id)
        # Audit: who is unknown until per-user auth exists (shared key).
        logger.info("memory erased account=%r opportunity=%r records=%d", account_id, opportunity_id, deleted)
        return Response(200, {"deleted": deleted, "account_id": account_id, "opportunity_id": opportunity_id,
                              "forgotten_at": forgotten_at})

    # --- used by POST /calls ---------------------------------------------------------------

    def call_scope(self, body: dict) -> dict[str, str]:
        """Validate ``account_id``/``opportunity_id`` for a new call; returns session.start fields."""
        account_id, opportunity_id = body.get("account_id"), body.get("opportunity_id")
        if account_id is None:
            if opportunity_id is not None:
                raise ApiError(400, "invalid_request", "'opportunity_id' needs 'account_id'")
            return {}
        if not valid_entity_id(account_id):
            raise ApiError(400, "invalid_request", "'account_id' is malformed")
        self._account(account_id, status=400)
        fields = {"account_id": account_id}
        if opportunity_id is not None:
            if not valid_entity_id(opportunity_id):
                raise ApiError(400, "invalid_request", "'opportunity_id' is malformed")
            opportunity = self._store().get_opportunity(account_id, opportunity_id)
            if opportunity is None:
                raise ApiError(400, "unknown_opportunity", "No such opportunity for this account")
            fields["opportunity_id"] = opportunity_id
            if opportunity.get("opportunity_type"):
                fields["opportunity_type"] = opportunity["opportunity_type"]
        return fields

    # --- helpers ---------------------------------------------------------------------

    def _store(self) -> AccountStore:
        if self.accounts is None:
            raise ApiError(500, "accounts_not_configured", "Account storage is not configured")
        return self.accounts

    def _account(self, account_id: str, status: int = 404) -> dict:
        account = self._store().get_account(account_id) if valid_entity_id(account_id) else None
        if account is None:
            raise ApiError(status, "unknown_account" if status == 400 else "account_not_found", "No such account")
        return account

    def _opportunity_param(self, req: Request, account_id: str) -> str | None:
        opportunity_id = req.query.get("opportunity_id")
        if opportunity_id is None:
            return None
        if not valid_entity_id(opportunity_id) or self._store().get_opportunity(account_id, opportunity_id) is None:
            raise ApiError(404, "opportunity_not_found", "No such opportunity for this account")
        return opportunity_id


def _name(body: dict) -> str:
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME_CHARS:
        raise ApiError(400, "invalid_request", f"'name' must be 1-{MAX_NAME_CHARS} characters")
    return " ".join(name.split())
