"""Client for Vexa's meeting API: send a bot to a meeting and read its live transcript.

Protocol (docs.vexa.ai, checked against live frames from the hosted service):

* Auth is the ``X-API-Key`` header on REST and WebSocket. Keys have scopes: a ``bot`` key can
  start and stop bots but is refused (403) on transcripts; reading them needs the ``tx`` scope.
  A separate key can be given for reading (``tx_key``).
* The keys are never read from the environment or stored: the person starting the call types them
  in the console and they arrive in the ``meeting.join`` event.
* ``POST /bots`` sends the bot, ``DELETE /bots/{platform}/{id}`` removes it.
* ``/ws``: send ``{"action": "subscribe", "meetings": [{"platform", "native_id"}]}``. Frames:

  - ``{"type": "transcript", "speaker": key, "confirmed": [...], "pending": [...]}``.
    ``confirmed`` segments are final; ``pending`` ones are drafts that keep changing.
  - ``{"type": "transcript_retract", "segment_ids": [...]}``
  - ``{"type": "meeting.status", "payload": {"status": ...}}``
  - older: ``{"type": "transcript.mutable", "payload": {"segments": [...]}}`` (``completed`` flag)

* A segment has a stable ``segment_id``, ``text``, ``speaker`` (may be empty: Vexa leaves it blank
  rather than guess) and ``speaker_key`` (an opaque id).

Latency: Vexa confirms a sentence some time after it is spoken. To keep the assist close to real
time, a draft whose text has not changed for ``promote_after_s`` seconds (the speaker paused) is
delivered as final. When Vexa later confirms that segment it is ignored, so a sentence is never
delivered twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import httpx
import websockets

from app.meeting_link import ZOOM, MeetingLink

logger = logging.getLogger(__name__)

# At DEBUG these libraries log every request and handshake header, and X-API-Key holds the caller's Vexa key.
for _noisy in ("websockets", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

DEFAULT_API_BASE = "https://api.cloud.vexa.ai"
DEFAULT_PROMOTE_AFTER_SECONDS = 5.0
MIN_PROMOTE_WORDS = 3
PING_INTERVAL_SECONDS = 25
TICK_SECONDS = 0.25  # How often drafts are checked for stability.
HTTP_TIMEOUT_SECONDS = 10  # Starting a call is one request through API Gateway, which allows 30 s in all.
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_REMEMBERED_SEGMENTS = 5000
TERMINAL_STATUSES = frozenset({"completed", "failed"})


class VexaError(Exception):
    """A Vexa call failed. ``status`` is the HTTP status (503 when Vexa could not be reached)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Vexa {status}: {detail}")
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class VexaSettings:
    api_key: str = field(default="", repr=False)
    tx_key: str = field(default="", repr=False)  # Key with the transcript scope; api_key when empty.
    api_base: str = DEFAULT_API_BASE
    ws_url: str = ""  # Derived from api_base when empty.
    promote_after_s: float = DEFAULT_PROMOTE_AFTER_SECONDS  # 0 turns promotion off.

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> VexaSettings:
        base = (env.get("VEXA_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        if not base.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("VEXA_API_BASE must be https (plain http only for localhost)")
        try:
            promote = float(env.get("VEXA_PROMOTE_AFTER_SECONDS", DEFAULT_PROMOTE_AFTER_SECONDS))
        except ValueError:
            raise ValueError("VEXA_PROMOTE_AFTER_SECONDS must be a number") from None
        return cls(
            api_base=base,
            ws_url=(env.get("VEXA_WS_URL") or "").strip(),
            promote_after_s=max(0.0, promote),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def stream_url(self) -> str:
        if self.ws_url:
            return self.ws_url
        scheme, _, rest = self.api_base.partition("://")
        return f"{'wss' if scheme == 'https' else 'ws'}://{rest}/ws"

    @property
    def bot_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    @property
    def read_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.tx_key or self.api_key}


# --- REST --------------------------------------------------------------------------------


def _detail(response: httpx.Response) -> str:
    """The service's explanation, in plain words when it gives one (for example, out of credit)."""
    try:
        body = response.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):
            return detail["message"][:300]
        return (detail if isinstance(detail, str) else json.dumps(detail))[:300]
    except ValueError:
        return response.text[:300]


async def _call(method: str, url: str, headers: dict[str, str], **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            return await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as e:
        raise VexaError(503, f"Vexa could not be reached ({type(e).__name__})") from None


async def check_read_access(settings: VexaSettings, link: MeetingLink) -> None:
    """Fail early if the key cannot read transcripts, instead of joining a meeting that stays silent.

    Raises:
        VexaError: The key is refused. A 404 (no such meeting yet) is fine.
    """
    response = await _call("GET", f"{settings.api_base}/transcripts/{link.platform}/{link.native_id}",
                           settings.read_headers)
    if response.status_code in (401, 403):
        raise VexaError(response.status_code,
                        "this key cannot read transcripts; use a key with the transcript (tx) scope, or enter one in the transcription key field")


async def request_bot(settings: VexaSettings, link: MeetingLink, bot_name: str) -> None:
    """Send the bot to the meeting.

    Raises:
        VexaError: Vexa refused the request (bad key, meeting already has a bot, limit reached...).
    """
    body: dict = {"bot_name": bot_name, "transcribe_enabled": True}
    if link.platform == ZOOM:
        body["meeting_url"] = link.url  # Vexa reads the id and the passcode from the link itself.
    else:
        body.update(platform=link.platform, native_meeting_id=link.native_id)
        if link.passcode:
            body["passcode"] = link.passcode
    response = await _call("POST", f"{settings.api_base}/bots", settings.bot_headers, json=body)
    if response.status_code >= 300:
        raise VexaError(response.status_code, _detail(response))


async def stop_bot(settings: VexaSettings, link: MeetingLink) -> None:
    """Make the bot leave. Already gone (404) counts as done.

    Raises:
        VexaError: Vexa refused the request.
    """
    response = await _call("DELETE", f"{settings.api_base}/bots/{link.platform}/{link.native_id}",
                           settings.bot_headers)
    if response.status_code >= 300 and response.status_code != 404:
        raise VexaError(response.status_code, _detail(response))


# --- live transcript -----------------------------------------------------------------------


@dataclass(frozen=True)
class Utterance:
    """One finished sentence or turn, ready for the agent."""

    segment_id: str
    speaker: str  # Display name; empty when Vexa could not tell.
    speaker_key: str
    text: str
    start: object  # Epoch seconds, an ISO string, or None.
    promoted: bool = False  # Delivered from a stable draft, before Vexa confirmed it.


OnUtterance = Callable[[Utterance], Awaitable[None]]
OnStatus = Callable[[str], Awaitable[None]]


@dataclass
class _Draft:
    segment: dict
    text: str
    changed_at: float


class TranscriptStream:
    """One WebSocket subscription to one meeting, delivering each finished utterance once."""

    def __init__(self, settings: VexaSettings, link: MeetingLink, on_utterance: OnUtterance,
                 on_status: OnStatus | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings, self._link = settings, link
        self._on_utterance, self._on_status = on_utterance, on_status
        self._clock = clock
        self._delivered: OrderedDict[str, None] = OrderedDict()  # segment ids already handed on
        self._drafts: dict[str, _Draft] = {}
        self._stop = asyncio.Event()
        self._order = asyncio.Lock()  # The reader and the draft ticker both deliver; turns keep their order.
        self.retracted_after_delivery = 0

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Listen until stopped or the meeting ends. Reconnects with backoff."""
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._listen()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure means: reconnect
                logger.warning("transcript stream error (%s); reconnecting", type(e).__name__)
            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay + random.uniform(0, 0.5))
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, 30.0)

    async def _listen(self) -> None:
        async with websockets.connect(self._settings.stream_url, additional_headers=self._settings.read_headers,
                                      ping_interval=None, max_size=MAX_FRAME_BYTES) as ws:
            await ws.send(json.dumps({"action": "subscribe", "meetings": [
                {"platform": self._link.platform, "native_id": self._link.native_id}]}))
            ticker = asyncio.create_task(self._tick(ws))
            subscribed = False
            try:
                async for raw in ws:
                    frame = _loads(raw)
                    if frame.get("type") == "subscribed":
                        subscribed = True
                    elif frame.get("type") == "error" and not subscribed:
                        raise ConnectionError("subscribe refused")  # e.g. the meeting is not visible yet
                    await self._handle(frame)
            finally:
                ticker.cancel()

    async def _tick(self, ws) -> None:
        """Timed work that must not wait for the next frame: promoting drafts, pings, stopping."""
        next_ping = self._clock() + PING_INTERVAL_SECONDS
        while True:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                await ws.close()  # Ends the read loop in _listen.
                return
            await self._promote_stable_drafts()
            if self._clock() >= next_ping:
                await ws.send(json.dumps({"action": "ping"}))
                next_ping = self._clock() + PING_INTERVAL_SECONDS

    async def _handle(self, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "meeting.status":
            status = (frame.get("payload") or {}).get("status")
            if status and self._on_status:
                await self._on_status(status)
            if status in TERMINAL_STATUSES:
                self._stop.set()
        elif kind == "transcript_retract":
            for segment_id in frame.get("segment_ids") or []:
                self._drafts.pop(segment_id, None)
                if segment_id in self._delivered:
                    self.retracted_after_delivery += 1  # Turns are immutable once stored; counted only.
        elif "confirmed" in frame or "pending" in frame:
            for seg in frame.get("confirmed") or []:
                await self._deliver(seg, promoted=False)
            self._track_drafts(frame.get("speaker"), frame.get("pending") or [])
        else:  # Older shape: a flat list with a completed flag.
            segments = (frame.get("payload") or {}).get("segments") or frame.get("segments")
            for seg in segments if isinstance(segments, list) else []:
                if isinstance(seg, dict) and seg.get("completed"):
                    await self._deliver(seg, promoted=False)

    def _track_drafts(self, speaker_key: object, pending: list) -> None:
        """A frame lists all of one speaker's current drafts; drafts missing from it are gone."""
        now = self._clock()
        seen = set()
        for seg in pending:
            sid, text = seg.get("segment_id"), (seg.get("text") or "").strip()
            if not sid or not text or sid in self._delivered:
                continue
            seen.add(sid)
            known = self._drafts.get(sid)
            if known is None or known.text != text:
                self._drafts[sid] = _Draft(seg, text, now)
        for sid, draft in list(self._drafts.items()):
            if draft.segment.get("speaker_key") == speaker_key and sid not in seen:
                del self._drafts[sid]

    async def _promote_stable_drafts(self) -> None:
        limit = self._settings.promote_after_s
        if limit <= 0:
            return
        now = self._clock()
        for sid, draft in list(self._drafts.items()):
            if now - draft.changed_at >= limit and len(draft.text.split()) >= MIN_PROMOTE_WORDS:
                if self._drafts.pop(sid, None) is not None:  # The reader may have taken it meanwhile.
                    await self._deliver(draft.segment, promoted=True)

    async def _deliver(self, seg: dict, *, promoted: bool) -> None:
        sid, text = seg.get("segment_id"), (seg.get("text") or "").strip()
        if not text or not sid:
            return
        async with self._order:
            if sid in self._delivered:
                return
            self._delivered[sid] = None
            self._drafts.pop(sid, None)
            while len(self._delivered) > MAX_REMEMBERED_SEGMENTS:
                self._delivered.popitem(last=False)
            await self._on_utterance(Utterance(
                segment_id=sid, speaker=(seg.get("speaker") or "").strip(),
                speaker_key=str(seg.get("speaker_key") or ""), text=text,
                start=seg.get("absolute_start_time") or seg.get("start"), promoted=promoted))


def _loads(raw: object) -> dict:
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return frame if isinstance(frame, dict) else {}
