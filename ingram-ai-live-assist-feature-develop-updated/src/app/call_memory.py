"""What a call reads from and writes to long-term memory.

Start of a call (first agent run): ``load_prior_context`` gathers remembered
facts per level plus the minutes of the account's earlier calls, which are in
DynamoDB right away (memory extraction lags by a minute or more).

End of a call (after the minutes): ``remember_call`` writes curated notes,
account-level facts to the account actor and the rest to the opportunity actor.
"""

from __future__ import annotations

import logging
from typing import Any

from app.account_store import AccountStore
from app.guidance import MEMORY_SCOPE_ACCOUNT, guidance_for_meta
from app.memory import LongTermMemory, MemoryScope
from app.session_store import SessionStore
from app.timeline_store import MINUTES_OK, TimelineStore

PRIOR_FACTS_PER_LEVEL = 15
PREVIOUS_CALLS = 3
MAX_PREVIOUS_ITEMS = 5


logger = logging.getLogger(__name__)


def load_prior_context(
    session_id: str,
    scope: MemoryScope,
    *,
    memory: LongTermMemory | None,
    timeline: TimelineStore,
    accounts: AccountStore | None = None,
) -> dict[str, Any] | None:
    """Return ``{"account": [...], "opportunity": [...], "previous_calls": [...]}``.

    Returns None if memory was unreachable, so the next run tries again.
    Earlier calls are the same opportunity's, or the account's if the call has none.
    """
    # Prior context is optional: any failure here must not fail the agent run.
    try:
        facts = [f.to_dict() for f in memory.list_facts(scope, PRIOR_FACTS_PER_LEVEL)] if memory else []
        since = accounts.forgotten_at(scope.account_id, scope.opportunity_id) if accounts else None
        calls = previous_calls(session_id, scope, timeline, since=since)
    except Exception:  # noqa: BLE001 - the call goes on without prior context.
        logger.warning("prior context unavailable session=%r", session_id, exc_info=True)
        return None
    return {
        "account": [f["text"] for f in facts if f["level"] == "account"],
        "opportunity": [f["text"] for f in facts if f["level"] == "opportunity"],
        "previous_calls": calls,
    }


def previous_calls(
    session_id: str, scope: MemoryScope, timeline: TimelineStore, *, since: str | None = None
) -> list[dict]:
    """Minutes of earlier ended calls, newest first. ``since``: skip calls that ended
    before the scope's memory was erased (Forget)."""
    metas, _ = timeline.list_account_calls(
        scope.account_id, opportunity_id=scope.opportunity_id, limit=PREVIOUS_CALLS + 5
    )
    calls = []
    for meta in metas:
        if meta["session_id"] == session_id or meta.get("status") != "ended":
            continue
        if since and (meta.get("ended_at") or "") <= since:
            continue
        record = timeline.get_minutes(meta["session_id"]) or {}
        minutes = record.get("minutes") if record.get("status") == MINUTES_OK else None
        if not minutes:
            continue
        calls.append({
            "session_id": meta["session_id"],
            "title": meta.get("title"),
            "ended_at": meta.get("ended_at"),
            "summary": minutes.get("summary"),
            "decisions": [i["text"] for i in minutes.get("decisions", [])][:MAX_PREVIOUS_ITEMS],
            "open_questions": [i["text"] for i in minutes.get("open_questions", [])][:MAX_PREVIOUS_ITEMS],
        })
        if len(calls) >= PREVIOUS_CALLS:
            break
    return calls


def remember_call(
    session_id: str, *, timeline: TimelineStore, store: SessionStore, memory: LongTermMemory
) -> bool:
    """Write the ended call to long-term memory once per minutes version. Returns True if written."""
    meta = timeline.get_meta(session_id) or {}
    scope = MemoryScope.from_meta(meta)
    record = timeline.get_minutes(session_id) or {}
    if scope is None or record.get("status") != MINUTES_OK:
        return False
    if record.get("memory_written_for") == record.get("generated_at"):
        return False  # This version of the minutes is already in memory.

    state = store.get(session_id)
    captured = state.captured_fields() if state else []
    minutes = record["minutes"]
    # Each field's memory scope comes from the call's config profile.
    scopes = guidance_for_meta(meta).memory_scopes(state.opportunity_type if state else None)
    account_level = {fid for fid, scope in scopes.items() if scope == MEMORY_SCOPE_ACCOUNT}

    # The notes hold facts only. No call title/date header (it was extracted as a
    # fact about "the user"), and no open questions (they describe the call, not
    # the customer; earlier calls' open questions reach the agent via the minutes).
    account_lines = [f"- {fs.label}: {fs.value}" for fid, fs in captured if fid in account_level]
    contacts = [a["name"] for a in minutes.get("attendees", []) if a.get("speaker") == "cx" and a.get("name")]
    if account_lines or contacts:
        memory.write_notes(actor=scope.account_actor, session_id=session_id, text="\n".join([
            *_section("Customer environment and constraints", account_lines),
            *(["Customer contacts: " + ", ".join(contacts)] if contacts else []),
        ]))

    deal_lines = [f"- {fs.label}: {fs.value}" for fid, fs in captured if fid not in account_level]
    actor = scope.opportunity_actor or scope.account_actor
    memory.write_notes(actor=actor, session_id=session_id, text="\n".join([
        f"Opportunity summary: {minutes.get('summary', '')}",
        *_section("Requirements captured", deal_lines),
        *_section("Decisions", [f"- {i['text']}" for i in minutes.get("decisions", [])]),
        *_section("Agreed actions", [
            f"- {i['text']}" + (f" (owner: {i['owner']})" if i.get("owner") else "")
            for i in minutes.get("action_items", [])
        ]),
    ]))
    timeline.mark_minutes(session_id, memory_written_for=record.get("generated_at"))
    return True


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title}:", *lines] if lines else []
