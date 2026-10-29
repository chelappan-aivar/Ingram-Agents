"""Long-term memory across calls, in a dedicated AgentCore Memory resource.

Two levels, each its own actor, so facts never leak between accounts:

    account      actor "acct-<account_id>"                 company-wide facts
    opportunity  actor "opp-<account_id>--<opportunity_id>" facts about one deal

Only curated text is written: one event per level when a call's minutes are
ready (see ``app/call_memory.py``). A semantic strategy extracts facts from it
into ``/facts/<actor>/``. The live conversation history stays in the separate
short-term memory, which has no strategies, so it is never extracted.

Retention: records expire after ``DATA_RETENTION_DAYS`` (180 by default, the same
as call data), set on the strategy by deploy. Reads also drop older records, in case
the strategy's expiry was configured differently. ``delete`` erases a level on request.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import boto3
from botocore.config import Config

from app.timeline_store import DATA_RETENTION_DAYS
DEFAULT_LONG_TERM_MEMORY_ID = os.environ.get("LONG_TERM_MEMORY_ID")  # Unset disables it.
RETENTION_DAYS = DATA_RETENTION_DAYS
MAX_RECORD_CHARS = 500
MAX_LIST_PAGES = 5
DELETE_BATCH = 100

# Short timeouts: lookups happen inside a live agent run.
_CLIENT_CONFIG = Config(connect_timeout=2, read_timeout=4, retries={"max_attempts": 2, "mode": "standard"})

Level = Literal["account", "opportunity"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryScope:
    """Whose memory a call may read and write. Built from the call's meta, never the model."""

    account_id: str
    opportunity_id: str | None = None

    @property
    def account_actor(self) -> str:
        return f"acct-{self.account_id}"

    @property
    def opportunity_actor(self) -> str | None:
        return f"opp-{self.account_id}--{self.opportunity_id}" if self.opportunity_id else None

    def actors(self, level: Level | Literal["both"] = "both") -> list[tuple[Level, str]]:
        pairs: list[tuple[Level, str]] = [("account", self.account_actor)]
        if self.opportunity_actor:
            pairs.append(("opportunity", self.opportunity_actor))
        return pairs if level == "both" else [p for p in pairs if p[0] == level]

    @classmethod
    def from_meta(cls, meta: dict) -> MemoryScope | None:
        if not meta.get("account_id"):
            return None
        return cls(meta["account_id"], meta.get("opportunity_id"))


def facts_namespace(actor: str) -> str:
    return f"/facts/{actor}/"


@dataclass(frozen=True)
class MemoryFact:
    level: Level
    text: str
    created_at: str | None
    record_id: str | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "text": self.text, "created_at": self.created_at}


class LongTermMemory:
    def __init__(self, memory_id: str, region_name: str | None = None, client: Any | None = None) -> None:
        self.memory_id = memory_id
        self._client = client or boto3.client("bedrock-agentcore", region_name=region_name, config=_CLIENT_CONFIG)

    # --- reads --------------------------------------------------------------------

    def search(self, scope: MemoryScope, query: str, level: Level | Literal["both"], top_k: int) -> list[MemoryFact]:
        """Semantic search in the scope's fact namespaces."""
        facts: list[MemoryFact] = []
        for lvl, actor in scope.actors(level):
            response = self._client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=facts_namespace(actor),
                searchCriteria={"searchQuery": query[:1000], "topK": top_k},
            )
            facts += [_fact(lvl, r) for r in response.get("memoryRecordSummaries", [])]
        facts = [f for f in facts if _fresh(f)]
        return sorted(facts, key=lambda f: f.score or 0, reverse=True)[:top_k]

    def list_facts(self, scope: MemoryScope, limit_per_level: int) -> list[MemoryFact]:
        """Newest facts per level, for the start-of-call context and the account page."""
        facts: list[MemoryFact] = []
        for lvl, actor in scope.actors():
            records = [_fact(lvl, r) for r in self._list(facts_namespace(actor))]
            records = sorted((f for f in records if _fresh(f)), key=lambda f: f.created_at or "", reverse=True)
            facts += records[:limit_per_level]
        return facts

    # --- writes -------------------------------------------------------------------

    def write_notes(self, *, actor: str, session_id: str, text: str) -> None:
        """Store curated call notes for extraction. Repeats with the same text are no-ops."""
        token = hashlib.sha256(f"{actor}|{session_id}|{text}".encode()).hexdigest()
        self._client.create_event(
            memoryId=self.memory_id,
            actorId=actor,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": "USER", "content": {"text": text[:100_000]}}}],
            clientToken=token,
        )

    def delete(self, actor_prefixes: list[str]) -> int:
        """Delete every fact under the given actors (exact actor or ``opp-<account>--`` prefix)."""
        deleted = 0
        for prefix in actor_prefixes:
            ids = [r["memoryRecordId"] for r in self._list(f"/facts/{prefix}")]
            for i in range(0, len(ids), DELETE_BATCH):
                batch = ids[i:i + DELETE_BATCH]
                self._client.batch_delete_memory_records(
                    memoryId=self.memory_id, records=[{"memoryRecordId": rid} for rid in batch]
                )
                deleted += len(batch)
        return deleted

    def _list(self, namespace_prefix: str) -> list[dict]:
        records: list[dict] = []
        kwargs: dict[str, Any] = {"memoryId": self.memory_id, "namespace": namespace_prefix, "maxResults": 100}
        for _ in range(MAX_LIST_PAGES):
            response = self._client.list_memory_records(**kwargs)
            records += response.get("memoryRecordSummaries", [])
            if not response.get("nextToken"):
                break
            kwargs["nextToken"] = response["nextToken"]
        return records


def account_delete_prefixes(account_id: str, opportunity_id: str | None = None) -> list[str]:
    """Actor prefixes to erase: one opportunity, or the account and all its opportunities."""
    scope = MemoryScope(account_id, opportunity_id)
    if opportunity_id:
        return [f"{scope.opportunity_actor}/"]
    # "--" can't appear inside an id, so this prefix only matches this account's opportunities.
    return [f"{scope.account_actor}/", f"opp-{account_id}--"]


_default_memory: LongTermMemory | None = None


def get_default_memory() -> LongTermMemory | None:
    """The process-wide client, or None when ``LONG_TERM_MEMORY_ID`` is unset."""
    global _default_memory
    if _default_memory is None and DEFAULT_LONG_TERM_MEMORY_ID:
        _default_memory = LongTermMemory(DEFAULT_LONG_TERM_MEMORY_ID, region_name=os.environ.get("AWS_REGION"))
    return _default_memory


def _fact(level: Level, record: dict) -> MemoryFact:
    created = record.get("createdAt")
    if isinstance(created, datetime):
        created = created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = (record.get("content") or {}).get("text") or ""
    return MemoryFact(level, " ".join(text.split())[:MAX_RECORD_CHARS], created,
                      record.get("memoryRecordId"), record.get("score"))


def _fresh(fact: MemoryFact) -> bool:
    if not fact.created_at:
        return True
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return fact.created_at >= cutoff
