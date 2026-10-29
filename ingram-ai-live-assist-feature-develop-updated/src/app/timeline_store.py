"""DynamoDB timeline: call metadata, every turn and every agent run.

Table: partition key ``session_id`` (S), sort key ``sk`` (S), TTL on ``expires_at``.

    sk = "meta"          status, source, title, assist_config, next_seq, timestamps;
                         also in the ``calls-by-created`` index (gsi1pk = "call")
    sk = "turn#000017"   one turn; seq assigned on arrival
    sk = "run#000017"    one agent run, keyed by the first seq it processed
    sk = "minutes"       minutes of meeting and final summary, written after the call ends

Accounts and opportunities share the table under ``account#<id>`` (see
``app/account_store.py``). Calls of an account are in ``calls-by-account``.

The entry point writes ``meta`` and turns. The background worker writes runs and minutes.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app.assist_config import AssistConfig
from app.guidance import BUILTIN_PROFILE_ID, GuidanceSet
from app.events import STATUS_ENDED, STATUS_LIVE, SessionStart, Turn
from app.session_store import SessionNotFoundError

DEFAULT_TIMELINE_TABLE = os.environ.get("TIMELINE_TABLE_NAME", "ingram-live-assist-timeline")
# How long call data is kept (TTL on every call item). Set by deploy.sh from config.
DATA_RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "180"))
TTL_SECONDS = DATA_RETENTION_DAYS * 24 * 3600

META_SK = "meta"
MINUTES_SK = "minutes"
PREQUESTIONS_SK = "prequestions"
TURN_PREFIX = "turn#"
RUN_PREFIX = "run#"

PREQUESTIONS_GENERATING = "generating"
PREQUESTIONS_OK = "ok"
PREQUESTIONS_ERROR = "error"
PREQUESTIONS_STALE_SECONDS = 10 * 60  # A "generating" claim older than this belongs to a crashed attempt.

MINUTES_GENERATING = "generating"
MINUTES_OK = "ok"
MINUTES_EMPTY = "empty"  # The call had no turns.
MINUTES_ERROR = "error"
# A "generating" claim older than this belongs to a crashed attempt and may be retaken.
MINUTES_STALE_SECONDS = 15 * 60
# Sources that own a call's turns: other senders (manual lines) are rejected.
EXCLUSIVE_SOURCES = ("recording", "transcribe", "meeting")
CALLS_INDEX = "calls-by-created"
CALLS_INDEX_PK = "gsi1pk"
# Calls of one account, newest first: gsi2pk = "account#<id>", sort key created_at.
ACCOUNT_CALLS_INDEX = "calls-by-account"
ACCOUNT_CALLS_INDEX_PK = "gsi2pk"
_CALLS_INDEX_VALUE = "call"
_SEQ_WIDTH = 6
_MAX_SEQ = 10**_SEQ_WIDTH - 1


class SessionEndedError(Exception):
    """Raised when an event targets a call that has already ended."""


class SourceMismatchError(Exception):
    """Raised when a turn comes from a source other than the one that owns the call."""


class SessionNotEndedError(Exception):
    """Raised when an action needs an ended call, such as regenerating minutes."""


def turn_sk(seq: int) -> str:
    return f"{TURN_PREFIX}{seq:0{_SEQ_WIDTH}d}"


def run_sk(first_seq: int) -> str:
    return f"{RUN_PREFIX}{first_seq:0{_SEQ_WIDTH}d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_at() -> int:
    return int(time.time()) + TTL_SECONDS


def _iso_seconds_ago(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() - seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plain(value: Any) -> Any:
    """Convert DynamoDB Decimals back to int or float, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def _dynamo_safe(value: Any) -> Any:
    """Convert floats to Decimal, which the DynamoDB resource API requires."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dynamo_safe(v) for v in value]
    return value


def _is_condition_failure(error: ClientError) -> bool:
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


class TimelineStore:
    """Reads and writes the timeline table. The table must already exist
    (see ``scripts/create_timeline_table.py``)."""

    def __init__(
        self,
        table_name: str = DEFAULT_TIMELINE_TABLE,
        region_name: str | None = None,
        resource: Any | None = None,
    ) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)

    # --- meta -----------------------------------------------------------------

    def get_meta(self, session_id: str) -> dict | None:
        item = self._table.get_item(Key={"session_id": session_id, "sk": META_SK}).get("Item")
        return plain(item) if item else None

    def start_session(self, event: SessionStart, guidance: GuidanceSet | None = None) -> tuple[dict, bool]:
        """Create the call. Idempotent: a repeat returns the existing meta and False.

        ``guidance``: the call's checklists. A custom profile is stored as a snapshot so
        later edits don't change the call; the built-in one is referenced by id only.
        """
        now = _now_iso()
        item = {
            "session_id": event.session_id,
            "sk": META_SK,
            "status": STATUS_LIVE,
            "source": event.source,
            "title": event.title,
            "assist_config": event.assist_config.to_dict(),
            "next_seq": 0,
            "created_at": now,
            "updated_at": now,
            "expires_at": _expires_at(),
            CALLS_INDEX_PK: _CALLS_INDEX_VALUE,
        }
        if event.source in EXCLUSIVE_SOURCES:
            item["exclusive_source"] = event.source
        if event.account_id:
            item["account_id"] = event.account_id
            item[ACCOUNT_CALLS_INDEX_PK] = f"account#{event.account_id}"
        if event.opportunity_id:
            item["opportunity_id"] = event.opportunity_id
        if event.description:
            item["description"] = event.description
        if event.preset_opportunity_type:
            item["preset_opportunity_type"] = event.preset_opportunity_type
        if guidance is not None:
            item["config_id"], item["config_version"] = guidance.profile_id, guidance.version
            if guidance.profile_id != BUILTIN_PROFILE_ID:
                item["guidance"] = guidance.to_snapshot()
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            return self.get_meta(event.session_id) or item, False
        return plain(item), True

    def update_session(
        self,
        session_id: str,
        *,
        assist_config: AssistConfig | None = None,
        status: str | None = None,
    ) -> dict:
        """Change settings and/or the live/paused status.

        Raises:
            SessionNotFoundError: The call does not exist.
            SessionEndedError: The call has ended.
        """
        sets = ["updated_at = :now"]
        values: dict[str, Any] = {":now": _now_iso(), ":ended": STATUS_ENDED}
        if assist_config is not None:
            sets.append("assist_config = :cfg")
            values[":cfg"] = assist_config.to_dict()
        if status is not None:
            sets.append("#st = :status")
            values[":status"] = status
        try:
            response = self._table.update_item(
                Key={"session_id": session_id, "sk": META_SK},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression="attribute_exists(sk) AND #st <> :ended",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            self._raise_for_missing_or_ended(session_id)
        return plain(response["Attributes"])

    def end_session(self, session_id: str, reason: str | None) -> tuple[dict, bool]:
        """Mark the call ended. Idempotent: a repeat returns the meta and False.

        Raises:
            SessionNotFoundError: The call does not exist.
        """
        now = _now_iso()
        try:
            response = self._table.update_item(
                Key={"session_id": session_id, "sk": META_SK},
                UpdateExpression="SET #st = :ended, ended_at = :now, end_reason = :reason, updated_at = :now",
                ConditionExpression="attribute_exists(sk) AND #st <> :ended",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":ended": STATUS_ENDED, ":now": now, ":reason": reason},
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            meta = self.get_meta(session_id)
            if meta is None:
                raise SessionNotFoundError(session_id) from None
            return meta, False
        return plain(response["Attributes"]), True

    def set_meeting_status(self, session_id: str, platform: str, bot: str) -> None:
        """Record what the meeting bot is doing, for the console. Does nothing if the call is gone.

        Only the platform and a status word are stored, never credentials.
        """
        try:
            self._table.update_item(
                Key={"session_id": session_id, "sk": META_SK},
                UpdateExpression="SET #m = :m",
                ConditionExpression="attribute_exists(sk)",
                ExpressionAttributeNames={"#m": "meeting"},
                ExpressionAttributeValues={":m": {"platform": platform, "bot": bot}},
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise

    # --- turns ----------------------------------------------------------------

    def append_turn(self, turn: Turn) -> int:
        """Store a turn and return its seq (1-based, in arrival order).

        Raises:
            SessionNotFoundError: The call does not exist.
            SessionEndedError: The call has ended.
            SourceMismatchError: The call's turns come from another source.
        """
        try:
            response = self._table.update_item(
                Key={"session_id": turn.session_id, "sk": META_SK},
                UpdateExpression="ADD next_seq :one SET updated_at = :now",
                ConditionExpression=(
                    "attribute_exists(sk) AND #st <> :ended AND next_seq < :max "
                    "AND (attribute_not_exists(exclusive_source) OR exclusive_source = :src)"
                ),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":one": 1, ":now": _now_iso(), ":ended": STATUS_ENDED, ":max": _MAX_SEQ,
                    ":src": turn.source,
                },
                ReturnValues="UPDATED_NEW",
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            self._raise_for_missing_or_ended(turn.session_id, source=turn.source)

        seq = int(response["Attributes"]["next_seq"])
        self._table.put_item(Item={
            "session_id": turn.session_id,
            "sk": turn_sk(seq),
            "seq": seq,
            "speaker": turn.speaker,
            "speaker_name": turn.speaker_name,
            "text": turn.text,
            "spoken_at": turn.spoken_at,
            "received_at": _now_iso(),
            "source": turn.source,
            "event_id": turn.event_id,
            "expires_at": _expires_at(),
        })
        return seq

    def pending_turns(self, session_id: str, after_seq: int, limit: int) -> list[dict]:
        """Return up to ``limit`` turns with seq greater than ``after_seq``, oldest first."""
        response = self._table.query(
            KeyConditionExpression=Key("session_id").eq(session_id)
            & Key("sk").between(turn_sk(after_seq + 1), turn_sk(_MAX_SEQ)),
            Limit=limit,
            ConsistentRead=True,
        )
        return [plain(i) for i in response.get("Items", [])]

    # --- runs -----------------------------------------------------------------

    def put_run(self, session_id: str, first_seq: int, record: dict) -> None:
        """Write a run record. A retry of the same batch overwrites the earlier record."""
        self._table.put_item(Item=_dynamo_safe({
            **record,
            "session_id": session_id,
            "sk": run_sk(first_seq),
            "expires_at": _expires_at(),
        }))

    # --- pre-questions --------------------------------------------------------

    def claim_prequestions(self, session_id: str) -> bool:
        """Mark the pre-questions as generating. Returns False if another attempt owns them or they exist.

        Claimable when absent, failed, or stale (a crashed attempt), so each call is prepared at most once.
        """
        try:
            self._table.update_item(
                Key={"session_id": session_id, "sk": PREQUESTIONS_SK},
                UpdateExpression="SET #st = :generating, started_at = :now, expires_at = :exp",
                ConditionExpression=(
                    "attribute_not_exists(sk) OR #st = :error OR (#st = :generating AND started_at < :stale)"
                ),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":generating": PREQUESTIONS_GENERATING, ":error": PREQUESTIONS_ERROR, ":now": _now_iso(),
                    ":stale": _iso_seconds_ago(PREQUESTIONS_STALE_SECONDS), ":exp": _expires_at(),
                },
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            return False
        return True

    def put_prequestions(self, session_id: str, record: dict) -> None:
        """Write the result of a pre-questions attempt (status ok or error)."""
        self._table.put_item(Item=_dynamo_safe({
            **record,
            "session_id": session_id,
            "sk": PREQUESTIONS_SK,
            "expires_at": _expires_at(),
        }))

    def get_prequestions(self, session_id: str) -> dict | None:
        item = self._table.get_item(
            Key={"session_id": session_id, "sk": PREQUESTIONS_SK}, ConsistentRead=True
        ).get("Item")
        return plain(item) if item else None

    # --- minutes --------------------------------------------------------------

    def claim_minutes(self, session_id: str, *, regenerate: bool = False) -> bool:
        """Mark minutes as generating. Returns False if another attempt owns them.

        Claimable when absent, failed, stale (a crashed attempt), or, with
        ``regenerate``, already done.
        """
        now = _now_iso()
        allowed = [":error"] + ([":ok", ":empty"] if regenerate else [])
        condition = (
            "attribute_not_exists(sk) OR #st IN (" + ", ".join(allowed) + ") "
            "OR (#st = :generating AND started_at < :stale)"
        )
        values: dict[str, Any] = {
            ":error": MINUTES_ERROR, ":generating": MINUTES_GENERATING,
            ":stale": _iso_seconds_ago(MINUTES_STALE_SECONDS),
        }
        if regenerate:
            values.update({":ok": MINUTES_OK, ":empty": MINUTES_EMPTY})
        try:
            self._table.update_item(
                Key={"session_id": session_id, "sk": MINUTES_SK},
                UpdateExpression="SET #st = :generating, started_at = :now, expires_at = :exp",
                ConditionExpression=condition,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={**values, ":now": now, ":exp": _expires_at()},
            )
        except ClientError as e:
            if not _is_condition_failure(e):
                raise
            return False
        return True

    def put_minutes(self, session_id: str, record: dict) -> None:
        """Write the result of a minutes attempt (status ok, empty or error)."""
        self._table.put_item(Item=_dynamo_safe({
            **record,
            "session_id": session_id,
            "sk": MINUTES_SK,
            "expires_at": _expires_at(),
        }))

    def mark_minutes(self, session_id: str, **fields: Any) -> None:
        """Add fields to an existing minutes item, e.g. the long-term memory write status."""
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        self._table.update_item(
            Key={"session_id": session_id, "sk": MINUTES_SK},
            UpdateExpression="SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))),
            ConditionExpression="attribute_exists(sk)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":v{i}": _dynamo_safe(v) for i, v in enumerate(fields.values())},
        )

    def get_minutes(self, session_id: str) -> dict | None:
        item = self._table.get_item(
            Key={"session_id": session_id, "sk": MINUTES_SK}, ConsistentRead=True
        ).get("Item")
        return plain(item) if item else None

    # --- reads ----------------------------------------------------------------

    def items(self, session_id: str) -> list[dict]:
        """Return every item of a call in sort-key order: meta, runs, then turns."""
        items: list[dict] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("session_id").eq(session_id),
            "ConsistentRead": True,
        }
        while True:
            response = self._table.query(**kwargs)
            items += [plain(i) for i in response.get("Items", [])]
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def list_calls(self, limit: int = 20, cursor: dict | None = None) -> tuple[list[dict], dict | None]:
        """Return call metas, newest first, and the cursor for the next page."""
        kwargs: dict[str, Any] = {
            "IndexName": CALLS_INDEX,
            "KeyConditionExpression": Key(CALLS_INDEX_PK).eq(_CALLS_INDEX_VALUE),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        response = self._table.query(**kwargs)
        return [plain(i) for i in response.get("Items", [])], response.get("LastEvaluatedKey")

    def list_account_calls(
        self, account_id: str, *, opportunity_id: str | None = None, limit: int = 20, cursor: dict | None = None,
    ) -> tuple[list[dict], dict | None]:
        """Return the account's call metas, newest first, optionally for one opportunity."""
        kwargs: dict[str, Any] = {
            "IndexName": ACCOUNT_CALLS_INDEX,
            "KeyConditionExpression": Key(ACCOUNT_CALLS_INDEX_PK).eq(f"account#{account_id}"),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if opportunity_id:
            # Filtered after the read; fine while an account has at most a few hundred calls.
            kwargs["FilterExpression"] = Attr("opportunity_id").eq(opportunity_id)
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        response = self._table.query(**kwargs)
        return [plain(i) for i in response.get("Items", [])], response.get("LastEvaluatedKey")

    def _raise_for_missing_or_ended(self, session_id: str, source: str | None = None) -> None:
        meta = self.get_meta(session_id)
        if meta is None:
            raise SessionNotFoundError(session_id)
        if meta.get("status") == STATUS_ENDED:
            raise SessionEndedError(session_id)
        if meta.get("exclusive_source") and source != meta["exclusive_source"]:
            raise SourceMismatchError(session_id)
        raise RuntimeError(f"Unexpected condition failure for session {session_id!r}")


_default_timeline: TimelineStore | None = None


def get_default_timeline() -> TimelineStore:
    """Return the process-wide timeline store, created on first use."""
    global _default_timeline
    if _default_timeline is None:
        _default_timeline = TimelineStore()
    return _default_timeline
