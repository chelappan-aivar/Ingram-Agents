"""Canonical input contract, ``ila.event.v1``.

Every source (recording replayer now, channel adapters later) sends these events
to the single entry point. One envelope, four kinds:

    session.start   create the call and its settings
    turn            one final spoken utterance
    session.update  change settings or pause/resume the source
    session.end     no more turns; flush pending work, then write the minutes
    session.summarize  regenerate the minutes of an ended call
    meeting.join    send a bot to a meeting and follow it (the call must already be started)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.assist_config import AssistConfig, InvalidAssistConfigError
from app.speaker_roles import parse_name_list
from app.transcript import (
    InvalidTranscriptChunkError,
    TranscriptChunk,
    parse_name,
    parse_timestamp,
)

SCHEMA = "ila.event.v1"

SESSION_START = "session.start"
TURN = "turn"
SESSION_UPDATE = "session.update"
SESSION_END = "session.end"
SESSION_SUMMARIZE = "session.summarize"
MEETING_JOIN = "meeting.join"
EVENT_TYPES = (SESSION_START, TURN, SESSION_UPDATE, SESSION_END, SESSION_SUMMARIZE, MEETING_JOIN)

STATUS_LIVE = "live"
STATUS_PAUSED = "paused"
STATUS_ENDED = "ended"
SETTABLE_STATUSES = (STATUS_LIVE, STATUS_PAUSED)  # "ended" only via session.end.

# Also the AgentCore runtime session id, which must be at least 33 characters.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{33,100}$")

# Account and opportunity ids: lowercase words joined by single hyphens. No "--", so
# "<account>--<opportunity>" is unambiguous in long-term memory actor ids.
ENTITY_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_ENTITY_ID_CHARS = 40

MAX_TEXT_CHARS = 10_000
MAX_DESCRIPTION_CHARS = 2_000  # The meeting description or agenda a BA pastes in.
MAX_SHORT_FIELD_CHARS = 200
MAX_MEETING_URL_CHARS = 2048
MIN_SECRET_CHARS, MAX_SECRET_CHARS = 8, 300
_SECRET_RE = re.compile(r"^[\x21-\x7e]+$")  # Printable ASCII, no spaces: how API keys look.

_ALLOWED_KEYS = {
    SESSION_START: {
        "schema", "type", "session_id", "source", "title", "assist_config",
        "account_id", "opportunity_id", "opportunity_type", "config_id", "config_version",
        "description", "prepare_questions",
    },
    TURN: {
        "schema", "type", "session_id", "source", "speaker", "speaker_name", "text",
        "spoken_at", "is_final", "event_id",
    },
    SESSION_UPDATE: {"schema", "type", "session_id", "assist_config", "status"},
    SESSION_END: {"schema", "type", "session_id", "reason"},
    SESSION_SUMMARIZE: {"schema", "type", "session_id"},
    MEETING_JOIN: {"schema", "type", "session_id", "meeting_url", "ba_names", "bot_name",
                   "vexa_api_key", "vexa_tx_key"},
}


class InvalidEventError(ValueError):
    """Raised when an event does not match ``ila.event.v1``."""


@dataclass(frozen=True)
class SessionStart:
    session_id: str
    source: str
    title: str | None
    assist_config: AssistConfig
    account_id: str | None = None  # Enables long-term memory for the call.
    opportunity_id: str | None = None  # Requires account_id.
    preset_opportunity_type: str | None = None  # Set and locked from the first run.
    config_id: str | None = None  # Guidance profile; None is the built-in one.
    config_version: int | None = None  # None is the profile's current version.
    description: str | None = None  # Meeting description or agenda, typed by the BA. Data, never instructions.
    prepare_questions: bool = False  # Ask the agent to write questions to prepare from the title and description.


@dataclass(frozen=True)
class Turn:
    session_id: str
    source: str
    speaker: str
    text: str
    speaker_name: str | None = None
    spoken_at: str | None = None  # UTC, YYYY-MM-DDTHH:MM:SSZ
    event_id: str | None = None

    def to_chunk(self) -> TranscriptChunk:
        return TranscriptChunk(
            speaker=self.speaker, text=self.text, name=self.speaker_name, timestamp=self.spoken_at
        )


@dataclass(frozen=True)
class SessionUpdate:
    session_id: str
    assist_config: AssistConfig | None
    status: str | None


@dataclass(frozen=True)
class SessionEnd:
    session_id: str
    reason: str | None


@dataclass(frozen=True)
class SessionSummarize:
    session_id: str


@dataclass(frozen=True)
class MeetingJoin:
    """Follow a meeting. The Vexa keys come from the person using the console and are only ever held
    in memory: ``repr=False`` keeps them out of logs, tracebacks and debug output."""

    session_id: str
    meeting_url: str
    ba_names: tuple[str, ...]
    bot_name: str | None
    vexa_api_key: str = field(repr=False)
    vexa_tx_key: str = field(default="", repr=False)  # Only when vexa_api_key cannot read transcripts.


Event = SessionStart | Turn | SessionUpdate | SessionEnd | SessionSummarize | MeetingJoin


def parse_event(payload: object) -> Event:
    """Validate a payload and return the typed event.

    Raises:
        InvalidEventError: The payload is not a valid ``ila.event.v1`` event.
    """
    if not isinstance(payload, dict):
        raise InvalidEventError("event must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise InvalidEventError(f"'schema' must be '{SCHEMA}'")

    kind = payload.get("type")
    if kind not in EVENT_TYPES:
        raise InvalidEventError(f"'type' must be one of {list(EVENT_TYPES)}")

    unknown = set(payload) - _ALLOWED_KEYS[kind]
    if unknown:
        raise InvalidEventError(f"unknown keys for '{kind}': {sorted(unknown)}")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise InvalidEventError(
            "'session_id' must be 33-100 characters of letters, digits, '_' or '-'"
        )

    if kind == SESSION_START:
        account_id = _entity_id(payload, "account_id")
        opportunity_id = _entity_id(payload, "opportunity_id")
        if opportunity_id and not account_id:
            raise InvalidEventError("'opportunity_id' needs 'account_id'")
        return SessionStart(
            session_id=session_id,
            source=_short_text(payload, "source", default="unknown"),
            title=_short_text(payload, "title", default=None),
            assist_config=_assist_config(payload.get("assist_config")),
            account_id=account_id,
            opportunity_id=opportunity_id,
            preset_opportunity_type=_short_text(payload, "opportunity_type", default=None),
            config_id=_entity_id(payload, "config_id"),
            config_version=_config_version(payload.get("config_version")),
            description=parse_description(payload.get("description")),
            prepare_questions=_flag(payload, "prepare_questions"),
        )
    if kind == TURN:
        return _parse_turn(session_id, payload)
    if kind == SESSION_UPDATE:
        return _parse_update(session_id, payload)
    if kind == SESSION_SUMMARIZE:
        return SessionSummarize(session_id=session_id)
    if kind == MEETING_JOIN:
        return _parse_meeting_join(session_id, payload)
    return SessionEnd(session_id=session_id, reason=_short_text(payload, "reason", default=None))


def _parse_turn(session_id: str, payload: dict) -> Turn:
    if payload.get("is_final", True) is not True:
        raise InvalidEventError("only final turns are accepted; drop interim results upstream")

    text = payload.get("text")
    if isinstance(text, str) and len(text) > MAX_TEXT_CHARS:
        raise InvalidEventError(f"'text' must be at most {MAX_TEXT_CHARS} characters")

    try:
        chunk = TranscriptChunk.from_payload({"speaker": payload.get("speaker"), "text": text})
        speaker_name = parse_name(payload.get("speaker_name"))
        spoken_at = parse_timestamp(payload.get("spoken_at"))
    except InvalidTranscriptChunkError as e:
        # Reword the transcript module's field names to this contract's.
        raise InvalidEventError(
            str(e).replace("transcript_chunk.name", "speaker_name")
            .replace("transcript_chunk.timestamp", "spoken_at")
            .replace("transcript_chunk.", "")
        ) from e

    return Turn(
        session_id=session_id,
        source=_short_text(payload, "source", default="unknown"),
        speaker=chunk.speaker,
        text=chunk.text,
        speaker_name=speaker_name,
        spoken_at=spoken_at,
        event_id=_short_text(payload, "event_id", default=None),
    )


def _parse_meeting_join(session_id: str, payload: dict) -> MeetingJoin:
    url = payload.get("meeting_url")
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_MEETING_URL_CHARS:
        raise InvalidEventError(f"'meeting_url' must be a link of at most {MAX_MEETING_URL_CHARS} characters")
    try:
        ba_names = tuple(parse_name_list(payload.get("ba_names")))
    except ValueError as e:
        raise InvalidEventError(f"'ba_names': {e}") from None
    return MeetingJoin(
        session_id=session_id,
        meeting_url=url.strip(),
        ba_names=ba_names,
        bot_name=_short_text(payload, "bot_name", default=None),
        vexa_api_key=_secret(payload, "vexa_api_key", required=True),
        vexa_tx_key=_secret(payload, "vexa_tx_key", required=False),
    )


def _secret(payload: dict, key: str, *, required: bool) -> str:
    """A credential. Errors name the field but never repeat the value."""
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise InvalidEventError(f"'{key}' is required")
        return ""
    if not isinstance(value, str) or not MIN_SECRET_CHARS <= len(value.strip()) <= MAX_SECRET_CHARS \
            or not _SECRET_RE.match(value.strip()):
        raise InvalidEventError(f"'{key}' does not look like an API key")
    return value.strip()


def _parse_update(session_id: str, payload: dict) -> SessionUpdate:
    status = payload.get("status")
    if status is not None and status not in SETTABLE_STATUSES:
        raise InvalidEventError(
            f"'status' must be one of {list(SETTABLE_STATUSES)}; use session.end to end a call"
        )
    config_payload = payload.get("assist_config")
    if config_payload is None and status is None:
        raise InvalidEventError("session.update needs 'assist_config', 'status' or both")
    return SessionUpdate(
        session_id=session_id,
        assist_config=_assist_config(config_payload) if config_payload is not None else None,
        status=status,
    )


def _assist_config(payload: object) -> AssistConfig:
    try:
        return AssistConfig.from_payload(payload)
    except InvalidAssistConfigError as e:
        raise InvalidEventError(str(e)) from e


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def parse_description(value: object) -> str | None:
    """A meeting description: text with line breaks kept, control characters removed, length capped.

    Raises:
        InvalidEventError: It is not text, or it is longer than ``MAX_DESCRIPTION_CHARS``.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidEventError("'description' must be text")
    text = _CONTROL_CHARS.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise InvalidEventError(f"'description' must be at most {MAX_DESCRIPTION_CHARS} characters")
    return text or None


def _flag(payload: dict, key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise InvalidEventError(f"'{key}' must be true or false")
    return value


def _short_text(payload: dict, key: str, *, default: str | None) -> str | None:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise InvalidEventError(f"'{key}' must be a non-empty string")
    value = value.strip()
    if len(value) > MAX_SHORT_FIELD_CHARS:
        raise InvalidEventError(f"'{key}' must be at most {MAX_SHORT_FIELD_CHARS} characters")
    return value


def _config_version(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidEventError("'config_version' must be a non-negative integer")
    return value


def valid_entity_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_ENTITY_ID_CHARS and bool(ENTITY_ID_RE.match(value))


def _entity_id(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not valid_entity_id(value):
        raise InvalidEventError(
            f"'{key}' must be up to {MAX_ENTITY_ID_CHARS} lowercase letters and digits, "
            "in words joined by single hyphens"
        )
    return value
