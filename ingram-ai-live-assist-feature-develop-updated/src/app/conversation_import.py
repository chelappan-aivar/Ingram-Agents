"""Recorded-conversation parsing for the debug UI's playback mode.

Expected file format::

    {
      "conversation": [
        {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba",
         "name": "John (Partner)", "text": "Tell me about your project."},
        {"timestamp": "2026-10-08T10:00:18Z", "speaker": "cx",
         "name": "Alice (Customer)", "text": "We want to move to the cloud."}
      ]
    }

``timestamp`` and ``text`` are required. ``name`` falls back to a label based on
``speaker``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.transcript import VALID_SPEAKERS

DEFAULT_DISPLAY_NAME = {"ba": "Partner BA", "cx": "Customer"}

_OFFSET_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


class InvalidConversationFileError(ValueError):
    """Raised when a conversation file is missing or malformed."""


@dataclass(frozen=True)
class ConversationTurn:
    timestamp: datetime  # Naive UTC.
    speaker: str
    text: str
    name: str


def parse_conversation(data: object) -> list[ConversationTurn]:
    """Validate a loaded conversation file and return its turns sorted by timestamp.

    Raises:
        InvalidConversationFileError: The data is malformed.
    """
    if not isinstance(data, dict) or "conversation" not in data:
        raise InvalidConversationFileError(
            "JSON must be an object with a top-level 'conversation' list"
        )

    raw_turns = data["conversation"]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise InvalidConversationFileError("'conversation' must be a non-empty list")

    turns: list[ConversationTurn] = []
    for i, item in enumerate(raw_turns):
        if not isinstance(item, dict):
            raise InvalidConversationFileError(f"turn {i} must be a JSON object")

        speaker = item.get("speaker")
        if speaker not in VALID_SPEAKERS:
            raise InvalidConversationFileError(
                f"turn {i}: 'speaker' must be one of {sorted(VALID_SPEAKERS)}, got {speaker!r}"
            )

        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidConversationFileError(f"turn {i}: 'text' must be a non-empty string")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = DEFAULT_DISPLAY_NAME[speaker]

        turns.append(
            ConversationTurn(
                timestamp=_parse_timestamp(item.get("timestamp"), i),
                speaker=speaker,
                text=text.strip(),
                name=name.strip(),
            )
        )

    turns.sort(key=lambda t: t.timestamp)
    return turns


def compute_delays_seconds(turns: list[ConversationTurn]) -> list[float]:
    """Return the pause before each turn, relative to the previous one. The first is 0."""
    if not turns:
        return []
    delays = [0.0]
    for prev, cur in zip(turns, turns[1:]):
        delays.append(max(0.0, (cur.timestamp - prev.timestamp).total_seconds()))
    return delays


def attio_transcript_to_conversation(
    text: str,
    *,
    customer_names: list[str],
    start: datetime,
) -> dict:
    """Convert an Attio call transcript export to the conversation format.

    The export repeats ``<speaker name>``, ``<M:SS or H:MM:SS offset>``, then one
    or more lines of text. Anything before the first block is ignored.

    Args:
        text: The exported transcript.
        customer_names: Speakers to tag as ``cx`` (case-insensitive). Everyone
            else is tagged ``ba``.
        start: Call start time. Offsets are added to it to build timestamps.

    Raises:
        InvalidConversationFileError: No transcript blocks were found.
    """
    customers = {n.strip().lower() for n in customer_names}
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    lines = [line.strip() for line in text.splitlines()]
    blocks: list[dict] = []
    current: dict | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line and not _OFFSET_RE.match(line) and _OFFSET_RE.match(nxt):
            if current and current["text"]:
                blocks.append(current)
            current = {"name": line, "offset": _parse_offset_seconds(nxt), "text": []}
            i += 2
            continue
        if current is not None and line:
            current["text"].append(line)
        i += 1
    if current and current["text"]:
        blocks.append(current)

    if not blocks:
        raise InvalidConversationFileError(
            "no '<name>' / '<M:SS>' / '<text>' blocks found in transcript"
        )

    conversation = []
    for b in blocks:
        ts = (start + timedelta(seconds=b["offset"])).astimezone(timezone.utc)
        conversation.append(
            {
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "speaker": "cx" if b["name"].lower() in customers else "ba",
                "name": b["name"],
                "text": " ".join(b["text"]),
            }
        )
    return {"conversation": conversation}


def _parse_timestamp(value: object, index: int) -> datetime:
    """Parse an ISO-8601 timestamp to naive UTC so aware and naive values compare safely."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidConversationFileError(
            f"turn {index}: 'timestamp' must be a non-empty ISO-8601 string"
        )
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidConversationFileError(
            f"turn {index}: invalid timestamp '{value}': {e}"
        ) from e
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_offset_seconds(value: str) -> int:
    m = _OFFSET_RE.match(value)
    if m is None:
        raise ValueError(f"not an offset: {value!r}")
    hours = int(m.group(1) or 0)
    return hours * 3600 + int(m.group(2)) * 60 + int(m.group(3))
