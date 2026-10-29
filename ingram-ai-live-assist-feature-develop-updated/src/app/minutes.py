"""End-of-call minutes of meeting (MoM) and final summary, with turn citations.

Runs once after a call ends and its turns are processed (see ``Processor``), or
again on ``session.summarize``. The model reads the whole transcript, with each
line tagged ``#seq``, and submits structured minutes through one tool. Citations
are checked against the call's real turns. The result is stored as the
timeline's ``minutes`` item.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strands import Agent, tool
from strands.models import BedrockModel

from app.orchestrator import DEFAULT_MODEL_ID, DEFAULT_REGION
from app.prompts.minutes import (
    MERGE_INSTRUCTIONS,
    MINUTES_PROMPT,
    MINUTES_PROMPT_VERSION,
    WINDOW_INSTRUCTIONS,
)
from app.session_store import SessionState, SessionStore
from app.timeline_store import (
    MINUTES_EMPTY,
    MINUTES_ERROR,
    MINUTES_OK,
    TURN_PREFIX,
    TimelineStore,
)
from app.transcript import TranscriptChunk

# About 50k tokens. Longer transcripts are summarised in windows, then merged.
MAX_TRANSCRIPT_CHARS = 200_000
MINUTES_TIMEOUT_SECONDS = 180
MAX_ATTEMPTS = 2

logger = logging.getLogger(__name__)


# --- schema -----------------------------------------------------------------------

class _Base(BaseModel):
    # Extra keys from the model are ignored rather than failing the whole submission.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CitedItem(_Base):
    text: str = Field(min_length=1, max_length=1000)
    citations: list[int] = Field(default_factory=list, max_length=20)


class ActionItem(CitedItem):
    owner: str | None = Field(default=None, max_length=100)
    due: str | None = Field(default=None, max_length=100)  # As said in the call.


class OpenQuestion(CitedItem):
    field_id: str | None = Field(default=None, max_length=100)


class Attendee(_Base):
    speaker: Literal["ba", "cx"]
    name: str | None = Field(default=None, max_length=100)


class Minutes(_Base):
    summary: str = Field(min_length=1, max_length=4000)
    attendees: list[Attendee] = Field(default_factory=list, max_length=20)
    key_points: list[CitedItem] = Field(default_factory=list, max_length=30)
    decisions: list[CitedItem] = Field(default_factory=list, max_length=20)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=30)
    risks: list[CitedItem] = Field(default_factory=list, max_length=20)
    open_questions: list[OpenQuestion] = Field(default_factory=list, max_length=20)
    next_steps: list[CitedItem] = Field(default_factory=list, max_length=20)


CITED_SECTIONS = ("key_points", "decisions", "action_items", "risks", "open_questions", "next_steps")


class MinutesNotSubmittedError(RuntimeError):
    """The model finished without a valid submit_minutes call."""


# --- entry point ----------------------------------------------------------------

AgentFactory = Callable[..., Agent]


def make_minutes_agent(*, system_prompt: str, tools: list) -> Agent:
    return Agent(
        model=BedrockModel(model_id=DEFAULT_MODEL_ID, region_name=DEFAULT_REGION),
        system_prompt=system_prompt,
        tools=tools,
        callback_handler=None,  # Keep model output out of stdout / CloudWatch.
    )


async def generate_minutes(
    session_id: str,
    *,
    timeline: TimelineStore,
    store: SessionStore,
    regenerate: bool = False,
    make_agent: AgentFactory = make_minutes_agent,
) -> str | None:
    """Generate and store the call's minutes. Returns the stored status, or None
    if another attempt owns them or they already exist (and ``regenerate`` is False).
    """
    if not timeline.claim_minutes(session_id, regenerate=regenerate):
        return None
    started_at = _now_iso()
    started = time.monotonic()
    try:
        record = await _build_record(session_id, timeline, store, make_agent)
    except Exception as e:  # noqa: BLE001 - every failure must end the claim.
        logger.exception("minutes failed session=%r", session_id)
        # A code only: exception text can carry internal details to API clients.
        record = {"status": MINUTES_ERROR, "error_code": _error_code(e)}
    record.update(started_at=started_at, duration_ms=int((time.monotonic() - started) * 1000))
    timeline.put_minutes(session_id, record)
    logger.info(
        "minutes %s session=%r ms=%d turns=%s",
        record["status"], session_id, record["duration_ms"], record.get("turn_count"),
    )
    return record["status"]


async def _build_record(
    session_id: str, timeline: TimelineStore, store: SessionStore, make_agent: AgentFactory
) -> dict[str, Any]:
    items = timeline.items(session_id)
    meta = next((i for i in items if i["sk"] == "meta"), {})
    chunks = sorted(
        (TranscriptChunk.from_timeline_item(i) for i in items if i["sk"].startswith(TURN_PREFIX)),
        key=lambda c: c.seq,
    )
    if not chunks:
        return {"status": MINUTES_EMPTY, "minutes": None, "turn_count": 0}

    valid_seqs = {c.seq for c in chunks}
    context = _call_context(meta, store.get(session_id))
    windows = _windows([c.format_line() for c in chunks], MAX_TRANSCRIPT_CHARS)

    if len(windows) == 1:
        draft = await _ask(make_agent, f"{context}\n\n## Transcript\n{windows[0]}")
    else:
        partials = []
        for n, window in enumerate(windows, start=1):
            header = WINDOW_INSTRUCTIONS.format(part=n, total=len(windows))
            partial = await _ask(make_agent, f"{header}\n\n{context}\n\n## Transcript\n{window}")
            partials.append(clean_minutes(partial, valid_seqs))
        draft = await _ask(
            make_agent,
            f"{MERGE_INSTRUCTIONS}\n\n{context}\n\n## Partial minutes\n"
            + json.dumps(partials, ensure_ascii=False),
        )

    return {
        "status": MINUTES_OK,
        "minutes": clean_minutes(draft, valid_seqs),
        "model_id": DEFAULT_MODEL_ID,
        "prompt_version": MINUTES_PROMPT_VERSION,
        "generated_at": _now_iso(),
        "transcript_last_seq": max(valid_seqs),
        "turn_count": len(chunks),
        "windows": len(windows),
    }


async def _ask(make_agent: AgentFactory, prompt: str) -> Minutes:
    """One model pass, retried once on timeout or failure."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        scratch: dict[str, Minutes] = {}
        agent = make_agent(system_prompt=MINUTES_PROMPT, tools=[_submit_tool(scratch)])
        try:
            await asyncio.wait_for(agent.invoke_async(prompt), MINUTES_TIMEOUT_SECONDS)
            if "minutes" not in scratch:
                raise MinutesNotSubmittedError("model did not submit minutes")
            return scratch["minutes"]
        except Exception as e:  # noqa: BLE001 - retried, then re-raised below.
            last = e
            logger.warning("minutes attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, type(e).__name__)
    raise last or MinutesNotSubmittedError("no attempt ran")


def _submit_tool(scratch: dict):
    @tool
    def submit_minutes(
        summary: str,
        attendees: list[dict] | None = None,
        key_points: list[dict] | None = None,
        decisions: list[dict] | None = None,
        action_items: list[dict] | None = None,
        risks: list[dict] | None = None,
        open_questions: list[dict] | None = None,
        next_steps: list[dict] | None = None,
    ) -> str:
        """Submit the minutes of the call. Call once with everything.

        Every list item is {"text": str, "citations": [seq, ...]}, plus the extra keys below.

        Args:
            summary: Final call summary, 1 to 3 short paragraphs.
            attendees: [{"speaker": "ba" | "cx", "name": str or null}].
            key_points: Main things learned.
            decisions: Things agreed or confirmed.
            action_items: Also "owner" (str or null) and "due" (as said, or null).
            risks: Risks, concerns and constraints raised.
            open_questions: Still unknown. Also "field_id" (open checklist field id, or null).
            next_steps: Agreed next steps.
        """
        payload = {
            "summary": summary, "attendees": attendees or [], "key_points": key_points or [],
            "decisions": decisions or [], "action_items": action_items or [], "risks": risks or [],
            "open_questions": open_questions or [], "next_steps": next_steps or [],
        }
        try:
            scratch["minutes"] = Minutes.model_validate(payload)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:10]
            )
            return f"Minutes not recorded. Fix these and call submit_minutes again: {problems}"
        return "Minutes recorded."

    return submit_minutes


