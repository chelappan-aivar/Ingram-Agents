"""Calls fed by a live meeting link or a pasted transcript: validation and event building.

``source.type`` values handled here (the rest are in ``api/app.py``):

* ``meeting``: ``{"type": "meeting", "meeting_url": "...", "ba_names": ["Ravi Menon"], "bot_name": "..."}``
  plus, with ``MEETING_PROVIDER=vexa`` only, ``"vexa_api_key": "...", "vexa_tx_key": "..."``.
  - vexa: the agent runtime sends a Vexa bot into the meeting and its sentences become turns as they are
    spoken. The Vexa keys are typed in the console. They are forwarded to the runtime in one ``meeting.join``
    event and are never stored or logged here.
  - recall: ``api/recall.py`` sends a Recall.ai bot. No key is typed in the console: the Recall keys are in
    Secrets Manager. Any Vexa keys in the request are ignored.
* ``transcript``: ``{"type": "transcript", "text": "...", "ba_names": [...], "speed": 5}``
  The transcript is played into the call at speaking pace, like a recording.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from api.http import ApiError
from app.events import MEETING_JOIN, SCHEMA, InvalidEventError, parse_event
from app.meeting_link import PLATFORMS, InvalidMeetingLinkError, parse_meeting_link
from app.speaker_roles import MAX_BA_NAMES, parse_name_list
from app.transcript_text import MAX_TRANSCRIPT_CHARS, InvalidTranscriptTextError, transcript_to_conversation

DEFAULT_BOT_NAME = "Ingram Live Assist"
MAX_BOT_NAME_CHARS = 60
TRANSCRIPT_MAX_SPEED = 100.0
TRANSCRIPT_DEFAULT_SPEED = 5.0
TRANSCRIPT_MAX_GAP_SECONDS = 8.0
MAX_JOB_BYTES = 200_000  # Async Lambda invocations are limited to 256 KB.
TRANSCRIPT_FORMATS = ["txt", "vtt", "srt", "json"]
_BOT_NAME_BAD = re.compile(r"[^\w .'\-]", re.UNICODE)
_PROBE_SESSION_ID = "p" * 33  # Lets the event parser check a request before the call has an id.


@dataclass(frozen=True)
class MeetingPlan:
    """A checked meeting request. The keys are secrets: ``repr=False`` keeps them out of logs."""

    meeting_url: str
    ba_names: list[str]
    bot_name: str
    vexa_api_key: str = field(default="", repr=False)  # Empty with the recall provider.
    vexa_tx_key: str = field(default="", repr=False)


class LiveSources:
    def __init__(self, provider: str = "vexa", *, meeting_ready: bool = True, not_ready_reason: str = "") -> None:
        self.provider = provider
        self._ready = meeting_ready
        self._reason = not_ready_reason

    # --- what /options reports ----------------------------------------------------------

    def options(self) -> dict:
        meeting: dict = {"enabled": self._ready, "provider": self.provider, "platforms": list(PLATFORMS),
                         "max_ba_names": MAX_BA_NAMES, "needs_vexa_key": self.provider == "vexa"}
        if not self._ready:
            meeting["reason"] = self._reason
        return {
            "meeting": meeting,
            "transcript": {"max_chars": MAX_TRANSCRIPT_CHARS, "formats": TRANSCRIPT_FORMATS,
                           "speed": {"min_exclusive": 0, "max": TRANSCRIPT_MAX_SPEED,
                                     "default": TRANSCRIPT_DEFAULT_SPEED}},
        }

    # --- transcript -----------------------------------------------------------------------

    def transcript_job(self, source: dict) -> dict:
        """A replayer job that plays the pasted transcript into the call."""
        ba_names = _ba_names(source)
        speed = _speed(source)
        try:
            conversation = transcript_to_conversation(source.get("text"), ba_names)
        except InvalidTranscriptTextError as e:
            raise ApiError(400, "invalid_upload", str(e)) from None
        job = {"mode": "recording", "conversation": conversation, "speed": speed,
               "max_gap_seconds": TRANSCRIPT_MAX_GAP_SECONDS}
        if len(json.dumps(job)) > MAX_JOB_BYTES:
            raise ApiError(400, "invalid_upload", "The transcript is too long. Split it into parts.")
        return job

    # --- meeting --------------------------------------------------------------------------

    def meeting_plan(self, source: dict) -> MeetingPlan:
        """Check a meeting request before the call exists, so a bad one never creates a call."""
        try:
            parse_meeting_link(source.get("meeting_url"))
        except InvalidMeetingLinkError as e:
            raise ApiError(400, "invalid_meeting_link", str(e)) from None
        bot_name = source.get("bot_name") or DEFAULT_BOT_NAME
        if not isinstance(bot_name, str):
            raise ApiError(400, "invalid_request", "'source.bot_name' must be text")
        bot_name = _BOT_NAME_BAD.sub("", bot_name).strip()[:MAX_BOT_NAME_CHARS] or DEFAULT_BOT_NAME
        vexa = self.provider == "vexa"
        plan = MeetingPlan(
            meeting_url=source["meeting_url"].strip(),
            ba_names=_ba_names(source),
            bot_name=bot_name,
            vexa_api_key=_key(source, "vexa_api_key") if vexa else "",
            vexa_tx_key=_key(source, "vexa_tx_key") if vexa else "",
        )
        if vexa:
            try:
                parse_event(self.join_event(plan, _PROBE_SESSION_ID))
            except InvalidEventError as e:
                raise ApiError(400, "invalid_request", f"'source': {e}") from None
        return plan

    def join_event(self, plan: MeetingPlan, session_id: str) -> dict:
        """The ``meeting.join`` event for the runtime. It carries the keys, so never log it."""
        event = {"schema": SCHEMA, "type": MEETING_JOIN, "session_id": session_id,
                 "meeting_url": plan.meeting_url, "ba_names": plan.ba_names, "bot_name": plan.bot_name,
                 "vexa_api_key": plan.vexa_api_key}
        if plan.vexa_tx_key:
            event["vexa_tx_key"] = plan.vexa_tx_key
        return event


# --- helpers ---------------------------------------------------------------------------------


def _key(source: dict, name: str) -> str:
    """A key as typed. Missing keys are reported by the event check; the value is never echoed."""
    value = source.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", f"'source.{name}' must be text")
    return value.strip()


def _ba_names(source: dict) -> list[str]:
    try:
        return parse_name_list(source.get("ba_names"))
    except ValueError as e:
        raise ApiError(400, "invalid_request", f"'source.ba_names': {e}") from None


def _speed(source: dict) -> float:
    value = source.get("speed", TRANSCRIPT_DEFAULT_SPEED)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= TRANSCRIPT_MAX_SPEED:
        raise ApiError(400, "invalid_request", f"'source.speed' must be above 0 and at most {TRANSCRIPT_MAX_SPEED:g}")
    return float(value)
