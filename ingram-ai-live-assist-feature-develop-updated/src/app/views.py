"""Read model for consoles: the full view of a call and its handoff export.

Built from two reads, the timeline query and the session state item, so a UI
can poll it every second or two.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.guidance import GuidanceSet, guidance_for_meta
from app.session_store import SessionState, SessionStore
from app.timeline_store import META_SK, MINUTES_SK, PREQUESTIONS_SK, RUN_PREFIX, TURN_PREFIX, TimelineStore

_TURN_FIELDS = ("seq", "speaker", "speaker_name", "text", "spoken_at", "received_at", "source", "event_id")
_MINUTES_FIELDS = (
    "status", "minutes", "generated_at", "started_at", "duration_ms", "model_id",
    "prompt_version", "transcript_last_seq", "turn_count", "windows", "error_code",
)
_PREQUESTIONS_FIELDS = ("status", "questions", "generated_at", "started_at", "duration_ms", "model_id", "error_code")
_RUN_FIELDS = (
    "first_seq", "last_seq", "turn_count", "status", "run_number", "started_at", "duration_ms",
    "narration", "suggestions", "captured_fields", "added_gaps", "dismissed_gaps",
    "opportunity_type", "opportunity_type_just_resolved", "settings", "error", "memory_lookups",
)


def build_session_view(
    session_id: str, *, timeline: TimelineStore, store: SessionStore
) -> dict[str, Any] | None:
    """Return everything a console shows for a call, or None if it doesn't exist."""
    items = timeline.items(session_id)
    meta = next((i for i in items if i["sk"] == META_SK), None)
    if meta is None:
        return None
    minutes = next((i for i in items if i["sk"] == MINUTES_SK), None)
    prequestions = next((i for i in items if i["sk"] == PREQUESTIONS_SK), None)

    state = store.get(session_id)
    cursor = state.last_seq if state else 0
    runs = sorted(
        ({k: i.get(k) for k in _RUN_FIELDS} for i in items if i["sk"].startswith(RUN_PREFIX)),
        key=lambda r: r["first_seq"],
    )
    turns = sorted(
        ({k: i.get(k) for k in _TURN_FIELDS} for i in items if i["sk"].startswith(TURN_PREFIX)),
        key=lambda t: t["seq"],
    )
    for t in turns:
        t["processed"] = t["seq"] <= cursor
        t["run_first_seq"] = next(
            (r["first_seq"] for r in runs if r["first_seq"] <= t["seq"] <= r["last_seq"]), None
        )

    latest_seq = int(meta.get("next_seq") or 0)
    return {
        "session_id": session_id,
        "status": meta.get("status"),
        "title": meta.get("title"),
        "description": meta.get("description"),
        "source": meta.get("source"),
        "account_id": meta.get("account_id"),
        "opportunity_id": meta.get("opportunity_id"),
        "config_id": meta.get("config_id") or "ingram-default",
        "config_version": meta.get("config_version") or 0,
        "accepts_manual_turns": meta.get("status") != "ended" and not meta.get("exclusive_source"),
        # Live meeting calls: {"platform", "bot"} where bot is Vexa's status word. Null otherwise.
        "meeting": meta.get("meeting"),
        "assist_config": meta.get("assist_config"),
        "created_at": meta.get("created_at"),
        "ended_at": meta.get("ended_at"),
        "end_reason": meta.get("end_reason"),
        "processing": {
            "latest_seq": latest_seq,
            "last_processed_seq": cursor,
            "pending_turns": max(latest_seq - cursor, 0),
            "run_count": state.chunk_count if state else 0,
            "busy": latest_seq > cursor,
        },
        "turns": turns,
        "runs": runs,
        "suggestions": _suggestions(state),
        "state": _state(state, guidance_for_meta(meta)),
        "minutes": _minutes(minutes),
        # Questions to prepare, written at the start from the meeting's title and description.
        "prequestions": _prequestions(prequestions),
        # Loaded on the first agent run of a call with an account; null until then.
        "prior_context": state.prior_context if state else None,
    }


def call_row(meta: dict[str, Any]) -> dict[str, Any]:
    """One call in a list."""
    return {
        "session_id": meta["session_id"], "title": meta.get("title"), "status": meta.get("status"),
        "source": meta.get("source"), "created_at": meta.get("created_at"), "ended_at": meta.get("ended_at"),
        "turn_count": int(meta.get("next_seq") or 0),
        "account_id": meta.get("account_id"), "opportunity_id": meta.get("opportunity_id"),
        "config_id": meta.get("config_id") or "ingram-default", "config_version": meta.get("config_version") or 0,
    }


def build_handoff(view: dict[str, Any]) -> dict[str, Any]:
    """Structured capture of the call, ready for SOW drafting. No model call."""
    state = view["state"]
    return {
        "session_id": view["session_id"],
        "account_id": view["account_id"],
        "opportunity_id": view["opportunity_id"],
        "title": view["title"],
        "status": view["status"],
        "created_at": view["created_at"],
        "ended_at": view["ended_at"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "complete": view["status"] == "ended" and not view["processing"]["busy"],
        "problem_summary": state["problem_summary"],
        "opportunity_type": state["opportunity_type"],
        "captured": state["captured"],
        "open_gaps": state["open_gaps"],
        "dismissed": state["dismissed"],
        "suggested_questions": [s["question"] for s in view["suggestions"]],
        "turn_count": view["processing"]["latest_seq"],
        "summary": (view["minutes"].get("minutes") or {}).get("summary"),
        "minutes": view["minutes"],
    }


def _state(state: SessionState | None, guidance: GuidanceSet) -> dict[str, Any]:
    if state is None:
        return {
            "problem_summary": None, "opportunity_type": None,
            "open_gaps": [], "captured": [], "dismissed": [],
        }
    categories = guidance.categories()
    return {
        "problem_summary": state.problem_summary,
        "opportunity_type": state.opportunity_type,
        "open_gaps": [
            {
                "field_id": fid, "label": fs.label, "description": fs.description,
                "required": fs.required, "category": categories.get(fid), "source": fs.source,
            }
            for fid, fs in state.open_fields()
        ],
        "captured": [
            {
                "field_id": fid, "label": fs.label, "value": fs.value, "speaker": fs.last_speaker,
                "category": categories.get(fid), "updated_at": fs.updated_at,
                "evidence": list(fs.evidence),
            }
            for fid, fs in state.captured_fields()
        ],
        "dismissed": [
            {"field_id": fid, "label": fs.label, "reason": fs.note}
            for fid, fs in state.dismissed_fields()
        ],
    }


def _suggestions(state: SessionState | None) -> list[dict[str, Any]]:
    if state is None:
        return []
    return [
        {
            "run_number": s.get("turn"),
            "question": s.get("question"),
            "target_field_id": s.get("target_field_id"),
            "target_label": s.get("target_label"),
            "rationale": s.get("rationale"),
            "trigger_seq": s.get("trigger_seq"),
            "created_at": s.get("created_at"),
        }
        for s in state.suggestions
    ]


def _prequestions(item: dict[str, Any] | None) -> dict[str, Any]:
    """Pre-questions status and list. ``status`` is "none" when the call did not ask for them."""
    if item is None:
        return {"status": "none"}
    return {k: item.get(k) for k in _PREQUESTIONS_FIELDS if item.get(k) is not None}


def _minutes(item: dict[str, Any] | None) -> dict[str, Any]:
    """Minutes status and body. ``status`` is "none" until the call has ended."""
    if item is None:
        return {"status": "none"}
    return {k: item.get(k) for k in _MINUTES_FIELDS if item.get(k) is not None}
