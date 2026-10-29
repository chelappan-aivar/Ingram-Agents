"""Transcript chunk parsing and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

SPEAKER_BA = "ba"
SPEAKER_CX = "cx"
VALID_SPEAKERS = {SPEAKER_BA, SPEAKER_CX}

MAX_TURNS_PER_REQUEST = 20
MAX_NAME_LENGTH = 100


class InvalidTranscriptChunkError(ValueError):
    """Raised when a transcript chunk is missing or malformed."""


@dataclass(frozen=True)
class TranscriptChunk:
    """A single spoken turn."""

    speaker: str
    text: str
    name: str | None = None
    timestamp: str | None = None  # UTC, YYYY-MM-DDTHH:MM:SSZ
    seq: int | None = None  # Timeline seq; lets the model cite the turn.

    def format_line(self) -> str:
        """Render the turn for the model, e.g. ``[#17 | 2026-10-08T10:00:18Z | cx | Alice]: Hi``."""
        ref = f"#{self.seq}" if self.seq is not None else None
        header = " | ".join(p for p in (ref, self.timestamp, self.speaker, self.name) if p)
        return f"[{header}]: {self.text}"

    @classmethod
    def from_timeline_item(cls, item: dict) -> TranscriptChunk:
        """Build a chunk from a stored timeline turn item."""
        return cls(
            speaker=item["speaker"],
            text=item["text"],
            name=item.get("speaker_name"),
            timestamp=item.get("spoken_at"),
            seq=item.get("seq"),
        )

    @classmethod
    def from_payload(cls, payload: dict) -> TranscriptChunk:
        speaker = payload.get("speaker")
        text = payload.get("text")

        if speaker not in VALID_SPEAKERS:
            raise InvalidTranscriptChunkError(
                f"transcript_chunk.speaker must be one of {sorted(VALID_SPEAKERS)}, "
                f"got {speaker!r}"
            )
        if not isinstance(text, str) or not text.strip():
            raise InvalidTranscriptChunkError("transcript_chunk.text must be a non-empty string")

        return cls(
            speaker=speaker,
            text=text.strip(),
            name=parse_name(payload.get("name")),
            timestamp=parse_timestamp(payload.get("timestamp")),
        )


def parse_chunks(payload: object) -> list[TranscriptChunk]:
    """Parse ``transcript_chunk``: a single turn object or a list of turns, oldest first."""
    if isinstance(payload, dict):
        return [TranscriptChunk.from_payload(payload)]
    if not isinstance(payload, list):
        raise InvalidTranscriptChunkError("'transcript_chunk' must be an object or a list of objects")
    if not payload:
        raise InvalidTranscriptChunkError("transcript_chunk list must not be empty")
    if len(payload) > MAX_TURNS_PER_REQUEST:
        raise InvalidTranscriptChunkError(
            f"transcript_chunk may carry at most {MAX_TURNS_PER_REQUEST} turns, got {len(payload)}"
        )
    if not all(isinstance(p, dict) for p in payload):
        raise InvalidTranscriptChunkError("every transcript_chunk entry must be an object")
    return [TranscriptChunk.from_payload(p) for p in payload]


def parse_timestamp(value: object) -> str | None:
    """Normalize an optional ISO-8601 timestamp to UTC. Naive values are treated as UTC."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidTranscriptChunkError("transcript_chunk.timestamp must be an ISO-8601 string")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidTranscriptChunkError(
            f"transcript_chunk.timestamp is not valid ISO-8601: {value!r}"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_name(value: object) -> str | None:
    """Return a single-line, length-capped display name."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidTranscriptChunkError("transcript_chunk.name must be a string")
    # Strip characters that could forge a speaker header in the prompt line.
    name = re.sub(r"[\s|\[\]]+", " ", value).strip()[:MAX_NAME_LENGTH]
    return name or None
