"""Minutes of meeting: generation, citation checks, storage claims, the final pass and export."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from strands import Agent

from app import minutes as minutes_module
from app.assist_config import AssistConfig
from app.events import SessionStart, Turn
from app.minutes import Minutes, clean_minutes, generate_minutes
from app.minutes_export import minutes_markdown
from app.processor import Processor
from app.session_store import SessionState
from app.views import build_handoff, build_session_view
from tests.fake_model import FakeModel, ScriptedToolCall

GOOD = {
    "summary": "The customer wants to move two apps to AWS.",
    "attendees": [{"speaker": "ba", "name": "Bo"}, {"speaker": "cx", "name": "Alice"}],
    "key_points": [{"text": "Two apps run on-prem.", "citations": [2, 42]}],
    "decisions": [{"text": "Start with the billing app.", "citations": [3]}],
    "action_items": [{"text": "Send the inventory.", "owner": "cx", "due": "Friday", "citations": [3]}],
    "risks": [{"text": "Licence renewal in May.", "citations": []}],
    "open_questions": [{"text": "Target region?", "field_id": "target_region", "citations": [1]}],
    "next_steps": [],
}


class Factory:
    """make_agent stand-in: one FakeModel per attempt, scripted in order."""

    def __init__(self, *scripts, delay: float = 0):
        self.scripts = list(scripts)
        self.prompts: list[str] = []
        self.delay = delay

    def __call__(self, *, system_prompt, tools):
        calls = self.scripts.pop(0) if self.scripts else []
        factory = self

        class _Agent(Agent):
            async def invoke_async(self, prompt, *a, **kw):
                factory.prompts.append(prompt)
                if factory.delay:
                    await asyncio.sleep(factory.delay)
                return await super().invoke_async(prompt, *a, **kw)

        return _Agent(model=FakeModel(tool_calls=calls), system_prompt=system_prompt, tools=tools,
                      callback_handler=None)


def _submit(payload=GOOD, tool_use_id="m1"):
    return [ScriptedToolCall("submit_minutes", payload, tool_use_id)]


def _call_with_turns(timeline, session_id, texts=("Hello.", "We run on-prem.", "Start with billing."), end=True):
    timeline.start_session(SessionStart(session_id, "manual", "Discovery", AssistConfig()))
    for i, text in enumerate(texts):
        timeline.append_turn(Turn(session_id, "manual", "ba" if i % 2 == 0 else "cx", text))
    if end:
        timeline.end_session(session_id, "done")


# --- schema and citations -----------------------------------------------------------

def test_clean_drops_unknown_citations_and_flags_uncited():
    out = clean_minutes(Minutes.model_validate(GOOD), {1, 2, 3})
    assert out["key_points"][0] == {"text": "Two apps run on-prem.", "citations": [2], "uncited": False}
    assert out["risks"][0]["uncited"] is True
    assert out["action_items"][0]["owner"] == "cx"


def test_windows_split_long_transcripts():
    lines = ["x" * 40] * 5
    assert len(minutes_module._windows(lines, 100)) == 3
    assert minutes_module._windows(lines, 10_000) == ["\n".join(lines)]


# --- generation ---------------------------------------------------------------------

async def test_generates_and_stores_minutes(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id)
    state = SessionState.new(session_id)
    state.add_field("scope", label="Scope")
    state.set_field_captured("scope", "Billing app first", "cx", [3])
    session_store.save(state)
    factory = Factory(_submit())

    status = await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=factory)

    assert status == "ok"
    record = timeline.get_minutes(session_id)
    assert record["status"] == "ok" and record["turn_count"] == 3 and record["transcript_last_seq"] == 3
    assert record["minutes"]["key_points"][0]["citations"] == [2]
    assert record["prompt_version"] == "minutes-v1"
    prompt = factory.prompts[0]
    assert "[#2 | cx]: We run on-prem." in prompt
    assert "scope (Scope): Billing app first [turns #3]" in prompt


async def test_invalid_submission_is_reported_to_the_model_and_fixed(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id)
    bad = {**GOOD, "attendees": [{"speaker": "someone"}]}
    # The bad payload is rejected and the attempt ends without minutes; the retry submits a good one.
    status = await generate_minutes(
        session_id, timeline=timeline, store=session_store, make_agent=Factory(_submit(bad), _submit()),
    )
    assert status == "ok"


async def test_no_submission_after_retries_records_error_code_only(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id)
    status = await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=Factory([], []))

    record = timeline.get_minutes(session_id)
    assert status == "error" and record["error_code"] == "not_submitted"
    assert "minutes" not in record or record.get("minutes") is None


async def test_timeout_records_error(timeline, session_store, session_id, monkeypatch):
    _call_with_turns(timeline, session_id)
    monkeypatch.setattr(minutes_module, "MINUTES_TIMEOUT_SECONDS", 0.01)
    status = await generate_minutes(
        session_id, timeline=timeline, store=session_store, make_agent=Factory(_submit(), _submit(), delay=0.2),
    )
    assert status == "error" and timeline.get_minutes(session_id)["error_code"] == "timeout"


async def test_call_without_turns_is_empty(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id, texts=())
    factory = Factory()
    assert await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=factory) == "empty"
    assert factory.prompts == []


async def test_long_call_is_summarised_in_windows_then_merged(timeline, session_store, session_id, monkeypatch):
    _call_with_turns(timeline, session_id, texts=["a" * 60, "b" * 60, "c" * 60])
    monkeypatch.setattr(minutes_module, "MAX_TRANSCRIPT_CHARS", 100)
    factory = Factory(_submit(), _submit(), _submit(), _submit())

    assert await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=factory) == "ok"
    assert len(factory.prompts) == 4  # 3 windows + merge
    assert "part 1 of 3" in factory.prompts[0] and "Partial minutes" in factory.prompts[3]
    assert timeline.get_minutes(session_id)["windows"] == 3


# --- claims -------------------------------------------------------------------------

async def test_existing_minutes_are_kept_unless_regenerated(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id)
    await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=Factory(_submit()))

    assert await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=Factory()) is None
    assert await generate_minutes(
        session_id, timeline=timeline, store=session_store, regenerate=True, make_agent=Factory(_submit()),
    ) == "ok"


def test_claim_rules(timeline, session_id, monkeypatch):
    assert timeline.claim_minutes(session_id) is True
    assert timeline.claim_minutes(session_id, regenerate=True) is False  # Generating, not stale.
    monkeypatch.setattr("app.timeline_store.MINUTES_STALE_SECONDS", -60)
    assert timeline.claim_minutes(session_id) is True  # Stale claim is retaken.
    timeline.put_minutes(session_id, {"status": "error", "error_code": "timeout"})
    monkeypatch.setattr("app.timeline_store.MINUTES_STALE_SECONDS", 900)
    assert timeline.claim_minutes(session_id) is True  # Failed attempts can be retried.


# --- final pass in the processor ----------------------------------------------------

class RecordingMinutes:
    def __init__(self):
        self.calls: list[bool] = []

    async def __call__(self, session_id, *, timeline, store, regenerate):
        self.calls.append(regenerate)
        return "ok"


async def _noop_run(**_kw):  # Never called: the tests below have no pending turns.
    raise AssertionError("unexpected run")


async def test_final_pass_runs_once_after_end(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id, texts=())
    minutes = RecordingMinutes()
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=_noop_run, minutes=minutes)

    proc.schedule(session_id)
    await proc.wait_idle()
    assert minutes.calls == [False]

    proc.schedule(session_id, regenerate_minutes=True)
    await proc.wait_idle()
    assert minutes.calls == [False, True]


async def test_live_call_gets_no_minutes(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id, texts=(), end=False)
    minutes = RecordingMinutes()
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=_noop_run, minutes=minutes)
    proc.schedule(session_id)
    await proc.wait_idle()
    assert minutes.calls == []


async def test_failed_last_batch_of_ended_call_is_retried_once(timeline, session_store, session_id):
    from tests.test_processor import FakeRun

    _call_with_turns(timeline, session_id, texts=("only turn",))
    run = FakeRun(session_store, fail_times=1)
    minutes = RecordingMinutes()
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run, minutes=minutes)
    proc.schedule(session_id)
    await proc.wait_idle()

    assert run.batches == [["only turn"], ["only turn"]]
    assert minutes.calls == [False]


# --- views and export ---------------------------------------------------------------

async def test_view_handoff_and_markdown(timeline, session_store, session_id):
    _call_with_turns(timeline, session_id)
    view = build_session_view(session_id, timeline=timeline, store=session_store)
    assert view["minutes"] == {"status": "none"}

    await generate_minutes(session_id, timeline=timeline, store=session_store, make_agent=Factory(_submit()))
    view = build_session_view(session_id, timeline=timeline, store=session_store)
    assert view["minutes"]["status"] == "ok"
    assert len(view["turns"]) == 3  # The minutes item is not mistaken for a turn or run.

    handoff = build_handoff(view)
    assert handoff["summary"] == GOOD["summary"] and handoff["minutes"]["status"] == "ok"

    md = minutes_markdown(view)
    assert "## Summary" in md and "Two apps run on-prem. [#2]" in md
    assert "Licence renewal in May. _(no citation)_" in md
    assert "(owner: cx; due: Friday) [#3]" in md


def test_markdown_escapes_model_output():
    view = {"session_id": "s", "title": "T", "minutes": {"status": "ok", "minutes": {
        **GOOD, "summary": "<script>x</script>", "key_points": [{"text": "[link](javascript:x)", "citations": [1]}],
    }}}
    md = minutes_markdown(view)
    assert "<script>" not in md and "\\[link\\](javascript:x)" in md


@pytest.mark.parametrize("payload", [{"summary": ""}, {"summary": "ok", "key_points": [{"text": ""}]}])
def test_schema_rejects_empty_text(payload):
    with pytest.raises(ValidationError):
        Minutes.model_validate(payload)
