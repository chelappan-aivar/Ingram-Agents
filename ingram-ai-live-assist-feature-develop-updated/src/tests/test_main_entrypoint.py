"""Entry point tests: event dispatch, acks and rejections, with moto tables and
the real worker driven by a scripted model."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main
from app.processor import Processor
from app.views import build_handoff, build_session_view
from tests.fake_model import FakeModel, ScriptedToolCall


@pytest.fixture
def processor(timeline, session_store, monkeypatch):
    from strands import Agent
    from strands.tools.executors import SequentialToolExecutor

    def fake_make_agent(*, session_id, system_prompt, tools):
        model = FakeModel(tool_calls=[
            ScriptedToolCall("record_captured_field", {
                "field_id": "current_environment", "value": "on-prem", "speaker": "cx"}, "t1"),
            ScriptedToolCall("suggest_next_question", {
                "question": "Roughly how much data is there?", "target_field_id": "data_volumes",
                "rationale": "Hosting is known, data is not."}, "t2"),
        ], final_text="Captured hosting.")
        return Agent(model=model, system_prompt=system_prompt, tools=tools,
                     tool_executor=SequentialToolExecutor())

    monkeypatch.setattr("app.orchestrator._make_agent", fake_make_agent)
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0)
    monkeypatch.setattr(main, "get_processor", lambda: proc)
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    return proc


def _event(kind, sid, **fields):
    return {"schema": "ila.event.v1", "type": kind, "session_id": sid, **fields}


async def _send(payload, runtime_session_id=None):
    return await main.agent_invocation(payload, SimpleNamespace(session_id=runtime_session_id))


async def test_full_call_flow(processor, timeline, session_store, session_id):
    ack = await _send(_event("session.start", session_id, title="Demo",
                             assist_config={"scope": "guided", "max_suggestions": 2}), session_id)
    assert ack == {"accepted": True, "type": "session.start", "session_id": session_id,
                   "created": True, "status": "live", "config_id": "ingram-default", "config_version": 0}

    ack = await _send(_event("turn", session_id, speaker="cx", text="We run on-prem."), session_id)
    assert ack["accepted"] and ack["seq"] == 1

    ack = await _send(_event("session.update", session_id, status="paused"), session_id)
    assert ack["status"] == "paused" and ack["assist_config"]["scope"] == "guided"

    ack = await _send(_event("session.end", session_id, reason="done"), session_id)
    assert ack["status"] == "ended" and ack["ended_now"] is True
    await processor.wait_idle()

    view = build_session_view(session_id, timeline=timeline, store=session_store)
    assert view["status"] == "ended" and view["title"] == "Demo"
    assert view["processing"] == {"latest_seq": 1, "last_processed_seq": 1, "pending_turns": 0,
                                  "run_count": 1, "busy": False}
    assert view["turns"][0]["processed"] and view["turns"][0]["run_first_seq"] == 1
    run = view["runs"][0]
    assert run["status"] == "ok" and run["narration"] == ["Captured hosting."]
    assert run["captured_fields"] == ["current_environment"]
    assert run["settings"]["scope"] == "guided"
    assert view["suggestions"][0]["question"] == "Roughly how much data is there?"
    assert view["suggestions"][0]["rationale"] == "Hosting is known, data is not."
    assert {c["field_id"] for c in view["state"]["captured"]} == {"current_environment"}
    assert view["state"]["captured"][0]["category"] == "Technical"

    handoff = build_handoff(view)
    assert handoff["complete"] is True
    assert handoff["suggested_questions"] == ["Roughly how much data is there?"]


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"type": "turn"}, "invalid_event"),
        (_event("turn", "s" * 40, speaker="cx", text="hi"), "unknown_session"),
    ],
)
async def test_rejections(processor, payload, code):
    ack = await _send(payload)
    assert ack["accepted"] is False and ack["code"] == code and ack["error"]


async def test_turn_after_end_rejected(processor, session_id):
    await _send(_event("session.start", session_id))
    await _send(_event("session.end", session_id))
    ack = await _send(_event("turn", session_id, speaker="cx", text="late"))
    assert ack["code"] == "session_ended"
    await processor.wait_idle()


async def test_runtime_session_must_match(processor, session_id):
    ack = await _send(_event("session.start", session_id), runtime_session_id="x" * 40)
    assert ack["code"] == "session_mismatch"


async def test_turn_text_is_not_logged(processor, session_id, caplog):
    caplog.set_level("INFO", logger="live_assist")
    await _send(_event("session.start", session_id))
    await _send(_event("turn", session_id, speaker="cx", text="SECRET customer words"))
    await processor.wait_idle()
    assert "event accepted type=turn" in caplog.text
    assert "SECRET" not in caplog.text
