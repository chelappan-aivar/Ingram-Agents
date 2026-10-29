"""Applies a validated event to the timeline and schedules processing."""

from __future__ import annotations

from typing import Any

from app.events import STATUS_ENDED, Event, SessionEnd, SessionStart, SessionSummarize, SessionUpdate, Turn
from app.config_store import ConfigCatalog
from app.processor import Processor
from app.session_store import SessionNotFoundError
from app.timeline_store import SessionNotEndedError, TimelineStore


def handle_event(
    event: Event, *, timeline: TimelineStore, processor: Processor, configs: ConfigCatalog | None = None
) -> dict[str, Any]:
    """Store the event and return the ack fields specific to its type.

    Raises:
        SessionNotFoundError: A turn, update, end or summarize for a call that was never started.
        SessionEndedError: A turn or update for a call that has ended.
        SessionNotEndedError: A summarize for a call that is still live.
        ConfigNotFoundError: A start with an unknown or archived config profile.
    """
    if isinstance(event, SessionStart):
        guidance, _ = (configs or ConfigCatalog(None)).resolve(event.config_id, event.config_version)
        meta, created = timeline.start_session(event, guidance)
        if created and event.prepare_questions:
            processor.prepare_questions(event.session_id)  # Background: the ack does not wait for the model.
        return {"created": created, "status": meta["status"],
                "config_id": meta.get("config_id"), "config_version": meta.get("config_version")}

    if isinstance(event, Turn):
        seq = timeline.append_turn(event)
        processor.schedule(event.session_id)
        return {"seq": seq}

    if isinstance(event, SessionUpdate):
        meta = timeline.update_session(
            event.session_id, assist_config=event.assist_config, status=event.status
        )
        return {"status": meta["status"], "assist_config": meta["assist_config"]}

    if isinstance(event, SessionEnd):
        meta, ended_now = timeline.end_session(event.session_id, event.reason)
        # Flush turns that are still pending, then write the minutes.
        processor.schedule(event.session_id)
        return {"status": meta["status"], "ended_now": ended_now}

    if isinstance(event, SessionSummarize):
        meta = timeline.get_meta(event.session_id)
        if meta is None:
            raise SessionNotFoundError(event.session_id)
        if meta.get("status") != STATUS_ENDED:
            raise SessionNotEndedError(event.session_id)
        processor.schedule(event.session_id, regenerate_minutes=True)
        return {"status": meta["status"], "minutes_status": "queued"}

    raise TypeError(f"Unsupported event: {type(event).__name__}")
