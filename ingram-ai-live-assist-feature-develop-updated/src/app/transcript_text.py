"""Pasted or uploaded transcript text -> the conversation format the replayer plays.

Accepted, detected from the content:

* WebVTT and SRT, as exported by Teams and Zoom (``<v Name>text</v>`` or ``Name: text`` cues)
* plain text, one turn per line: ``Name: text``, optionally led by a time (``[01:23] Name: text``)
* the project's conversation JSON (``{"conversation": [...]}``)

Each turn gets ``ba`` or ``cx`` from the speaker's name (see ``speaker_roles``). Text before any
speaker name is attributed to the customer. When the source has no times, they are estimated
from the amount of speech, so the replay keeps a natural pace.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from app.conversation_import import InvalidConversationFileError, parse_conversation
from app.events import MAX_TEXT_CHARS
from app.speaker_roles import RoleResolver
from app.transcript import MAX_NAME_LENGTH, SPEAKER_CX, parse_name

MAX_TRANSCRIPT_CHARS = 60_000  # About an hour of speech.
MAX_TURNS = 1_500
MAX_TURN_CHARS = 1_200  # Longer paragraphs are split on sentence ends, to stay conversational.
WORDS_PER_SECOND = 2.5  # About 150 words a minute.
MIN_TURN_SECONDS = 1.5

DEFAULT_CUSTOMER_NAME = "Customer"

_CUE_TIME = re.compile(
    r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)
_VOICE_TAG = re.compile(r"^<v(?:\.[\w.-]+)?\s+([^>]+)>(.*?)(?:</v>)?$", re.DOTALL)
_ANY_TAG = re.compile(r"</?[^>]+>")
_LEADING_TIME = re.compile(r"^[\[(]?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[\])]?\s*[-–]?\s*")
# "Name: text". Names are short, so a long prefix before a colon is not a speaker.
_SPEAKER_LINE = re.compile(r"^([^\W\d_][^:\n]{0,%d}?)\s*:\s+(.+)$" % (MAX_NAME_LENGTH - 1), re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class InvalidTranscriptTextError(ValueError):
    """The text could not be read as a transcript. The message says why, for the user."""


def transcript_to_conversation(text: object, ba_names: list[str], *, start: datetime | None = None) -> dict:
    """Parse ``text`` and return ``{"conversation": [...]}`` for the replayer.

    Raises:
        InvalidTranscriptTextError: The text is empty, too large, or has no speech in it.
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidTranscriptTextError("Paste or upload a transcript.")
    if len(text) > MAX_TRANSCRIPT_CHARS:
        raise InvalidTranscriptTextError(f"The transcript must be at most {MAX_TRANSCRIPT_CHARS:,} characters.")
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    roles = RoleResolver(ba_names)

    if text.lstrip().startswith("{"):
        return _from_json(text)

    raw = _parse_vtt_or_srt(text) if _looks_like_cues(text) else _parse_plain(text)
    raw = _split_long(raw)
    if not raw:
        raise InvalidTranscriptTextError("No spoken lines were found in the transcript.")
    if len(raw) > MAX_TURNS:
        raise InvalidTranscriptTextError(f"The transcript has more than {MAX_TURNS:,} turns.")
    return {"conversation": _timestamped(raw, roles, start or datetime.now(timezone.utc))}


def _from_json(text: str) -> dict:
    try:
        data = json.loads(text)
        parse_conversation(data)
    except (json.JSONDecodeError, InvalidConversationFileError) as e:
        raise InvalidTranscriptTextError(f"The JSON is not a valid conversation file: {e}") from None
    return data


def _looks_like_cues(text: str) -> bool:
    head = text.lstrip()[:2000]
    return head.startswith("WEBVTT") or bool(re.search(r"^\d+\s*\n[\d:,. ]+-->", head, re.MULTILINE)) or "-->" in head


