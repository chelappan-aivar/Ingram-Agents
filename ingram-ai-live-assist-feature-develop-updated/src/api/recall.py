"""Recall.ai as the live-meeting source (``MEETING_PROVIDER=recall``).

Vexa and Recall work in opposite directions:

* Vexa: the agent runtime connects out to Vexa and holds a WebSocket open for the whole meeting.
* Recall: this API sends the bot, then Recall pushes signed webhooks to this API. Each webhook is
  turned into an ``ila.event.v1`` event and sent to the runtime like any other source.

So nothing here keeps state between requests. The call's session id and the partner's names travel
inside the bot's ``metadata``, which Recall returns on every transcript webhook.

Routes (no ``X-Access-Key``: Recall cannot send one; every request is verified by its signature):

* ``POST /recall/transcript``  real-time ``transcript.data`` events (set per bot when it is created)
* ``POST /recall/status``      ``bot.status_change`` events (set once in the Recall dashboard)

The HTTP client uses only the standard library so the Lambda zip needs no extra dependency.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from api.http import ApiError, Request, Response
from app.events import MAX_TEXT_CHARS, SCHEMA, SESSION_ID_RE
from app.meeting_link import InvalidMeetingLinkError, parse_meeting_link
from app.speaker_roles import RoleResolver, parse_name_list
from app.transcript import MAX_NAME_LENGTH, parse_name

logger = logging.getLogger(__name__)

# Same value as app.meeting_source.SOURCE. Not imported: that module needs libraries the Lambda zip lacks.
SOURCE = "meeting"
UNKNOWN_SPEAKER = "Unknown"

DEFAULT_REGION = "us-west-2"
STREAMING_MODES = ("prioritize_low_latency", "prioritize_accuracy")
HTTP_TIMEOUT_SECONDS = 10
SIGNATURE_TOLERANCE_SECONDS = 300
# Recall says call_ended means the bot left and real-time transcription is complete, but webhooks are
# delivered one after another. A short wait lets the last sentences arrive before the call is ended.
END_GRACE_SECONDS = 4.0
MAX_METADATA_NAMES_CHARS = 500

# Status codes that end the call. "done" follows "call_ended", so it is not needed.
ENDING_STATUS_CODES = frozenset({"call_ended", "fatal"})
# The newer per-status webhook events and the status code each stands for.
ENDING_EVENTS = {"bot.call_ended": "call_ended", "bot.fatal": "fatal"}
# Runtime rejections that retrying cannot fix: answer 200 so Recall stops resending.
_DROP_ACK_CODES = frozenset({"unknown_session", "source_mismatch", "invalid_event"})

_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_SUB_CODE_BAD = re.compile(r"[^a-z0-9_]")

# Transport: (method, url, headers, body) -> (status, body). Replaced in tests.
Http = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class RecallError(Exception):
    """A Recall call failed. ``status`` is the HTTP status (503 when Recall could not be reached)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Recall {status}: {detail}")
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class RecallSettings:
    region: str
    public_url: str  # The address Recall can reach this API on, without a trailing slash.
    api_key: Callable[[], str | None] = field(repr=False)
    webhook_secret: Callable[[], str | None] = field(repr=False)
    streaming_mode: str = "prioritize_low_latency"
    language_code: str = "en"
    separate_streams: bool = True  # Best speaker names; Recall says it can raise cost when people overlap.

    @classmethod
    def from_env(cls, env: dict[str, str], *, api_key: Callable[[], str | None],
                 webhook_secret: Callable[[], str | None]) -> RecallSettings:
        region = (env.get("RECALL_REGION") or DEFAULT_REGION).strip()
        if not _REGION_RE.match(region):
            raise ValueError("RECALL_REGION must look like us-west-2")
        public_url = (env.get("PUBLIC_API_URL") or "").strip().rstrip("/")
        if not public_url.startswith("https://"):
            raise ValueError("PUBLIC_API_URL must be the https address Recall can reach")
        mode = (env.get("RECALL_STREAMING_MODE") or "prioritize_low_latency").strip()
        if mode not in STREAMING_MODES:
            raise ValueError(f"RECALL_STREAMING_MODE must be one of {list(STREAMING_MODES)}")
        language = (env.get("RECALL_LANGUAGE_CODE") or "en").strip()
        if mode == "prioritize_low_latency" and language != "en":
            raise ValueError("RECALL_LANGUAGE_CODE must be 'en' in prioritize_low_latency mode")
        return cls(
            region=region, public_url=public_url, api_key=api_key, webhook_secret=webhook_secret,
            streaming_mode=mode, language_code=language,
            separate_streams=(env.get("RECALL_SEPARATE_STREAMS") or "true").strip().lower() == "true",
        )

    @property
    def api_base(self) -> str:
        return f"https://{self.region}.recall.ai/api/v1"


