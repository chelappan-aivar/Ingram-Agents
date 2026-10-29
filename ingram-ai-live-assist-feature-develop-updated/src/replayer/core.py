"""Sends a recorded conversation to the entry point at speaking pace.

Recording adapter plus stream: each recorded turn becomes an ``ila.event.v1``
turn event, sent after the recorded gap (capped, then divided by ``speed``).
Honors pause and end through the call's status.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.conversation_import import ConversationTurn, compute_delays_seconds

SCHEMA = "ila.event.v1"
SOURCE = "recording"
DEFAULT_MAX_GAP_SECONDS = 8.0
PAUSE_POLL_SECONDS = 1.0

FINISHED = "finished"
ENDED = "ended"  # The call was ended by someone else.
DEADLINE = "deadline"  # Out of time; resume from next_index.


class Sender(Protocol):
    def send(self, event: dict) -> dict:
        """Deliver one event to the entry point and return its JSON ack."""


class ReplayError(RuntimeError):
    """Raised when the entry point rejects an event the replay depends on."""


@dataclass(frozen=True)
class ReplayResult:
    session_id: str
    stopped: str  # FINISHED, ENDED or DEADLINE
    turns_sent: int
    next_index: int | None  # Set when stopped at DEADLINE.


def new_session_id() -> str:
    """Return a fresh session id (37 chars, above the runtime's 33-char minimum)."""
    return f"call-{uuid.uuid4().hex}"


def start_event(
    session_id: str,
    *,
    title: str | None = None,
    assist_config: dict | None = None,
    source: str = SOURCE,
) -> dict:
    event = {"schema": SCHEMA, "type": "session.start", "session_id": session_id, "source": source}
    if title:
        event["title"] = title
    if assist_config:
        event["assist_config"] = assist_config
    return event


def turn_event(session_id: str, turn: ConversationTurn, index: int) -> dict:
    return {
        "schema": SCHEMA,
        "type": "turn",
        "session_id": session_id,
        "source": SOURCE,
        "speaker": turn.speaker,
        "speaker_name": turn.name,
        "text": turn.text,
        "spoken_at": _iso(turn.timestamp),
        "is_final": True,
        "event_id": f"rec-{index}",
    }


def end_event(session_id: str, reason: str) -> dict:
    return {"schema": SCHEMA, "type": "session.end", "session_id": session_id, "reason": reason}


def replay(
    *,
    session_id: str,
    turns: list[ConversationTurn],
    sender: Sender,
    speed: float = 1.0,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    start_index: int = 0,
    title: str | None = None,
    assist_config: dict | None = None,
    get_status: Callable[[str], str | None] | None = None,
    deadline: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    on_event: Callable[[dict, dict], None] | None = None,
    send_start: bool = True,
) -> ReplayResult:
    """Replay ``turns`` from ``start_index`` as a live call.

    Sends ``session.start`` when ``start_index`` is 0 (unless ``send_start`` is
    False because the caller already started the call) and ``session.end``
    after the last turn. Stops early if the call is ended elsewhere, or before
    ``deadline`` (a ``now()`` value) so a caller can continue later.

    Raises:
        ReplayError: The entry point rejected an event.
    """
    if speed <= 0:
        raise ValueError("speed must be positive")

    def send(event: dict) -> dict:
        ack = sender.send(event)
        if on_event:
            on_event(event, ack)
        return ack

    if send_start and start_index == 0:
        ack = send(start_event(session_id, title=title, assist_config=assist_config))
        if not ack.get("accepted"):
            raise ReplayError(f"session.start rejected: {ack.get('error')}")

    delays = compute_delays_seconds(turns)
    sent = 0
    for i in range(start_index, len(turns)):
        wait = min(delays[i], max_gap_seconds) / speed
        if deadline is not None and now() + wait > deadline:
            return ReplayResult(session_id, DEADLINE, sent, i)
        if wait > 0:
            sleep(wait)

        status = get_status(session_id) if get_status else None
        while status == "paused":
            if deadline is not None and now() + PAUSE_POLL_SECONDS > deadline:
                return ReplayResult(session_id, DEADLINE, sent, i)
            sleep(PAUSE_POLL_SECONDS)
            status = get_status(session_id)
        if status == "ended":
            return ReplayResult(session_id, ENDED, sent, None)

        ack = send(turn_event(session_id, turns[i], i))
        if not ack.get("accepted"):
            if ack.get("code") == "session_ended":
                return ReplayResult(session_id, ENDED, sent, None)
            raise ReplayError(f"turn {i} rejected: {ack.get('error')}")
        sent += 1

    send(end_event(session_id, "replay finished"))
    return ReplayResult(session_id, FINISHED, sent, None)


def _iso(dt: datetime) -> str:
    """Format as UTC ISO-8601. Naive values are UTC (the import normalizes to naive UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
