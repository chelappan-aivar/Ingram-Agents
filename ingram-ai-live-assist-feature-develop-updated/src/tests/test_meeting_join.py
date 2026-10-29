"""The ``meeting.join`` event and the runner that handles it inside the runtime session."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.assist_config import AssistConfig
from app.events import InvalidEventError, MeetingJoin, SessionStart, parse_event
from app.meeting_join import MeetingRunner
from app.vexa_client import VexaSettings
from tests.fake_vexa import BOT_KEY, BOTH_KEY, TX_KEY, FakeVexa, status

SID = "call-0001-abcdefghijklmnopqrstuvwxyz0123"
URL = "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34"


def join_payload(**over):
    payload = {"schema": "ila.event.v1", "type": "meeting.join", "session_id": SID, "meeting_url": URL,
               "vexa_api_key": BOTH_KEY}
    payload.update(over)
    return payload


# --- the event -----------------------------------------------------------------------------------


def test_a_valid_join_event_is_parsed():
    event = parse_event(join_payload(ba_names=["Ravi Menon"], bot_name="Assist", vexa_tx_key=TX_KEY))
    assert isinstance(event, MeetingJoin)
    assert (event.meeting_url, event.ba_names, event.bot_name) == (URL, ("Ravi Menon",), "Assist")
    assert (event.vexa_api_key, event.vexa_tx_key) == (BOTH_KEY, TX_KEY)


def test_the_keys_stay_out_of_repr_and_logs_and_tracebacks():
    event = parse_event(join_payload(vexa_tx_key=TX_KEY))
    for text in (repr(event), str(event)):
        assert BOTH_KEY not in text and TX_KEY not in text


@pytest.mark.parametrize("over, fragment", [
    ({"vexa_api_key": None}, "'vexa_api_key' is required"),
    ({"vexa_api_key": ""}, "'vexa_api_key' is required"),
    ({"vexa_api_key": "short"}, "does not look like an API key"),
    ({"vexa_api_key": "has a space in it"}, "does not look like an API key"),
    ({"vexa_api_key": "x" * 301}, "does not look like an API key"),
    ({"vexa_api_key": 12345678}, "does not look like an API key"),
    ({"vexa_tx_key": "bad key"}, "'vexa_tx_key' does not look like an API key"),
    ({"meeting_url": ""}, "'meeting_url'"),
    ({"meeting_url": "x" * 3000}, "'meeting_url'"),
    ({"ba_names": 5}, "'ba_names'"),
    ({"extra": 1}, "unknown keys"),
])
def test_invalid_join_events_are_refused_without_echoing_the_key(over, fragment):
    with pytest.raises(InvalidEventError) as error:
        parse_event(join_payload(**over))
    assert fragment in str(error.value)
    for value in over.values():
        if isinstance(value, str) and "key" in str(over) and len(value) > 3 and value != URL:
            assert value not in str(error.value)


def test_the_tx_key_is_optional():
    assert parse_event(join_payload()).vexa_tx_key == ""


# --- the runner ----------------------------------------------------------------------------------


class Tracker:
    def __init__(self):
        self.added, self.completed = [], []

    def add_async_task(self, name, metadata=None):
        self.added.append((name, metadata))
        return len(self.added)

    def complete_async_task(self, task_id):
        self.completed.append(task_id)
        return True


@pytest.fixture
def vexa():
    fake = FakeVexa()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def delivered():
    return []


@pytest.fixture
def runner(timeline, vexa, delivered):
    async def deliver(event):
        delivered.append(event)
        return {"accepted": True}

    return MeetingRunner(timeline=timeline, deliver=deliver, tracker=Tracker(),
                         base_settings=VexaSettings(api_base=vexa.base, promote_after_s=0.0))


def start_call(timeline, source="meeting", session_id=SID):
    timeline.start_session(SessionStart(session_id=session_id, source=source, title="t", assist_config=AssistConfig()))


def join(runner, **over):
    return asyncio.run(_join_and_settle(runner, parse_event(join_payload(**over))))


async def _join_and_settle(runner, event):
    ack = await runner.join(event)
    task = runner._tasks.get(event.session_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return ack


@pytest.mark.parametrize("setup, code", [
    (lambda tl: None, "unknown_session"),
    (lambda tl: start_call(tl, source="manual"), "source_mismatch"),
])
def test_the_call_must_exist_and_be_a_meeting_call(runner, timeline, vexa, setup, code):
    setup(timeline)
    ack = join(runner)
    assert (ack["accepted"], ack["code"]) == (False, code) and vexa.bot_requests == []


def test_an_ended_call_cannot_be_joined(runner, timeline, vexa):
    start_call(timeline)
    timeline.end_session(SID, "done")
    assert join(runner)["code"] == "session_ended" and vexa.bot_requests == []


def test_a_bad_link_is_refused_before_vexa_is_called(runner, timeline, vexa):
    start_call(timeline)
    ack = join(runner, meeting_url="https://evil.example.com/x")
    assert ack["code"] == "invalid_meeting_link" and vexa.bot_requests == []


def test_a_key_without_the_transcript_scope_is_refused_and_not_echoed(runner, timeline, vexa, caplog):
    caplog.set_level(logging.DEBUG)
    start_call(timeline)
    ack = join(runner, vexa_api_key=BOT_KEY)
    assert ack["code"] == "meeting_not_authorized" and vexa.bot_requests == []
    assert BOT_KEY not in str(ack) and BOT_KEY not in caplog.text


def test_a_vexa_error_that_repeats_the_key_is_scrubbed(runner, timeline, vexa):
    start_call(timeline)
    vexa.bot_response = (403, {"detail": f"key {BOTH_KEY} has no balance"})
    ack = join(runner)
    assert ack["code"] == "meeting_not_authorized" and BOTH_KEY not in str(ack) and "***" in ack["error"]


def test_a_good_join_sends_the_bot_and_starts_following(runner, timeline, vexa):
    start_call(timeline)
    ack = join(runner)
    assert ack == {"accepted": True, "meeting": "joining", "platform": "teams"}
    assert len(vexa.bot_requests) == 1 and vexa.bot_requests[0]["bot_name"] == "Ingram Live Assist"


def test_the_follower_keeps_the_session_busy_and_releases_it(runner, timeline, vexa):
    start_call(timeline)
    vexa.script = [("sleep", 0.3), status("completed")]

    async def go():
        await runner.join(parse_event(join_payload()))
        busy = runner.is_following(SID)
        again = await runner.join(parse_event(join_payload()))  # a repeated join is harmless
        await asyncio.wait_for(runner._tasks[SID], timeout=15)
        return busy, again

    busy, again = asyncio.run(go())
    assert busy and again == {"accepted": True, "meeting": "already_following"}
    assert len(vexa.bot_requests) == 1
    assert runner._tracker.added == [("meeting", {"session_id": SID})]
    assert runner._tracker.completed == [1]  # the runtime may idle the session once the meeting is over
    assert not runner.is_following(SID)


def test_a_crashing_source_still_ends_the_call(timeline, vexa, delivered, monkeypatch):
    start_call(timeline)

    async def boom(**_kw):
        raise RuntimeError("source blew up")

    monkeypatch.setattr("app.meeting_join.run_meeting", boom)

    async def deliver(event):
        delivered.append(event)
        return {"accepted": True}

    runner = MeetingRunner(timeline=timeline, deliver=deliver, tracker=Tracker(),
                           base_settings=VexaSettings(api_base=vexa.base, promote_after_s=0.0))

    async def go():
        await runner.join(parse_event(join_payload()))
        await asyncio.wait_for(runner._tasks[SID], timeout=15)

    asyncio.run(go())
    assert [(e["type"], e["reason"]) for e in delivered] == [("session.end", "source failed")]


# --- what is stored --------------------------------------------------------------------------------


def test_set_meeting_status_stores_only_platform_and_status(timeline):
    start_call(timeline)
    timeline.set_meeting_status(SID, "teams", "active")
    assert timeline.get_meta(SID)["meeting"] == {"platform": "teams", "bot": "active"}


def test_set_meeting_status_ignores_a_call_that_is_gone(timeline):
    timeline.set_meeting_status(SID, "teams", "active")
    assert timeline.get_meta(SID) is None  # no empty record was created


def test_the_keys_never_reach_the_logs_even_at_debug_level(runner, timeline, vexa, caplog):
    """The Vexa client sends the key as a header on REST and WebSocket; HTTP libraries log headers at DEBUG."""
    caplog.set_level(logging.DEBUG)
    start_call(timeline)
    vexa.script = [status("active"), status("completed")]

    async def go():
        await runner.join(parse_event(join_payload(vexa_tx_key=TX_KEY)))
        await asyncio.wait_for(runner._tasks[SID], timeout=15)

    asyncio.run(go())
    assert vexa.ws_keys, "the WebSocket connection was never made, so this test proved nothing"
    assert BOTH_KEY not in caplog.text and TX_KEY not in caplog.text
