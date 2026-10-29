"""API tests for meeting and transcript calls: real handlers and entry point, moto DynamoDB, fake Vexa.

The Vexa keys are typed in the console and travel in a ``meeting.join`` event to the runtime. These tests
run that path end to end and check that the keys are never stored or logged.
"""
from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config

import main
from api.app import Api, Request, Services
from app.meeting_join import MeetingRunner
from app.vexa_client import VexaSettings
from tests.conftest import TEST_TABLE_NAME, TEST_TIMELINE_TABLE
from tests.fake_vexa import BOT_KEY, BOTH_KEY, TX_KEY, FakeVexa
from tests.loop_sender import LoopSender
from tests.test_api import KEY, call

TEAMS_URL = "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34"


@pytest.fixture
def vexa():
    fake = FakeVexa()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def make_api(timeline, session_store, dynamodb_resource, monkeypatch, vexa):
    s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
    s3.create_bucket(Bucket="test-uploads")
    sender = LoopSender()
    runner = MeetingRunner(
        timeline=timeline, deliver=main._deliver_in_process,
        base_settings=VexaSettings(api_base=vexa.base, promote_after_s=0.0))
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(
        schedule=lambda _sid, **_kw: None, prepare_questions=lambda _sid: None))
    monkeypatch.setattr(main, "get_meetings", lambda: runner)
    jobs: list[dict] = []

    def build():
        return Api(Services(
            timeline=timeline, store=session_store, sender=sender, s3=s3, bucket="test-uploads",
            start_source=jobs.append, access_key=lambda: KEY)), jobs

    yield build
    sender.close()


def meeting_source(**extra):
    return {"type": "meeting", "meeting_url": TEAMS_URL, "vexa_api_key": BOTH_KEY, **extra}


def stored_text(dynamodb_resource) -> str:
    """Everything in both tables, as text."""
    items = []
    for name in (TEST_TABLE_NAME, TEST_TIMELINE_TABLE):
        items += dynamodb_resource.Table(name).scan()["Items"]
    return json.dumps(items, default=str)


# --- options ---------------------------------------------------------------------------------


def test_options_report_the_new_sources(make_api):
    api, _ = make_api()
    sources = call(api, "GET", "/options")[2]["sources"]
    assert sources["meeting"]["enabled"] is True and "zoom" in sources["meeting"]["platforms"]
    assert sources["meeting"]["needs_vexa_key"] is True
    assert sources["transcript"]["formats"] == ["txt", "vtt", "srt", "json"]


# --- meeting ---------------------------------------------------------------------------------


def test_meeting_call_starts_the_call_then_the_runtime_sends_the_bot(make_api, vexa, timeline):
    api, _ = make_api()
    status, _, out = call(api, "POST", "/calls", {
        "title": "Acme discovery",
        "source": meeting_source(ba_names="Ravi Menon, Asha Rao", bot_name="Ingram <b>Assist</b>")})
    assert status == 201 and out["source"] == "meeting" and out["status"] == "live"
    assert vexa.bot_requests == [{
        "bot_name": "Ingram bAssistb", "transcribe_enabled": True, "platform": "teams",
        "native_meeting_id": "123456789012", "passcode": "AbCdEf12GhIj34"}]
    sid = out["session_id"]
    assert [e["type"] for e in api.s.sender.events] == ["session.start", "meeting.join"]
    assert api.s.sender.events[1]["ba_names"] == ["Ravi Menon", "Asha Rao"]
    meta = timeline.get_meta(sid)
    assert meta["source"] == "meeting" and meta["exclusive_source"] == "meeting"
    # The console must not offer a typing box on a call fed by a meeting.
    view = call(api, "GET", f"/calls/{sid}")[2]
    assert view["accepts_manual_turns"] is False
    manual = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "ba", "text": "hi"})
    assert manual[0] == 409 and manual[2]["error"]["code"] == "source_mismatch"