def _urllib_http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - https only
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RecallError(503, f"Recall could not be reached ({type(e).__name__})") from None


def _detail(status: int, body: bytes) -> str:
    try:
        data = json.loads(body)
        text = data.get("detail") if isinstance(data, dict) else None
        return (text if isinstance(text, str) else json.dumps(data))[:300]
    except (ValueError, TypeError):
        return body.decode("utf-8", "replace")[:300] or f"HTTP {status}"


class RecallClient:
    """The three Recall calls this project needs. Never logs or returns the API key."""

    def __init__(self, settings: RecallSettings, http: Http = _urllib_http) -> None:
        self._s = settings
        self._http = http

    def _call(self, method: str, path: str, payload: dict | None = None) -> Any:
        try:
            key = self._s.api_key()
        except Exception:  # noqa: BLE001 - a missing secret is a setup problem, not a crash
            logger.exception("recall api key could not be read")
            key = None
        if not key:
            raise RecallError(500, "the Recall API key is not configured")
        headers = {"Authorization": key.strip(), "Accept": "application/json", "Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, raw = self._http(method, f"{self._s.api_base}{path}", headers, body)
        if status >= 300:
            raise RecallError(status, _detail(status, raw))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise RecallError(502, "Recall sent an unreadable answer") from None

    def create_bot(self, *, meeting_url: str, bot_name: str, session_id: str, ba_names: list[str]) -> str:
        """Send the bot. Returns its id."""
        s = self._s
        provider = {"mode": s.streaming_mode, "language_code": s.language_code}
        transcript: dict[str, Any] = {"provider": {"recallai_streaming": provider}}
        if s.separate_streams:
            transcript["diarization"] = {"use_separate_streams_when_available": True}
        out = self._call("POST", "/bot/", {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "metadata": {"session_id": session_id, "ba_names": ", ".join(ba_names)[:MAX_METADATA_NAMES_CHARS]},
            "recording_config": {
                "transcript": transcript,
                "realtime_endpoints": [{
                    "type": "webhook", "url": f"{s.public_url}/recall/transcript", "events": ["transcript.data"],
                }],
            },
        })
        bot_id = out.get("id") if isinstance(out, dict) else None
        if not isinstance(bot_id, str) or not bot_id:
            raise RecallError(502, "Recall did not return a bot id")
        return bot_id

    def leave_call(self, bot_id: str) -> None:
        """Make the bot leave. A bot that is already gone counts as done."""
        try:
            self._call("POST", f"/bot/{bot_id}/leave_call/")
        except RecallError as e:
            if e.status not in (400, 404):
                raise

    def bot_metadata(self, bot_id: str) -> dict:
        """The metadata given when the bot was created."""
        out = self._call("GET", f"/bot/{bot_id}/")
        metadata = out.get("metadata") if isinstance(out, dict) else None
        return metadata if isinstance(metadata, dict) else {}


# --- signature -----------------------------------------------------------------------------------


def verify_signature(secret: str | None, headers: dict[str, str], body: bytes, *, now: float | None = None) -> bool:
    """Check a request from Recall (HMAC-SHA256 over ``id.timestamp.body``, Svix style).

    ``headers`` has lowercase names. Accepts the ``webhook-*`` headers (workspace secret) and the older
    ``svix-*`` ones. Requests older than five minutes are refused, so a captured one cannot be replayed.
    """
    if not secret or not secret.startswith("whsec_"):
        return False
    msg_id = headers.get("webhook-id") or headers.get("svix-id")
    timestamp = headers.get("webhook-timestamp") or headers.get("svix-timestamp")
    signature = headers.get("webhook-signature") or headers.get("svix-signature")
    if not (msg_id and timestamp and signature):
        return False
    try:
        sent_at = int(timestamp)
        key = base64.b64decode(secret[len("whsec_"):], validate=True)
    except (ValueError, binascii.Error):
        return False
    if abs((time.time() if now is None else now) - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    )
    for part in signature.split(" "):  # Several signatures appear while a secret is being rotated.
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(value.encode(), expected):
            return True
    return False


# --- the source ------------------------------------------------------------------------------------


def _iso_now(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_start_ms(words: list) -> int:
    try:
        return int(float(words[0]["start_timestamp"]["relative"]) * 1000)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


class RecallSource:
    """Starts Recall bots for calls and turns Recall's webhooks into agent events."""

    def __init__(self, settings: RecallSettings, *, sender: Any, http: Http = _urllib_http,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self._s = settings
        self._sender = sender
        self.client = RecallClient(settings, http)
        self._clock = clock
        self._sleep = sleep

    def routes(self) -> list[tuple[str, str, Callable]]:
        return [("POST", r"/recall/transcript", self.transcript), ("POST", r"/recall/status", self.status)]

    # --- starting a meeting call --------------------------------------------------------------------

    def start(self, *, meeting_url: str, bot_name: str, session_id: str, ba_names: list[str]) -> None:
        """Send the bot to the meeting. Raises ``ApiError`` with the codes the console already knows."""
        try:
            link = parse_meeting_link(meeting_url)
        except InvalidMeetingLinkError as e:
            raise ApiError(400, "invalid_meeting_link", str(e)) from None
        try:
            bot_id = self.client.create_bot(
                meeting_url=link.url, bot_name=bot_name, session_id=session_id, ba_names=ba_names)
        except RecallError as e:
            raise _api_error(e) from None
        logger.info("recall bot requested session=%r bot=%s platform=%s", session_id, bot_id, link.platform)

    # --- webhooks ----------------------------------------------------------------------------------------

    def _verified_json(self, req: Request) -> dict:
        try:
            secret = self._s.webhook_secret()
        except Exception:  # noqa: BLE001
            logger.exception("recall webhook secret could not be read")
            secret = None
        if not secret:
            raise ApiError(500, "recall_not_configured", "The Recall verification secret is not configured")
        if not verify_signature(secret, req.headers, req.body, now=self._clock()):
            raise ApiError(401, "invalid_signature", "The request is not signed by Recall")
        return req.json()

    def transcript(self, req: Request) -> Response:
        body = self._verified_json(req)
        if body.get("event") != "transcript.data":
            return Response(200, {"ignored": True})
        parsed = self._turn_event(body)
        if parsed is None:
            return Response(200, {"ignored": True})
        event, bot_id = parsed
        ack = self._send(event)
        if ack.get("accepted"):
            return Response(200, {"accepted": True})
        code = ack.get("code") or "rejected"
        if code == "session_ended":
            # The call ended (for example from the console) while the bot is still in the meeting.
            self._leave_quietly(bot_id)
            return Response(200, {"accepted": False, "code": code})
        if code in _DROP_ACK_CODES:
            logger.warning("recall turn dropped session=%r code=%s", event["session_id"], code)
            return Response(200, {"accepted": False, "code": code})
        raise ApiError(502, code, ack.get("error") or "Rejected by the agent")

    def status(self, req: Request) -> Response:
        body = self._verified_json(req)
        change = _status_change(body)
        if change is None:
            return Response(200, {"ignored": True})
        code, sub_code, bot_id, metadata = change

        if metadata is None or "session_id" not in metadata:
            # The older bot.status_change event carries no metadata: ask Recall for the bot's.
            try:
                metadata = self.client.bot_metadata(bot_id)
            except RecallError as e:
                if e.status == 404:
                    return Response(200, {"ignored": True})
                raise _api_error(e) from None
        session_id = metadata.get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
            return Response(200, {"ignored": True})

        if code == "call_ended":
            self._sleep(END_GRACE_SECONDS)
            reason = "meeting ended"
        else:
            sub_code = _SUB_CODE_BAD.sub("", str(sub_code or "").lower())[:60]
            reason = f"the meeting bot failed ({sub_code})" if sub_code else "the meeting bot failed"
        ack = self._send({"schema": SCHEMA, "type": "session.end", "session_id": session_id, "reason": reason})
        return Response(200, {"accepted": bool(ack.get("accepted")), "code": ack.get("code")})

    # --- helpers --------------------------------------------------------------------------------------------

    def _turn_event(self, body: dict) -> tuple[dict, str] | None:
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
        metadata = bot.get("metadata") if isinstance(bot.get("metadata"), dict) else {}
        bot_id, session_id = bot.get("id"), metadata.get("session_id")
        if not isinstance(bot_id, str) or not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
            return None

        words = [w for w in (inner.get("words") or []) if isinstance(w, dict)]
        text = " ".join(w["text"].strip() for w in words if isinstance(w.get("text"), str) and w["text"].strip())
        if not text:
            return None

        participant = inner.get("participant") if isinstance(inner.get("participant"), dict) else {}
        raw_name = participant.get("name")
        name = parse_name(raw_name) if isinstance(raw_name, str) else None
        try:
            ba_names = parse_name_list(metadata.get("ba_names"))
        except ValueError:
            ba_names = []
        event = {
            "schema": SCHEMA, "type": "turn", "session_id": session_id, "source": SOURCE,
            "speaker": RoleResolver(ba_names).role_for(name),
            "speaker_name": (name or UNKNOWN_SPEAKER)[:MAX_NAME_LENGTH],
            "text": text[:MAX_TEXT_CHARS], "spoken_at": _iso_now(self._clock), "is_final": True,
            "event_id": f"recall-{bot_id}-{participant.get('id', 'x')}-{_first_start_ms(words)}"[:200],
        }
        return event, bot_id

    def _send(self, event: dict) -> dict:
        try:
            return self._sender.send(event)
        except Exception:  # noqa: BLE001
            logger.exception("agent unavailable type=%s", event.get("type"))
            # Not a 2xx: Recall resends, and the turn is not lost.
            raise ApiError(502, "agent_unavailable", "The agent did not accept the request") from None

    def _leave_quietly(self, bot_id: str) -> None:
        try:
            self.client.leave_call(bot_id)
        except Exception:  # noqa: BLE001 - the call is already over; a bot left behind is only a cost
            logger.warning("recall bot did not leave bot=%s", bot_id, exc_info=True)


def _status_change(body: dict) -> tuple[str, object, str, dict | None] | None:
    """(code, sub_code, bot id, metadata) of a webhook that ends the call, or None for anything else.

    Recall sends bot status in two shapes, and the dashboard decides which one arrives:
    * ``bot.call_ended`` / ``bot.fatal`` (newer): the bot and its metadata come with the event.
    * ``bot.status_change`` (older): only the bot id and a status code; the metadata must be fetched.
    """
    event = body.get("event")
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if event == "bot.status_change":
        status = data.get("status") if isinstance(data.get("status"), dict) else {}
        code, sub_code, bot_id, metadata = status.get("code"), status.get("sub_code"), data.get("bot_id"), None
    elif event in ENDING_EVENTS:
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
        code, sub_code, bot_id = ENDING_EVENTS[event], inner.get("sub_code"), bot.get("id")
        metadata = bot.get("metadata") if isinstance(bot.get("metadata"), dict) else None
    else:
        return None
    if code not in ENDING_STATUS_CODES or not isinstance(bot_id, str) or not bot_id:
        return None
    return code, sub_code, bot_id, metadata


def _api_error(error: RecallError) -> ApiError:
    """The codes match the Vexa ones, so the console shows the same messages."""
    if error.status in (401, 403):
        return ApiError(502, "meeting_not_authorized", f"The meeting service refused the key: {error.detail}")
    if error.status == 429:
        return ApiError(429, "meeting_limit", "The meeting service limit was reached. Try again later.")
    if error.status in (400, 422):
        return ApiError(400, "invalid_meeting_link", f"The meeting service could not use this link: {error.detail}")
    if error.status == 507:
        return ApiError(502, "meeting_unavailable", "No meeting bot was available. Try again in a minute.")
    if error.status == 500 and "not configured" in error.detail:
        return ApiError(502, "meeting_not_configured", "The meeting service is not configured on the server.")
    return ApiError(502, "meeting_unavailable", f"The meeting service did not accept the request: {error.detail}")
