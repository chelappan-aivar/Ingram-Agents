"""Transport-neutral events produced by the agents; the views decide how to frame them."""

from dataclasses import dataclass
from typing import Any

# An editor event is a JSON object with a "type" (text, tool_call, edit_proposal, done, ...).
type EditorEvent = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TextChunk:
    """A piece of the generated markdown."""

    text: str


@dataclass(frozen=True, slots=True)
class UsageReport:
    """Token usage, emitted once after the last chunk."""

    input_tokens: int
    output_tokens: int


type GenerationEvent = TextChunk | UsageReport


@dataclass(frozen=True, slots=True)
class SessionStarted:
    """Opens a generation stream with the session id the client must reuse."""

    session_id: str


@dataclass(frozen=True, slots=True)
class Completed:
    """Closes a generation stream that finished normally."""


@dataclass(frozen=True, slots=True)
class Failed:
    """Closes a generation stream that failed; `message` is safe to show to the user."""

    message: str


type GenerationStreamEvent = SessionStarted | TextChunk | UsageReport | Completed | Failed