def test_the_call_view_tells_what_the_bot_is_doing(make_api, vexa):
    api, _ = make_api()
    sid = call(api, "POST", "/calls", {"source": meeting_source()})[2]["session_id"]
    meeting = None
    for _ in range(50):
        meeting = call(api, "GET", f"/calls/{sid}")[2]["meeting"]
        if meeting:
            break
        time.sleep(0.1)
    assert meeting == {"platform": "teams", "bot": "requested"}
    manual = call(api, "POST", "/calls", {})[2]["session_id"]
    assert call(api, "GET", f"/calls/{manual}")[2]["meeting"] is None


def test_the_vexa_keys_are_never_stored_or_logged(make_api, vexa, dynamodb_resource, caplog):
    caplog.set_level(logging.DEBUG)
    api, _ = make_api()
    status, headers, out = call(api, "POST", "/calls", {"source": meeting_source(vexa_tx_key=TX_KEY)})
    assert status == 201
    sid = out["session_id"]
    for _ in range(50):  # let the runtime record the bot status
        if call(api, "GET", f"/calls/{sid}")[2]["meeting"]:
            break
        time.sleep(0.1)
    view = json.dumps(call(api, "GET", f"/calls/{sid}")[2]) + json.dumps(out)
    for secret in (BOTH_KEY, TX_KEY):
        assert secret not in stored_text(dynamodb_resource)
        assert secret not in view
        assert secret not in caplog.text


def test_a_key_that_cannot_read_transcripts_is_refused_before_any_bot_joins(make_api, vexa, timeline):
    api, _ = make_api()
    status, _, out = call(api, "POST", "/calls", {"source": meeting_source(vexa_api_key=BOT_KEY)})
    assert status == 502 and out["error"]["code"] == "meeting_not_authorized" and "tx" in out["error"]["message"]
    assert BOT_KEY not in json.dumps(out)
    assert vexa.bot_requests == []
    assert [m["status"] for m in timeline.list_calls(limit=10)[0]] == ["ended"]  # the call is closed, not left live


def test_a_separate_transcription_key_makes_a_bot_only_key_work(make_api, vexa):
    api, _ = make_api()
    status, _, _ = call(api, "POST", "/calls", {"source": meeting_source(vexa_api_key=BOT_KEY, vexa_tx_key=TX_KEY)})
    assert status == 201 and len(vexa.bot_requests) == 1


@pytest.mark.parametrize("code, http, api_code", [
    (409, 409, "meeting_busy"), (429, 429, "meeting_limit"), (422, 400, "invalid_meeting_link"),
    (500, 502, "meeting_unavailable"),
])
def test_bot_request_failures_map_to_clear_errors(make_api, vexa, timeline, code, http, api_code):
    vexa.bot_response = (code, {"detail": "no"})
    api, _ = make_api()
    status, _, out = call(api, "POST", "/calls", {"source": meeting_source()})
    assert (status, out["error"]["code"]) == (http, api_code)
    assert [m["status"] for m in timeline.list_calls(limit=10)[0]] == ["ended"]


@pytest.mark.parametrize("source, code", [
    ({"type": "meeting", "vexa_api_key": BOTH_KEY}, "invalid_meeting_link"),
    ({"type": "meeting", "meeting_url": "https://evil.example.com/x", "vexa_api_key": BOTH_KEY},
     "invalid_meeting_link"),
    (meeting_source(ba_names=5), "invalid_request"),
    (meeting_source(bot_name=5), "invalid_request"),
    ({"type": "meeting", "meeting_url": TEAMS_URL}, "invalid_request"),  # no key
    (meeting_source(vexa_api_key=""), "invalid_request"),
    (meeting_source(vexa_api_key="has spaces in it"), "invalid_request"),
    (meeting_source(vexa_api_key=12345678), "invalid_request"),
    (meeting_source(vexa_tx_key="short"), "invalid_request"),
])
def test_meeting_validation_happens_before_a_call_is_created(make_api, vexa, timeline, source, code):
    api, _ = make_api()
    status, _, out = call(api, "POST", "/calls", {"source": source})
    assert status == 400 and out["error"]["code"] == code
    assert vexa.bot_requests == [] and timeline.list_calls(limit=10)[0] == []
    assert api.s.sender.events == []


