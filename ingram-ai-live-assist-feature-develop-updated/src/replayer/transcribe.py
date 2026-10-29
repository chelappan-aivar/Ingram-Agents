"""Audio source: streams per-speaker audio through Amazon Transcribe and sends
each final segment as an ``ila.event.v1`` turn.

Input is the manifest written by ``make_audio.py``: one 16 kHz mono PCM track
per speaker, all aligned, each silent while the others talk. One Transcribe
stream per track means the speaker is known from the track, so no diarization
is needed.

The recording is streamed in windows of about five minutes, cut in the silence
between turns. Each window opens fresh Transcribe streams, so a run can stop
between windows and continue in a new process (a Lambda invocation, say)
without cutting a sentence. Final segments go through one queue and one
sender, so turns reach the entry point one at a time. Partial results are
dropped.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.transcript import VALID_SPEAKERS
from replayer.core import DEADLINE, ENDED, FINISHED, SCHEMA, ReplayError, Sender, end_event, start_event

SOURCE = "transcribe"
CHUNK_MS = 100
BYTES_PER_SAMPLE = 2
# Fastest pace verified against the service: at 3x Transcribe ended the stream
# with "no new audio was received for 15 seconds".
MAX_SPEED = 2.0
# Slowest pace a deadline-bound runner (Lambda) supports: a window must fit in
# one invocation.
MIN_SPEED = 0.5
WINDOW_SECONDS = 300.0
# Allowance after a window's audio for Transcribe to return its last results.
WINDOW_TAIL_SECONDS = 15.0
SUPPORTED_SAMPLE_RATES = (8000, 16000)
STATUS_POLL_SECONDS = 1.0
MANIFEST_FILE = "manifest.json"

OnSegment = Callable[[float, float, str, str], Awaitable[None]]


class InvalidManifestError(ValueError):
    """Raised when an audio manifest is missing or malformed."""


class AudioReader(Protocol):
    def size(self, location: str) -> int | None:
        """Size in bytes, or None if the track does not exist."""

    def read(self, location: str, start: int, end: int) -> Iterator[bytes]:
        """Yield the bytes in ``[start, end)``, in chunks of any size."""


@dataclass(frozen=True)
class Track:
    speaker_id: str
    name: str
    role: str  # "ba" or "cx"
    location: str  # Path relative to the manifest
    size_bytes: int


@dataclass(frozen=True)
class AudioCall:
    sample_rate: int
    tracks: tuple[Track, ...]
    duration_sec: float
    # (start_sec, end_sec, speaker_id) per manifest turn, used to cut windows
    # in silence and to skip speakers who are silent in a window.
    turns: tuple[tuple[float, float, str], ...] = ()
    reader: AudioReader | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class StreamResult:
    session_id: str
    stopped: str  # FINISHED, ENDED or DEADLINE
    turns_sent: int
    segments_dropped: int  # Final segments that arrived after the call was ended.
    next_offset: float | None = None  # Recording second to continue from, at DEADLINE.


class Transcriber(Protocol):
    async def run(self, audio: AsyncIterator[bytes], sample_rate: int, on_segment: OnSegment) -> None:
        """Stream ``audio`` and call ``on_segment(start, end, text, result_id)`` per final segment."""


# --- readers ------------------------------------------------------------------

class FileReader:
    """Reads tracks from the folder that holds the manifest."""

    def __init__(self, base: str | Path) -> None:
        self._base = Path(base)

    def size(self, location: str) -> int | None:
        path = self._base / location
        return path.stat().st_size if path.is_file() else None

    def read(self, location: str, start: int, end: int) -> Iterator[bytes]:
        with (self._base / location).open("rb") as f:
            f.seek(start)
            remaining = end - start
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data


class S3Reader:
    """Reads tracks from S3 under ``prefix`` with ranged GETs."""

    def __init__(self, bucket: str, prefix: str = "", client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3")
        self._s3 = client
        self._bucket = bucket
        self._prefix = prefix

    def size(self, location: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return self._s3.head_object(Bucket=self._bucket, Key=self._prefix + location)["ContentLength"]
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def read(self, location: str, start: int, end: int) -> Iterator[bytes]:
        if end <= start:
            return
        body = self._s3.get_object(
            Bucket=self._bucket, Key=self._prefix + location, Range=f"bytes={start}-{end - 1}"
        )["Body"]
        try:
            yield from body.iter_chunks(65536)
        finally:
            body.close()


# --- manifest -----------------------------------------------------------------

def parse_manifest(data: object, reader: AudioReader) -> AudioCall:
    """Validate a manifest and check its tracks exist through ``reader``.

    Raises:
        InvalidManifestError: The manifest is malformed or a track is missing.
    """
    if not isinstance(data, dict):
        raise InvalidManifestError("manifest must be a JSON object")
    audio = data.get("audio") or {}
    if audio.get("encoding") != "pcm_s16le" or audio.get("channels") != 1:
        raise InvalidManifestError("audio must be pcm_s16le, 1 channel")
    sample_rate = audio.get("sample_rate")
    if sample_rate not in SUPPORTED_SAMPLE_RATES:
        raise InvalidManifestError(f"sample_rate must be one of {SUPPORTED_SAMPLE_RATES}")
    if data.get("dry_run"):
        raise InvalidManifestError("manifest is from a dry run: its audio is silence")

    tracks = []
    for s in data.get("speakers") or []:
        if not isinstance(s, dict) or not isinstance(s.get("speaker_id"), str):
            raise InvalidManifestError("each speaker needs a speaker_id")
        role = s.get("role")
        if role not in VALID_SPEAKERS:
            raise InvalidManifestError(f"speaker {s['speaker_id']!r}: role must be 'ba' or 'cx'")
        location = _safe_relative(s.get("pcm"), s["speaker_id"])
        size = reader.size(location)
        if size is None:
            raise InvalidManifestError(f"speaker {s['speaker_id']!r}: missing audio {location}")
        tracks.append(Track(s["speaker_id"], s.get("name") or s["speaker_id"], role, location, size))
    if not tracks:
        raise InvalidManifestError("manifest has no speakers")
    if len({t.speaker_id for t in tracks}) != len(tracks):
        raise InvalidManifestError("speaker_id values must be unique")

    byte_rate = sample_rate * BYTES_PER_SAMPLE
    duration = data.get("duration_sec")
    if not isinstance(duration, (int, float)) or duration <= 0:
        duration = max(t.size_bytes for t in tracks) / byte_rate

    turns = []
    for t in data.get("turns") or []:
        if isinstance(t, dict) and isinstance(t.get("start_sec"), (int, float)) \
                and isinstance(t.get("end_sec"), (int, float)):
            turns.append((float(t["start_sec"]), float(t["end_sec"]), str(t.get("speaker_id", ""))))
    return AudioCall(sample_rate, tuple(tracks), float(duration), tuple(sorted(turns)), reader)


def load_manifest(path: str | Path) -> AudioCall:
    """Read a manifest from disk. Track paths resolve relative to it.

    Raises:
        InvalidManifestError: The manifest is malformed or a track is missing.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise InvalidManifestError(f"cannot read manifest {path}: {e}") from e
    return parse_manifest(data, FileReader(path.parent))


