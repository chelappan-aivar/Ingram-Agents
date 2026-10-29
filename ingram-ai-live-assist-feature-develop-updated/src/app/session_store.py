"""DynamoDB-backed session state: tracked fields, problem summary and suggestions.

One item per session, keyed by ``session_id``. Conversation history is stored
separately in AgentCore Memory.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3

DEFAULT_TABLE_NAME = os.environ.get("SESSION_TABLE_NAME", "ingram-live-assist-sessions")

FIELD_STATUS_MISSING = "missing"
FIELD_STATUS_CAPTURED = "captured"
FIELD_STATUS_NOT_APPLICABLE = "not_applicable"

FIELD_SOURCE_GUIDANCE = "guidance"
FIELD_SOURCE_AGENT = "agent"
MAX_EVIDENCE_PER_FIELD = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FieldState:
    """A tracked field and its capture status."""

    label: str
    description: str = ""
    required: bool = False
    source: str = FIELD_SOURCE_GUIDANCE
    order: int = 0
    status: str = FIELD_STATUS_MISSING
    value: str | None = None
    last_speaker: str | None = None
    note: str | None = None  # Dismissal reason.
    updated_at: str | None = None
    evidence: list[int] = field(default_factory=list)  # Timeline seqs that hold the value.

    @property
    def is_open(self) -> bool:
        return self.status == FIELD_STATUS_MISSING

    def to_item(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "description": self.description,
            "required": self.required,
            "source": self.source,
            "order": self.order,
            "status": self.status,
            "value": self.value,
            "last_speaker": self.last_speaker,
            "note": self.note,
            "updated_at": self.updated_at,
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_item(cls, field_id: str, item: dict[str, Any]) -> FieldState:
        return cls(
            label=item.get("label") or field_id,
            description=item.get("description") or "",
            required=bool(item.get("required", False)),
            source=item.get("source") or FIELD_SOURCE_GUIDANCE,
            order=int(item.get("order") or 0),  # DynamoDB returns Decimal.
            status=item.get("status", FIELD_STATUS_MISSING),
            value=item.get("value"),
            last_speaker=item.get("last_speaker"),
            note=item.get("note"),
            updated_at=item.get("updated_at"),
            evidence=[int(s) for s in (item.get("evidence") or [])],  # Decimal -> int.
        )


@dataclass
class SessionState:
    session_id: str
    opportunity_type: str | None = None
    opportunity_type_locked: bool = False
    problem_summary: str | None = None
    fields: dict[str, FieldState] = field(default_factory=dict)
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0  # Agent runs completed; a batch of turns counts once.
    last_seq: int = 0  # Highest timeline turn seq the agent has processed.
    # Facts and earlier calls loaded from long-term memory on the first run; None = not loaded.
    prior_context: dict[str, Any] | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    @classmethod
    def new(cls, session_id: str) -> SessionState:
        return cls(session_id=session_id)

    def add_field(
        self,
        field_id: str,
        *,
        label: str,
        description: str = "",
        required: bool = False,
        source: str = FIELD_SOURCE_GUIDANCE,
    ) -> bool:
        """Start tracking a field. Returns True if it was newly added.

        Re-adding a guidance field updates its definition (``required`` is OR-ed)
        but keeps its status and value.
        """
        existing = self.fields.get(field_id)
        if existing is not None:
            if source == FIELD_SOURCE_GUIDANCE and existing.source == FIELD_SOURCE_GUIDANCE:
                existing.label = label
                existing.description = description or existing.description
                existing.required = existing.required or required
            return False
        self.fields[field_id] = FieldState(
            label=label,
            description=description,
            required=required,
            source=source,
            order=len(self.fields),
        )
        return True

    def set_field_captured(
        self, field_id: str, value: str, speaker: str, evidence: list[int] | None = None
    ) -> None:
        """Set the value. Evidence accumulates: a new value keeps earlier details."""
        fs = self.fields[field_id]
        fs.status = FIELD_STATUS_CAPTURED
        fs.value = value
        fs.last_speaker = speaker
        fs.evidence = sorted(set(fs.evidence) | set(evidence or []))[-MAX_EVIDENCE_PER_FIELD:]
        fs.note = None
        fs.updated_at = _now_iso()

    def set_field_not_applicable(self, field_id: str, reason: str) -> None:
        fs = self.fields[field_id]
        fs.status = FIELD_STATUS_NOT_APPLICABLE
        fs.note = reason
        fs.updated_at = _now_iso()

    def open_fields(self) -> list[tuple[str, FieldState]]:
        """Return missing fields, required first, then in insertion order."""
        items = [(fid, fs) for fid, fs in self.fields.items() if fs.is_open]
        return sorted(items, key=lambda kv: (not kv[1].required, kv[1].order))

    def captured_fields(self) -> list[tuple[str, FieldState]]:
        return self._fields_with_status(FIELD_STATUS_CAPTURED)

    def dismissed_fields(self) -> list[tuple[str, FieldState]]:
        return self._fields_with_status(FIELD_STATUS_NOT_APPLICABLE)

    def agent_added_count(self) -> int:
        return sum(1 for fs in self.fields.values() if fs.source == FIELD_SOURCE_AGENT)

    def add_suggestion(
        self,
        *,
        question: str,
        target_field_id: str | None,
        target_label: str | None,
        trigger_speaker: str,
        trigger_text: str,
        rationale: str | None = None,
        trigger_seq: int | None = None,
    ) -> dict[str, Any]:
        """Append a suggestion to the history, tagged with the current request number."""
        entry = {
            "turn": self.chunk_count,
            "question": question,
            "target_field_id": target_field_id,
            "target_label": target_label,
            "rationale": rationale,
            "trigger_speaker": trigger_speaker,
            "trigger_text": trigger_text,
            "trigger_seq": trigger_seq,
            "created_at": _now_iso(),
        }
        self.suggestions.append(entry)
        return entry

    def to_item(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "opportunity_type": self.opportunity_type,
            "opportunity_type_locked": self.opportunity_type_locked,
            "problem_summary": self.problem_summary,
            "fields": {fid: fs.to_item() for fid, fs in self.fields.items()},
            "suggestions": list(self.suggestions),
            "chunk_count": self.chunk_count,
            "last_seq": self.last_seq,
            "prior_context": self.prior_context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> SessionState:
        return cls(
            session_id=item["session_id"],
            opportunity_type=item.get("opportunity_type"),
            opportunity_type_locked=bool(item.get("opportunity_type_locked", False)),
            problem_summary=item.get("problem_summary"),
            fields={
                fid: FieldState.from_item(fid, fs)
                for fid, fs in (item.get("fields") or {}).items()
            },
            suggestions=[
                {**s, "turn": int(s.get("turn") or 0)}  # DynamoDB returns Decimal.
                for s in (item.get("suggestions") or [])
            ],
            chunk_count=int(item.get("chunk_count") or 0),
            last_seq=int(item.get("last_seq") or 0),
            prior_context=item.get("prior_context"),
            created_at=item.get("created_at", _now_iso()),
            updated_at=item.get("updated_at", _now_iso()),
        )

    def _fields_with_status(self, status: str) -> list[tuple[str, FieldState]]:
        items = [(fid, fs) for fid, fs in self.fields.items() if fs.status == status]
        return sorted(items, key=lambda kv: kv[1].order)


class SessionNotFoundError(KeyError):
    """Raised when a continuation request references an unknown session_id."""


class SessionStore:
    """Reads and writes SessionState. The table must already exist
    (see ``scripts/create_session_table.py``)."""

    def __init__(
        self,
        table_name: str = DEFAULT_TABLE_NAME,
        region_name: str | None = None,
        resource: Any | None = None,
    ) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)

    def get(self, session_id: str) -> SessionState | None:
        # Consistent: the worker reads its cursor right after writing it.
        item = self._table.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
        return SessionState.from_item(item) if item is not None else None

    def require(self, session_id: str) -> SessionState:
        """Return an existing session.

        Raises:
            SessionNotFoundError: The session does not exist.
        """
        state = self.get(session_id)
        if state is None:
            raise SessionNotFoundError(session_id)
        return state

    def create(self, session_id: str) -> SessionState:
        state = SessionState.new(session_id)
        self.save(state)
        return state

    def save(self, state: SessionState) -> None:
        state.updated_at = _now_iso()
        self._table.put_item(Item=state.to_item())


_default_store: SessionStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> SessionStore:
    """Return the process-wide store, created on first use so imports need no AWS credentials."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = SessionStore()
        return _default_store
