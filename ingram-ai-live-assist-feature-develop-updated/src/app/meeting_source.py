"""Live meeting source: a Vexa bot listens to the meeting and its sentences become turns.

It runs inside the AgentCore Runtime session that owns the call (the same process as the worker that
runs the agent), so a turn goes to the agent in-process and no per-sentence network hop is needed. A
runtime session stays alive while it reports itself busy (up to its maximum lifetime), which is what
lets one session follow a whole meeting.

For each finished utterance the speaker's name decides the role (``speaker_roles``) and one
``ila.event.v1`` turn is delivered. The call ends when the meeting ends, or when somebody ends the
call from the console, in which case the bot is asked to leave.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.events import SCHEMA
from app.meeting_link import MeetingLink
from app.speaker_roles import RoleResolver
from app.transcript import MAX_NAME_LENGTH, parse_name
from app.vexa_client import (
    TranscriptStream,
    Utterance,
    VexaError,
    VexaSettings,
    stop_bot,
)

SOURCE = "meeting"
UNKNOWN_SPEAKER = "Unknown"
STATUS_POLL_SECONDS = 2.0
EPOCH_FLOOR = 1_000_000_000  # Below this a number is "seconds into the meeting", not a clock time.

logger = logging.getLogger(__name__)

Deliver = Callable[[dict], Awaitable[dict]]  # Hands one ila.event.v1 event to the agent; returns its ack.


@dataclass
class MeetingResult:
    turns_sent: int = 0
    turns_dropped: int = 0
    promoted: int = 0
    ended_by: str = ""  # "meeting", "user" or "error"


def _spoken_at(start: object) -> str:
    """UTC ISO time of an utterance; the current time when Vexa gave no clock time."""
    moment = None
    if isinstance(start, (int, float)) and not isinstance(start, bool) and start > EPOCH_FLOOR:
        moment = datetime.fromtimestamp(start, tz=timezone.utc)
    elif isinstance(start, str):
        try:
            moment = datetime.fromisoformat(start.replace("Z", "+00:00"))
            moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        except ValueError:
            moment = None
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def turn_event(session_id: str, utterance: Utterance, roles: RoleResolver) -> dict:
    name = parse_name(utterance.speaker) if utterance.speaker else None
    return {
        "schema": SCHEMA, "type": "turn", "session_id": session_id, "source": SOURCE,
        "speaker": roles.role_for(name), "speaker_name": (name or UNKNOWN_SPEAKER)[:MAX_NAME_LENGTH],
        "text": utterance.text, "spoken_at": _spoken_at(utterance.start), "is_final": True,
        "event_id": f"vexa-{utterance.segment_id}"[:200],
    }


def end_event(session_id: str, reason: str) -> dict:
    return {"schema": SCHEMA, "type": "session.end", "session_id": session_id, "reason": reason}


async def run_meeting(
    *,
    session_id: str,
    link: MeetingLink,
    ba_names: list[str],
    settings: VexaSettings,
    deliver: Deliver,
    get_status: Callable[[str], str | None],
    set_bot_status: Callable[[str], None] | None = None,
    stop_event: asyncio.Event | None = None,
    poll_seconds: float = STATUS_POLL_SECONDS,
) -> MeetingResult:
    """Follow the meeting until it ends or the call is ended. The bot was already requested.

    ``get_status`` returns the call's status (``live``, ``paused``, ``ended``) and ``set_bot_status``
    records what the bot is doing so the console can show it; both are blocking calls.
    """
    roles = RoleResolver(ba_names)
    result = MeetingResult()
    state = {"call_ended": False, "reason": None}

    async def record(bot_status: str) -> None:
        if set_bot_status is None:
            return
        try:
            await asyncio.to_thread(set_bot_status, bot_status)
        except Exception:  # noqa: BLE001 - a status the console cannot show must not stop the meeting
            logger.warning("bot status could not be recorded session=%r", session_id, exc_info=True)

    async def on_utterance(utterance: Utterance) -> None:
        if state["call_ended"]:
            return
        try:
            ack = await deliver(turn_event(session_id, utterance, roles))
        except Exception:  # noqa: BLE001 - a lost line must not stop the meeting; counted and logged
            logger.warning("turn could not be delivered session=%r", session_id, exc_info=True)
            result.turns_dropped += 1
            return
        if ack.get("accepted"):
            result.turns_sent += 1
            result.promoted += int(utterance.promoted)
        elif ack.get("code") == "session_ended":
            state["call_ended"] = True
            stream.stop()
        else:
            result.turns_dropped += 1
            logger.warning("turn rejected session=%r code=%s", session_id, ack.get("code"))

    async def on_status(status: str) -> None:
        logger.info("meeting status session=%r status=%s", session_id, status)
        await record(status)
        if status == "failed":
            state["reason"] = "the meeting bot failed"

    stream = TranscriptStream(settings, link, on_utterance, on_status)

    async def watch_call() -> None:
        """Stop following when the call is ended from the console, or the process is shutting down."""
        while True:
            await asyncio.sleep(poll_seconds)
            if stop_event is not None and stop_event.is_set():
                state["reason"] = "stopped"
                stream.stop()
                return
            status = await asyncio.to_thread(get_status, session_id)
            if status == "ended":
                state["call_ended"] = True
                stream.stop()
                return

    await record("requested")
    watcher = asyncio.create_task(watch_call())
    try:
        await stream.run()
    except Exception:  # noqa: BLE001
        logger.exception("meeting source failed session=%r", session_id)
        state["reason"] = "the live transcript connection failed"
    finally:
        watcher.cancel()

    result.ended_by = "user" if state["call_ended"] else ("error" if state["reason"] else "meeting")
    await record("failed" if result.ended_by == "error" else "completed")
    # The bot leaves when the call is over, whoever ended it. A bot that already left answers 404.
    try:
        await stop_bot(settings, link)
    except VexaError as e:
        logger.warning("bot did not stop session=%r: %s", session_id, e.detail)
    if not state["call_ended"]:
        reason = f"meeting ended: {state['reason']}" if state["reason"] else "meeting ended"
        try:
            await deliver(end_event(session_id, reason))
        except Exception:  # noqa: BLE001
            logger.warning("could not end the call session=%r", session_id, exc_info=True)
    logger.info("meeting source done session=%r sent=%d dropped=%d promoted=%d retracted=%d ended_by=%s",
                session_id, result.turns_sent, result.turns_dropped, result.promoted,
                stream.retracted_after_delivery, result.ended_by)
    return result