def _parse_vtt_or_srt(text: str) -> list[dict]:
    """Cues -> [{"name", "text", "offset"}]. Blocks are separated by blank lines."""
    turns: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        time_at = next((i for i, ln in enumerate(lines) if _CUE_TIME.match(ln)), None)
        if time_at is None:
            continue  # Header, NOTE or STYLE block.
        match = _CUE_TIME.match(lines[time_at])
        hours, minutes, seconds, millis = (int(g or 0) for g in match.groups())
        offset = hours * 3600 + minutes * 60 + seconds + millis / 1000
        payload = " ".join(lines[time_at + 1:])
        if not payload:
            continue
        name, spoken = None, payload
        voice = _VOICE_TAG.match(payload)
        if voice:
            name, spoken = voice.group(1).strip(), voice.group(2)
        else:
            payload = _ANY_TAG.sub("", payload).strip()
            name, spoken = _speaker_line(payload) or (None, payload)
        spoken = _ANY_TAG.sub("", spoken).strip()
        if spoken:
            turns.append({"name": name, "text": spoken, "offset": offset})
    return _merge_same_speaker(turns)


MAX_SPEAKER_WORDS = 6


def _speaker_line(line: str) -> tuple[str, str] | None:
    """(name, text) when the line starts with a plausible speaker name, else None."""
    match = _SPEAKER_LINE.match(line)
    if match and len(match.group(1).split()) <= MAX_SPEAKER_WORDS:
        return match.group(1).strip(), match.group(2).strip()
    return None


def _parse_plain(text: str) -> list[dict]:
    turns: list[dict] = []
    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        offset = None
        timed = _LEADING_TIME.match(line)
        if timed:
            hours, minutes, seconds = (int(g or 0) for g in timed.groups())
            offset, line = hours * 3600 + minutes * 60 + seconds, line[timed.end():]
        named = _speaker_line(line)
        if named:
            turns.append({"name": named[0], "text": named[1], "offset": offset})
        elif turns and offset is None and turns[-1]["text"]:
            turns[-1]["text"] += " " + line  # A wrapped line continues the previous turn.
        else:
            turns.append({"name": None, "text": line, "offset": offset})
    return turns


def _merge_same_speaker(turns: list[dict]) -> list[dict]:
    """Cue-by-cue exports split one utterance across cues; join neighbours from the same speaker."""
    merged: list[dict] = []
    for t in turns:
        last = merged[-1] if merged else None
        if last and last["name"] == t["name"] and t["offset"] - last["offset"] < 8 and len(last["text"]) < MAX_TURN_CHARS:
            last["text"] += " " + t["text"]
        else:
            merged.append(dict(t))
    return merged


def _split_long(turns: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in turns:
        for i, piece in enumerate(_chunks(t["text"])):
            out.append({**t, "text": piece, "offset": t["offset"] if i == 0 else None})
    return out


def _chunks(text: str) -> list[str]:
    """Split a long paragraph on sentence ends into pieces of at most MAX_TURN_CHARS."""
    if len(text) <= MAX_TURN_CHARS:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > MAX_TURN_CHARS:  # No sentence end in reach: cut at a space.
            cut = sentence.rfind(" ", 0, MAX_TURN_CHARS)
            cut = cut if cut > 0 else MAX_TURN_CHARS
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > MAX_TURN_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _timestamped(raw: list[dict], roles: RoleResolver, start: datetime) -> list[dict]:
    """Give every turn a role and a UTC time. Missing times continue from the previous turn."""
    base = start.astimezone(timezone.utc).replace(microsecond=0)
    clock = 0.0
    out = []
    for t in raw:
        name = parse_name(t["name"]) if t["name"] else None
        if t["offset"] is not None:
            clock = max(clock, float(t["offset"]))
        speech = t["text"][:MAX_TEXT_CHARS].strip()
        out.append({
            "timestamp": (base + timedelta(seconds=clock)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "speaker": roles.role_for(name) if name else SPEAKER_CX,
            "name": name or DEFAULT_CUSTOMER_NAME,
            "text": speech,
        })
        clock += max(MIN_TURN_SECONDS, len(speech.split()) / WORDS_PER_SECOND)
    return out