def test_a_rejected_key_is_not_echoed_back(make_api):
    api, _ = make_api()
    bad = "secret value with spaces"
    status, _, out = call(api, "POST", "/calls", {"source": meeting_source(vexa_api_key=bad)})
    assert status == 400 and bad not in json.dumps(out)


# --- transcript ------------------------------------------------------------------------------


def test_transcript_call_plays_the_text_as_a_recording_job(make_api):
    api, jobs = make_api()
    text = "Ravi Menon: Tell me about the project.\nHelen Brooks: We run on-prem and want AWS."
    status, _, out = call(api, "POST", "/calls", {"source": {
        "type": "transcript", "text": text, "ba_names": ["Ravi Menon"], "speed": 20}})
    assert status == 201 and out["source"] == "transcript"
    job = jobs[0]
    assert (job["mode"], job["speed"], job["session_id"], job["send_start"]) == ("recording", 20.0, out["session_id"], False)
    assert [(t["speaker"], t["name"]) for t in job["conversation"]["conversation"]] == [
        ("ba", "Ravi Menon"), ("cx", "Helen Brooks")]
    view = call(api, "GET", f"/calls/{out['session_id']}")[2]
    assert view["source"] == "recording" and view["accepts_manual_turns"] is False


def test_a_long_transcript_fits_through_the_call_endpoint(make_api):
    api, jobs = make_api()
    # Accented names are escaped to six bytes each in JSON, so a body passes 64 KB well before the text limit.
    line = "Hélène Brüggemann: Línea número {i} de la llamada, y sigue durante un rato más, ¿verdad? Ça va."
    text = "\n".join(line.format(i=i) for i in range(560))
    body = {"source": {"type": "transcript", "text": text}}
    assert len(text) < 60_000 and len(json.dumps(body)) > 64 * 1024
    status, _, out = call(api, "POST", "/calls", body)
    assert status == 201, out
    assert len(jobs[0]["conversation"]["conversation"]) == 560


@pytest.mark.parametrize("source, status, code", [
    ({"type": "transcript"}, 400, "invalid_upload"),
    ({"type": "transcript", "text": "   "}, 400, "invalid_upload"),
    ({"type": "transcript", "text": "x" * 60_001}, 400, "invalid_upload"),
    ({"type": "transcript", "text": "Helen: hi there", "speed": 0}, 400, "invalid_request"),
    ({"type": "transcript", "text": "Helen: hi there", "speed": 101}, 400, "invalid_request"),
    ({"type": "transcript", "text": "Helen: hi there", "speed": True}, 400, "invalid_request"),
    ({"type": "transcript", "text": "Helen: hi there", "ba_names": [1]}, 400, "invalid_request"),
])
def test_transcript_validation(make_api, source, status, code):
    api, jobs = make_api()
    got, _, out = call(api, "POST", "/calls", {"source": source})
    assert (got, out["error"]["code"]) == (status, code) and jobs == []


def test_oversized_bodies_are_still_refused(make_api):
    api, _ = make_api()
    raw = json.dumps({"source": {"type": "transcript", "text": "a" * (300 * 1024)}}).encode()
    status, _, payload = api.handle(Request("POST", "/calls", {"x-access-key": KEY}, {}, raw)).encoded()
    assert status == 413 and json.loads(payload)["error"]["code"] == "body_too_large"
    big = json.dumps({"title": "t" * (70 * 1024)}).encode()  # other routes keep the 64 KB limit
    assert api.handle(Request("POST", "/accounts", {"x-access-key": KEY}, {}, big)).status == 413