# --- helpers -------------------------------------------------------------------

def clean_minutes(minutes: Minutes, valid_seqs: set[int]) -> dict[str, Any]:
    """Keep only citations of real turns and flag items left without one."""
    out = minutes.model_dump()
    for section in CITED_SECTIONS:
        for item in out[section]:
            item["citations"] = sorted({s for s in item["citations"] if s in valid_seqs})
            item["uncited"] = not item["citations"]
    return out


def _call_context(meta: dict, state: SessionState | None) -> str:
    lines = [f"## Call\nTitle: {meta.get('title') or 'Untitled'}"]
    if state is None:
        return lines[0]
    if state.opportunity_type:
        lines.append(f"Opportunity type: {state.opportunity_type}")
    if state.problem_summary:
        lines.append(f"Problem summary from the live pass: {state.problem_summary}")
    captured = [
        f"- {fid} ({fs.label}): {fs.value}"
        + (f" [turns {', '.join(f'#{s}' for s in fs.evidence)}]" if fs.evidence else "")
        for fid, fs in state.captured_fields()
    ]
    if captured:
        lines.append("## Captured during the call\n" + "\n".join(captured))
    open_fields = [f"- {fid}: {fs.label}" for fid, fs in state.open_fields()]
    if open_fields:
        lines.append("## Checklist fields still open\n" + "\n".join(open_fields))
    return "\n".join(lines)


def _windows(lines: list[str], max_chars: int) -> list[str]:
    """Split transcript lines into consecutive windows of at most ``max_chars``."""
    windows: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if windows[-1] and size + len(line) + 1 > max_chars:
            windows.append([])
            size = 0
        windows[-1].append(line)
        size += len(line) + 1
    return ["\n".join(w) for w in windows]


def _error_code(error: Exception) -> str:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(error, MinutesNotSubmittedError):
        return "not_submitted"
    return "model_error"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
