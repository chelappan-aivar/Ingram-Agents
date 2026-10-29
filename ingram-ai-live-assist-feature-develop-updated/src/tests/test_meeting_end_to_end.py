"""A meeting streams sentences; the real API, entry point, stores and agent loop turn them into assist.

Only two things are stand-ins: Vexa (``FakeVexa`` on a real socket) and the language model
(``FakeModel`` scripted to call the agent's tools). Everything else is the production code path.
"""
from __future__ import annotations

import time

import boto3
import pytest
from botocore.config import Config
from strands import Agent

import main
from api.app import Api, Services
from app.meeting_join import MeetingRunner
from app.processor import Processor
from app.vexa_client import VexaSettings
from tests.fake_model import FakeModel, ScriptedToolCall
from tests.fake_vexa import BOTH_KEY, FakeVexa, segment, transcript
from tests.loop_sender import LoopSender
from tests.test_api import KEY, call

URL = "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34"


@pytest.fixture
def vexa():
    fake = FakeVexa()
    fake.start()
    yield fake
    fake.stop()


def test_customer_sentences_become_captured_facts_and_a_suggested_question(
    vexa, timeline, session_store, dynamodb_resource, monkeypatch
):
    sender = LoopSender()
    runner = MeetingRunner(timeline=timeline, deliver=main._deliver_in_process,
                           base_settings=VexaSettings(api_base=vexa.base, promote_after_s=0.0))
    monkeypatch.setattr(main, "get_meetings", lambda: runner)
    prompts: list[str] = []

    def fake_make_agent(*, session_id, system_prompt, tools):
        """A new scripted model per run, like a real one that decides from what it is shown."""
        prompts.append(system_prompt)
        return Agent(model=FakeModel(tool_calls=list(scripted_run())), system_prompt=system_prompt,
                     tools=tools, callback_handler=None)

    def scripted_run():
        # Run 1 sees only the BA's question: nothing to capture, so it just asks the next one.
        # Run 2 sees the customer's answer: it captures it (citing turn 2) and asks about the region.
        if len(prompts) == 1:
            return [ScriptedToolCall("suggest_next_question", {
                "target_field_id": "current_environment", "question": "Where do the workloads run today?",
                "rationale": "Not covered yet."}, "t0")]
        return [
            ScriptedToolCall("record_captured_field", {
                "field_id": "current_environment", "value": "60 VMs on-prem in Dallas", "speaker": "cx",
                "evidence_seqs": [2]}, "t1"),
            ScriptedToolCall("suggest_next_question", {
                "target_field_id": "target_region", "question": "Which AWS region should this land in?",
                "rationale": "Hosting is known; the region is not."}, "t2"),
        ]

    monkeypatch.setattr("app.orchestrator._make_agent", fake_make_agent)
    processor = Processor(timeline=timeline, store=session_store, debounce_seconds=0)
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    monkeypatch.setattr(main, "get_default_store", lambda: session_store)
    monkeypatch.setattr(main, "get_processor", lambda: processor)

    vexa.script = [
        ("sleep", 0.3),
        transcript(confirmed=[segment("a1", "Tell me about your current hosting.", speaker="Ravi Menon (Partner)",
                                       key="csrc:1", completed=True)], speaker="csrc:1"),
        ("sleep", 1.0),  # the first agent run starts before the answer arrives, as in a real conversation
        transcript(confirmed=[segment("b1", "We run sixty VMs on-prem in Dallas.", speaker="Helen Brooks",
                                       key="csrc:2", completed=True)], speaker="csrc:2"),
    ]
    s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
    api = Api(Services(
        timeline=timeline, store=session_store, sender=sender, s3=s3, bucket="b", start_source=lambda _j: None,
        access_key=lambda: KEY))

    status_code, _, out = call(api, "POST", "/calls", {
        "title": "Acme discovery", "assist_config": {"scope": "checklist", "max_suggestions": 1},
        "source": {"type": "meeting", "meeting_url": URL, "ba_names": "Ravi Menon", "vexa_api_key": BOTH_KEY}})
    assert status_code == 201, out
    sid = out["session_id"]

    # The assist appears while the meeting is still going, not only at the end.
    view = None
    for _ in range(100):
        time.sleep(0.1)
        view = call(api, "GET", f"/calls/{sid}")[2]
        if len(view["suggestions"]) == 2 and not view["processing"]["busy"]:
            break
    assert view["status"] == "live"
    assert [(t["speaker"], t["speaker_name"]) for t in view["turns"]] == [("ba", "Ravi Menon (Partner)"), ("cx", "Helen Brooks")]
    assert [c["field_id"] for c in view["state"]["captured"]] == ["current_environment"]
    assert view["state"]["captured"][0]["value"] == "60 VMs on-prem in Dallas"
    assert view["state"]["captured"][0]["evidence"] == [2]
    assert [s["question"] for s in view["suggestions"]] == [
        "Where do the workloads run today?", "Which AWS region should this land in?"]
    assert "current_environment" not in {g["field_id"] for g in view["state"]["open_gaps"]}
    assert view["accepts_manual_turns"] is False

    # Ending the call from the console makes the bot leave and stops the source.
    assert call(api, "POST", f"/calls/{sid}/end", {})[2]["status"] == "ended"
    for _ in range(100):
        time.sleep(0.1)
        if vexa.stop_requests:
            break
    assert vexa.stop_requests == ["teams/123456789012"]
    for _ in range(100):  # the follower finishes once the bot has been told to leave
        if not runner.is_following(sid):
            break
        time.sleep(0.1)
    assert not runner.is_following(sid)
    assert timeline.get_meta(sid)["meeting"]["bot"] == "completed"
    sender.wait_idle(processor)
    sender.close()