def is_audio_manifest(data: object) -> bool:
    return isinstance(data, dict) and "speakers" in data and "audio" in data


def _safe_relative(value: object, speaker_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidManifestError(f"speaker {speaker_id!r}: 'pcm' must be a relative path")
    parts = PurePosixPath(value).parts
    if value.startswith("/") or "\\" in value or any(p in ("..", "") for p in parts):
        raise InvalidManifestError(f"speaker {speaker_id!r}: 'pcm' must be a relative path")
    return value


# --- windows ------------------------------------------------------------------

def plan_windows(
    call: AudioCall, start: float, end: float, window: float = WINDOW_SECONDS
) -> list[tuple[float, float]]:
    """Split ``[start, end)`` into windows of at most ``window`` seconds where possible.

    Cuts fall in the middle of the silence between turns. A turn longer than a
    window, or a manifest without turns, gives a longer window.
    """
    cuts, run_end = [], None
    for s, e, _ in call.turns:
        if run_end is not None and s > run_end:
            cuts.append((run_end + s) / 2)
        run_end = e if run_end is None else max(run_end, e)
    cuts = [c for c in cuts if start < c < end]

    windows, ws = [], start
    while end - ws > window:
        within = [c for c in cuts if ws < c <= ws + window]
        cut = within[-1] if within else next((c for c in cuts if c > ws + window), end)
        windows.append((ws, cut))
        ws = cut
    if end > ws:
        windows.append((ws, end))
    return windows


def extend_to_silence(call: AudioCall, at: float) -> float:
    """Move ``at`` forward to the end of any turn still being spoken, so a limit
    never cuts a sentence. Unchanged without turn timings or in silence."""
    for s, e, _ in call.turns:  # Sorted by start; overlapping turns chain.
        if s < at < e:
            at = e
    return at


def _speakers_in(call: AudioCall, start: float, end: float) -> set[str] | None:
    """Speaker ids with a turn overlapping the window, or None if unknown."""
    if not call.turns:
        return None
    return {sid for s, e, sid in call.turns if s < end and e > start}


# --- streaming ----------------------------------------------------------------

async def stream_call(
    *,
    session_id: str,
    call: AudioCall,
    sender: Sender,
    transcriber_factory: Callable[[], Transcriber],
    speed: float = 1.0,
    title: str | None = None,
    assist_config: dict | None = None,
    get_status: Callable[[str], str | None] | None = None,
    on_event: Callable[[dict, dict], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    status_poll_seconds: float = STATUS_POLL_SECONDS,
    limit_seconds: float | None = None,
    max_speed: float = MAX_SPEED,
    send_start: bool = True,
    start_offset: float = 0.0,
    call_started_at: datetime | None = None,
    deadline: float | None = None,
    window_seconds: float = WINDOW_SECONDS,
) -> StreamResult:
    """Stream a call's audio through Transcribe and send its turns.

    Sends ``session.start`` first (unless ``send_start`` is False) and
    ``session.end`` after the last window. ``speed`` above 1 sends audio
    faster than real time. While the call is paused, silence is sent instead
    (Transcribe ends a stream after 15 s without audio) and the recording does
    not advance. ``limit_seconds`` stops after that much of the recording.

    With ``deadline`` (a ``clock()`` value), stops before a window that would
    not finish in time, or when a pause runs into it, and returns DEADLINE
    with ``next_offset`` to continue from.

    Raises:
        ReplayError: The entry point rejected session.start or a turn.
    """
    if not 0 < speed <= max_speed:
        raise ValueError(f"speed must be above 0 and at most {max_speed}")
    if call.reader is None:
        raise ValueError("call has no audio reader")

    def send(event: dict) -> dict:
        ack = sender.send(event)
        if on_event:
            on_event(event, ack)
        return ack

    if send_start:
        ack = await asyncio.to_thread(
            send, start_event(session_id, title=title, assist_config=assist_config, source=SOURCE)
        )
        if not ack.get("accepted"):
            raise ReplayError(f"session.start rejected: {ack.get('error')}")

    started_at = call_started_at or datetime.now(timezone.utc)
    end = call.duration_sec
    if limit_seconds is not None:
        end = min(call.duration_sec, extend_to_silence(call, limit_seconds))
    status = {"value": "live"}
    stop = asyncio.Event()  # Set when the call ends or delivery fails.
    counts = {"sent": 0, "dropped": 0}

    async def poll_status() -> None:
        while not stop.is_set():
            value = await asyncio.to_thread(get_status, session_id)
            status["value"] = value or "live"
            if value == "ended":
                stop.set()
                return
            await sleep(status_poll_seconds)

    def result(stopped: str, next_offset: float | None = None) -> StreamResult:
        return StreamResult(session_id, stopped, counts["sent"], counts["dropped"], next_offset)

    poller = asyncio.create_task(poll_status()) if get_status else None
    try:
        for ws, we in plan_windows(call, start_offset, end, window_seconds):
            if stop.is_set():
                break
            if deadline is not None and clock() + (we - ws) / speed + WINDOW_TAIL_SECONDS > deadline:
                return result(DEADLINE, ws)
            reached = await _stream_window(
                call=call, window=(ws, we), session_id=session_id, send=send,
                transcriber_factory=transcriber_factory, speed=speed, status=status, stop=stop,
                counts=counts, started_at=started_at, clock=clock, sleep=sleep, deadline=deadline,
            )
            if reached is not None:
                return result(DEADLINE, reached)
    finally:
        stop.set()
        if poller and not poller.done():
            poller.cancel()

    if status["value"] == "ended":
        return result(ENDED)
    await asyncio.to_thread(send, end_event(session_id, "audio finished"))
    return result(FINISHED)


async def _stream_window(
    *,
    call: AudioCall,
    window: tuple[float, float],
    session_id: str,
    send: Callable[[dict], dict],
    transcriber_factory: Callable[[], Transcriber],
    speed: float,
    status: dict,
    stop: asyncio.Event,
    counts: dict,
    started_at: datetime,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    deadline: float | None,
) -> float | None:
    """Stream one window. Returns the offset to resume from if a pause hit the deadline."""
    ws, we = window
    byte_rate = call.sample_rate * BYTES_PER_SAMPLE
    speaking = _speakers_in(call, ws, we)
    tracks = [
        t for t in call.tracks
        if (speaking is None or t.speaker_id in speaking) and min(_byte(we, call), t.size_bytes) > _byte(ws, call)
    ]
    if not tracks:
        return None

    queue: asyncio.Queue[tuple[Track, float, str, str] | None] = asyncio.Queue()
    window_stop = asyncio.Event()
    positions: dict[str, int] = {}
    hit = {"deadline": False}

    async def deliver() -> None:
        try:
            while (item := await queue.get()) is not None:
                track, start, text, result_id = item
                if status["value"] == "ended":
                    counts["dropped"] += 1
                    continue
                spoken_at = started_at + timedelta(seconds=ws + start)
                ack = await asyncio.to_thread(send, _turn_event(session_id, track, text, spoken_at, result_id))
                if ack.get("accepted"):
                    counts["sent"] += 1
                elif ack.get("code") == "session_ended":
                    status["value"] = "ended"
                    stop.set()
                    counts["dropped"] += 1
                else:
                    raise ReplayError(f"turn rejected: {ack.get('error')}")
        except BaseException:
            stop.set()  # Stop the audio streams too.
            raise

    async def transcribe(track: Track) -> None:
        async def on_segment(start: float, _end: float, text: str, result_id: str) -> None:
            await queue.put((track, start, text, result_id))

        audio = _paced_audio(
            call, track, ws, we, speed, status, stop, window_stop, positions, hit, clock, sleep, deadline
        )
        await transcriber_factory().run(audio, call.sample_rate, on_segment)

    deliverer = asyncio.create_task(deliver())
    try:
        async with asyncio.TaskGroup() as group:
            for track in tracks:
                group.create_task(transcribe(track))
        await queue.put(None)
        await deliverer
    finally:
        if not deliverer.done():
            deliverer.cancel()

    if hit["deadline"] and status["value"] != "ended":
        return min(positions.values()) / byte_rate
    return None


async def _paced_audio(
    call: AudioCall,
    track: Track,
    start_sec: float,
    end_sec: float,
    speed: float,
    status: dict,
    stop: asyncio.Event,
    window_stop: asyncio.Event,
    positions: dict[str, int],
    hit: dict,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    deadline: float | None,
) -> AsyncIterator[bytes]:
    """Yield ``CHUNK_MS`` chunks at ``speed`` times real time; silence while paused."""
    chunk_bytes = call.sample_rate * BYTES_PER_SAMPLE * CHUNK_MS // 1000
    chunk_seconds = CHUNK_MS / 1000
    silence = b"\x00" * chunk_bytes
    position = _byte(start_sec, call)
    end_byte = min(_byte(end_sec, call), track.size_bytes)
    positions[track.speaker_id] = position
    chunks = _rechunk(call.reader.read(track.location, position, end_byte), chunk_bytes)
    next_at = clock()
    try:
        while not (stop.is_set() or window_stop.is_set()):
            if status["value"] == "paused":
                if deadline is not None and clock() + WINDOW_TAIL_SECONDS > deadline:
                    hit["deadline"] = True
                    window_stop.set()
                    return
                chunk, step = silence, chunk_seconds
            else:
                chunk = await asyncio.to_thread(next, chunks, b"")
                if not chunk:
                    return
                position += len(chunk)
                positions[track.speaker_id] = position
                step = chunk_seconds / speed
            yield chunk
            next_at += step
            delay = next_at - clock()
            if delay > 0:
                await sleep(delay)
    finally:
        chunks.close()


def _rechunk(data: Iterator[bytes], size: int) -> Iterator[bytes]:
    """Re-slice a byte stream into chunks of exactly ``size`` (the last may be shorter)."""
    buffer = bytearray()
    for piece in data:
        buffer += piece
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


def _byte(seconds: float, call: AudioCall) -> int:
    return int(seconds * call.sample_rate) * BYTES_PER_SAMPLE


def _turn_event(session_id: str, track: Track, text: str, spoken_at: datetime, result_id: str) -> dict:
    return {
        "schema": SCHEMA,
        "type": "turn",
        "session_id": session_id,
        "source": SOURCE,
        "speaker": track.role,
        "speaker_name": track.name,
        "text": text,
        "spoken_at": spoken_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "is_final": True,
        "event_id": f"{track.speaker_id}-{result_id}"[:200],
    }


# --- Amazon Transcribe ----------------------------------------------------------

class BotocoreCredentialResolver:
    """Gives the Transcribe SDK the same credentials boto3 uses, including SSO profiles."""

    def __init__(self, region: str | None = None) -> None:
        import boto3

        self._session = boto3.Session(region_name=region)

    async def get_credentials(self):
        from amazon_transcribe.auth import Credentials

        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials found for Amazon Transcribe")
        frozen = creds.get_frozen_credentials()  # Refreshes expiring credentials.
        return Credentials(frozen.access_key, frozen.secret_key, frozen.token)


class AmazonTranscriber:
    """One Amazon Transcribe streaming session. Needs the ``amazon-transcribe`` package."""

    def __init__(self, region: str, language_code: str = "en-US") -> None:
        self._region = region
        self._language_code = language_code

    async def run(self, audio: AsyncIterator[bytes], sample_rate: int, on_segment: OnSegment) -> None:
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.model import TranscriptEvent

        client = TranscribeStreamingClient(
            region=self._region, credential_resolver=BotocoreCredentialResolver(self._region)
        )
        stream = await client.start_stream_transcription(
            language_code=self._language_code,
            media_sample_rate_hz=sample_rate,
            media_encoding="pcm",
        )

        async def feed() -> None:
            async for chunk in audio:
                await stream.input_stream.send_audio_event(audio_chunk=chunk)
            await stream.input_stream.end_stream()

        async def read() -> None:
            async for event in stream.output_stream:
                if not isinstance(event, TranscriptEvent):
                    continue
                for result in event.transcript.results:
                    if result.is_partial or not result.alternatives:
                        continue
                    text = (result.alternatives[0].transcript or "").strip()
                    if text:
                        await on_segment(result.start_time, result.end_time, text, result.result_id)

        async with asyncio.TaskGroup() as group:
            group.create_task(feed())
            group.create_task(read())
