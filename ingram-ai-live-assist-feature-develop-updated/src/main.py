"""AgentCore Runtime entry point for the live-assist agent.

Accepts only ``ila.event.v1`` events (see ``app/events.py``) and answers each
with a JSON ack in milliseconds. Turns are processed by a background worker;
results land in DynamoDB and are read with ``app/views.py``.

``meeting.join`` also starts a background task that follows a live meeting through
a Vexa bot (``app/meeting_join.py``). The task keeps this runtime session busy, so
one session follows one meeting from its start to its end.

    {"accepted": true, "type": "turn", "session_id": "...", "seq": 17}
    {"accepted": false, "code": "invalid_event" | "session_mismatch" | "unknown_session"
                                | "session_ended" | "session_live" | "source_mismatch"
                                | "unknown_config" | "meeting_busy" | "meeting_not_authorized" | ...,
     "error": "..."}
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()

from bedrock_agentcore import BedrockAgentCoreApp 

from app.events import InvalidEventError, MeetingJoin, parse_event 
from app.ingest import handle_event 
from app.meeting_join import MeetingRunner
from app.account_store import AccountStore
from app.config_store import ConfigCatalog, ConfigNotFoundError, ConfigStore
from app.memory import get_default_memory
from app.processor import Processor 
from app.session_store import SessionNotFoundError, get_default_store 
from app.timeline_store import (
    SessionEndedError,
    SessionNotEndedError,
    SourceMismatchError,
    get_default_timeline,
) 

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("live_assist")

app = BedrockAgentCoreApp()

_processor: Processor | None = None


_configs: ConfigCatalog | None = None
_meetings: MeetingRunner | None = None


def get_configs() -> ConfigCatalog:
    global _configs
    if _configs is None:
        _configs = ConfigCatalog(ConfigStore())
    return _configs


def get_processor() -> Processor:
    """Return the process-wide worker manager, created on first use."""
    global _processor
    if _processor is None:
        _processor = Processor(
            timeline=get_default_timeline(), store=get_default_store(), tracker=app,
            memory=get_default_memory(), accounts=AccountStore(),
        )
    return _processor


def get_meetings() -> MeetingRunner:
    """Return the process-wide meeting runner, created on first use."""
    global _meetings
    if _meetings is None:
        _meetings = MeetingRunner(timeline=get_default_timeline(), deliver=_deliver_in_process, tracker=app)
    return _meetings


async def _deliver_in_process(payload: dict) -> dict:
    """Hand an event the meeting source produced to the agent, through the same checks as any other sender."""
    try:
        event = parse_event(payload)
    except InvalidEventError as e:
        return _reject("invalid_event", str(e))
    return _apply(event, payload["type"])


@app.entrypoint
async def agent_invocation(payload: dict, context) -> dict:
    """Validate and store one event, then acknowledge it."""
    try:
        event = parse_event(payload)
    except InvalidEventError as e:
        return _reject("invalid_event", str(e))

    # A call must stay on one microVM, where its single worker runs.
    runtime_session_id = getattr(context, "session_id", None)
    if runtime_session_id and runtime_session_id != event.session_id:
        return _reject("session_mismatch", "session_id must equal the runtime session id")

    if isinstance(event, MeetingJoin):
        return await get_meetings().join(event)  # The event holds credentials: nothing from it is logged.
    return _apply(event, payload["type"])


def _apply(event, kind: str) -> dict:
    """Store an event and acknowledge it. Must run on the event loop: it schedules the worker."""
    try:
        ack = handle_event(event, timeline=get_default_timeline(), processor=get_processor(), configs=get_configs())
    except SessionNotFoundError:
        return _reject("unknown_session", "Unknown session_id. Send session.start first.")
    except SessionEndedError:
        return _reject("session_ended", "This session has ended.")
    except ConfigNotFoundError:
        return _reject("unknown_config", "Unknown or archived config profile.")
    except SessionNotEndedError:
        return _reject("session_live", "End the call before regenerating its minutes.")
    except SourceMismatchError:
        return _reject("source_mismatch", "This call takes turns only from its recording or audio source.")
    except Exception:
        logger.exception("event failed type=%s session=%r", kind, event.session_id)
        raise

    # Metadata only: transcript text is customer content and stays out of logs.
    logger.info("event accepted type=%s session=%r %s", kind, event.session_id, _log_fields(ack))
    return {"accepted": True, "type": kind, "session_id": event.session_id, **ack}


def _reject(code: str, error: str) -> dict:
    logger.info("event rejected code=%s: %s", code, error)
    return {"accepted": False, "code": code, "error": error}


def _log_fields(ack: dict) -> str:
    return " ".join(f"{k}={ack[k]}" for k in ("seq", "status", "created", "ended_now", "minutes_status") if k in ack)


if __name__ == "__main__":
    app.run()
