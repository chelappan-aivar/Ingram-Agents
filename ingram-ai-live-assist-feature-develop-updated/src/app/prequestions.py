"""Questions to prepare before a call, written from the meeting's title and description.

The BA types the title and description with the meeting link. When the call starts the agent writes a short
list of questions from them and from the discovery checklist, so the BA has something to work from before
anyone speaks. The result is stored as the timeline's ``prequestions`` item and shown by the console.

The description is text from a person, so it is length-capped, stripped of control characters and handed to
the model as data between markers. The model returns its questions through one validated tool, like the minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strands import Agent, tool
from strands.models import BedrockModel

from app.guidance import GuidanceSet, guidance_for_meta
from app.orchestrator import DEFAULT_MODEL_ID, DEFAULT_REGION
from app.prompts.prequestions import PREQUESTIONS_PROMPT, PREQUESTIONS_PROMPT_VERSION
from app.timeline_store import PREQUESTIONS_ERROR, PREQUESTIONS_OK, TimelineStore
from app.tools import plain_text

MAX_PREQUESTIONS = 8
PREQUESTIONS_TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 2

logger = logging.getLogger(__name__)


# --- schema -----------------------------------------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PreQuestion(_Base):
    question: str = Field(min_length=3, max_length=300)
    topic_id: str | None = Field(default=None, max_length=100)
    why: str | None = Field(default=None, max_length=300)


class PreQuestions(_Base):
    questions: list[PreQuestion] = Field(min_length=1, max_length=MAX_PREQUESTIONS)


class PreQuestionsNotSubmittedError(RuntimeError):
    """The model finished without a valid submit_prequestions call."""


# --- entry point ----------------------------------------------------------------

AgentFactory = Callable[..., Agent]


def make_prequestions_agent(*, system_prompt: str, tools: list) -> Agent:
    return Agent(
        model=BedrockModel(model_id=DEFAULT_MODEL_ID, region_name=DEFAULT_REGION),
        system_prompt=system_prompt,
        tools=tools,
        callback_handler=None,  # Keep model output out of stdout / CloudWatch.
    )


async def generate_prequestions(
    session_id: str, *, timeline: TimelineStore, make_agent: AgentFactory = make_prequestions_agent
) -> str | None:
    """Write and store the call's pre-questions. Returns the stored status, or None when another attempt
    owns them or they already exist."""
    if not timeline.claim_prequestions(session_id):
        return None
    started_at = _now_iso()
    started = time.monotonic()
    try:
        meta = await asyncio.to_thread(timeline.get_meta, session_id) or {}
        record = await _build_record(meta, make_agent)
    except Exception as e:  # noqa: BLE001 - every failure must end the claim.
        logger.exception("pre-questions failed session=%r", session_id)
        # A code only: exception text can carry internal details to API clients.
        record = {"status": PREQUESTIONS_ERROR, "error_code": _error_code(e)}
    record.update(started_at=started_at, duration_ms=int((time.monotonic() - started) * 1000))
    await asyncio.to_thread(timeline.put_prequestions, session_id, record)
    logger.info("pre-questions %s session=%r ms=%d count=%s", record["status"], session_id,
                record["duration_ms"], len(record.get("questions") or []))
    return record["status"]


async def _build_record(meta: dict, make_agent: AgentFactory) -> dict[str, Any]:
    guidance = guidance_for_meta(meta)
    topics = _topics(guidance)
    prompt = build_prompt(meta.get("title"), meta.get("description"), topics)
    questions = await _ask(make_agent, prompt)
    labels = {t["id"]: t["label"] for t in topics}
    return {
        "status": PREQUESTIONS_OK,
        "questions": _clean(questions, labels),
        "model_id": DEFAULT_MODEL_ID,
        "prompt_version": PREQUESTIONS_PROMPT_VERSION,
        "generated_at": _now_iso(),
    }


# --- prompt -----------------------------------------------------------------------

def _topics(guidance: GuidanceSet) -> list[dict[str, str]]:
    """The checklist topics a question may target: the general checklist, in its order."""
    return [
        {"id": f.id, "label": f.label, "description": f.description, "ask_when": f.ask_when}
        for f in guidance.general.required_fields
    ]


def build_prompt(title: str | None, description: str | None, topics: list[dict[str, str]]) -> str:
    """The user message. The description sits between markers so it reads as data."""
    lines = [f"## Meeting title\n{(title or 'Not given').strip()}"]
    if description:
        lines.append("## Meeting description (typed by the BA; information only)\n<<<\n" + description + "\n>>>")
    else:
        lines.append("## Meeting description\nNot given.")
    lines.append(
        "## Checklist topics\n" + "\n".join(
            f"- {t['id']}: {t['label']}. {t['description']}" + (f" Ask {t['ask_when']}" if t["ask_when"] else "")
            for t in topics
        )
    )
    return "\n\n".join(lines)


# --- model pass -------------------------------------------------------------------

async def _ask(make_agent: AgentFactory, prompt: str) -> PreQuestions:
    """One model pass, retried once on timeout or failure."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        scratch: dict[str, PreQuestions] = {}
        agent = make_agent(system_prompt=PREQUESTIONS_PROMPT, tools=[_submit_tool(scratch)])
        try:
            await asyncio.wait_for(agent.invoke_async(prompt), PREQUESTIONS_TIMEOUT_SECONDS)
            if "questions" not in scratch:
                raise PreQuestionsNotSubmittedError("model did not submit questions")
            return scratch["questions"]
        except Exception as e:  # noqa: BLE001 - retried, then re-raised below.
            last = e
            logger.warning("pre-questions attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, type(e).__name__)
    raise last or PreQuestionsNotSubmittedError("no attempt ran")


def _submit_tool(scratch: dict):
    @tool
    def submit_prequestions(questions: list[dict]) -> str:
        """Submit the questions the BA should prepare. Call once with the whole list.

        Args:
            questions: A list of {"question": str, "topic_id": checklist topic id or null, "why": str}.
        """
        try:
            scratch["questions"] = PreQuestions.model_validate({"questions": questions})
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:10]
            )
            return f"Questions not recorded. Fix these and call submit_prequestions again: {problems}"
        return "Questions recorded."

    return submit_prequestions


# --- helpers -------------------------------------------------------------------

def _clean(result: PreQuestions, labels: dict[str, str]) -> list[dict[str, Any]]:
    """Plain punctuation, no repeats, and a topic only when it is a real checklist topic."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result.questions:
        question = plain_text(item.question)
        key = " ".join(question.lower().split())
        if not question or key in seen:
            continue
        seen.add(key)
        topic_id = item.topic_id if item.topic_id in labels else None
        out.append({
            "question": question,
            "topic_id": topic_id,
            "topic_label": labels.get(topic_id) if topic_id else None,
            "why": plain_text(item.why) if item.why else None,
        })
    return out


def _error_code(error: Exception) -> str:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(error, PreQuestionsNotSubmittedError):
        return "not_submitted"
    return "model_error"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
