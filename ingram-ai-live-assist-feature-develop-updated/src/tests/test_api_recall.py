"""Recall.ai meeting source: bot creation, signed webhooks, and the call ending.

Real API handlers and entry point with moto DynamoDB. Recall itself is a fake HTTP function, and the
webhooks are signed the way Recall signs them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config

import main
from api.app import Api, Request, Services, _recall_from_env
from api.recall import (
    END_GRACE_SECONDS,
    SOURCE,
    RecallSettings,
    RecallSource,
    verify_signature,
)
from tests.loop_sender import LoopSender
from tests.test_api import KEY, call

TEAMS_URL = "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34"
PUBLIC_URL = "https://api.example.com"
API_KEY = "recall-api-key-value"
SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
NOW = 1_800_000_000.0


def sign(body: bytes, *, secret: str = SECRET, ts: float = NOW, msg_id: str = "msg_1", prefix: str = "webhook") -> dict:
    stamp = str(int(ts))
    digest = hmac.new(base64.b64decode(secret[len("whsec_"):]), f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256)
    return {f"{prefix}-id": msg_id, f"{prefix}-timestamp": stamp,
            f"{prefix}-signature": "v1," + base64.b64encode(digest.digest()).decode()}


class FakeRecall:
    """Stands in for Recall's REST API. Remembers the metadata given to a bot, like Recall does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.create = (201, {"id": "bot-1"})
        self.leave = (200, {})
        self.get_status = 200
        self.metadata: dict = {}

    def __call__(self, method, url, headers, body):
        payload = json.loads(body) if body else None
        self.calls.append((method, url, headers, payload))
        if method == "POST" and url.endswith("/bot/"):
            status, out = self.create
            if status < 300:
                self.metadata = payload["metadata"]
        elif method == "POST" and url.endswith("/leave_call/"):
            status, out = self.leave
        elif method == "GET":
            status, out = self.get_status, {"id": "bot-1", "metadata": self.metadata}
        else:  # pragma: no cover
            raise AssertionError(f"unexpected call {method} {url}")
        return status, json.dumps(out).encode()

    def urls(self, method: str) -> list[str]:
        return [u for m, u, _, _ in self.calls if m == method]


class BrokenSender:
    def send(self, event):
        raise ConnectionError("runtime down")


@pytest.fixture
def recall_http():
    return FakeRecall()


@pytest.fixture
def make_api(timeline, session_store, dynamodb_resource, monkeypatch, recall_http):
    s3 = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
    s3.create_bucket(Bucket="test-uploads")
    sender = LoopSender()
    sleeps: list[float] = []
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    prepared: list[str] = []
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(
        schedule=lambda _sid, **_kw: None, prepare_questions=prepared.append))

    def settings(**over):
        base = {"region": "us-west-2", "public_url": PUBLIC_URL,
                "api_key": lambda: API_KEY, "webhook_secret": lambda: SECRET}
        return RecallSettings(**{**base, **over})

    def build(*, recall="on", recall_sender=None, provider="recall", **settings_over):
        source = None
        if recall == "on":
            source = RecallSource(settings(**settings_over), sender=recall_sender or sender, http=recall_http,
                                  clock=lambda: NOW, sleep=sleeps.append)
        api = Api(Services(
            timeline=timeline, store=session_store, sender=sender, s3=s3, bucket="test-uploads",
            start_source=lambda job: None, access_key=lambda: KEY, meeting_provider=provider, recall=source))
        return api

    build.sender, build.sleeps, build.prepared = sender, sleeps, prepared
    yield build
    sender.close()


def start_call(api, **source) -> str:
    status, _, out = call(api, "POST", "/calls", {
        "source": {"type": "meeting", "meeting_url": TEAMS_URL, "ba_names": "Ravi Menon", **source}})
    assert status == 201, out
    return out["session_id"]


def utterance(session_id, *, words=("We", "run", "on-prem."), name="Alice", participant=2, bot="bot-1",
              ba_names="Ravi Menon", start=12.5, event="transcript.data"):
    return {"event": event, "data": {
        "data": {
            "words": [{"text": w, "start_timestamp": {"relative": start + i * 0.3},
                       "end_timestamp": {"relative": start + i * 0.3 + 0.2}} for i, w in enumerate(words)],
            "language_code": "en",
            "participant": {"id": participant, "name": name, "is_host": False, "platform": "desktop"},
        },
        "realtime_endpoint": {"id": "ep", "metadata": {}}, "transcript": {"id": "t", "metadata": {}},
        "recording": {"id": "r", "metadata": {}},
        "bot": {"id": bot, "metadata": {"session_id": session_id, "ba_names": ba_names}},
    }}


def status_event(code, *, bot="bot-1", sub_code=None):
    return {"event": "bot.status_change", "data": {"bot_id": bot, "status": {
        "code": code, "created_at": "2027-01-15T08:00:00Z", "sub_code": sub_code, "message": None}}}


def post(api, path, payload, *, headers=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    h = sign(body) if headers is None else headers
    status, _, out = api.handle(Request("POST", path, h, {}, body)).encoded()
    return status, (json.loads(out) if out else None)


def turns(timeline, session_id):
    return timeline.pending_turns(session_id, 0, 50)


# --- signature -----------------------------------------------------------------------------------


def test_known_signature_from_the_svix_documentation():
    # The example in Svix's docs, which Recall's signatures follow.
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    headers = {"webhook-id": "msg_p5jXN8AQM9LWM0D4loKWxJek", "webhook-timestamp": "1614265330",
               "webhook-signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="}
    body = b'{"test": 2432232314}'
    assert verify_signature(secret, headers, body, now=1614265330) is True
    assert verify_signature(secret, headers, body + b" ", now=1614265330) is False


def test_signature_accepts_both_header_sets_and_rotated_secrets():
    body = b'{"a": 1}'
    assert verify_signature(SECRET, sign(body), body, now=NOW)
    assert verify_signature(SECRET, sign(body, prefix="svix"), body, now=NOW)
    h = sign(body)
    h["webhook-signature"] = "v1,AAAA " + h["webhook-signature"]  # an older secret's signature comes first
    assert verify_signature(SECRET, h, body, now=NOW)


@pytest.mark.parametrize("change", ["secret", "body", "old", "future", "headers", "format"])
def test_signature_refuses_bad_requests(change):
    body = b'{"a": 1}'
    secret, headers, ts = SECRET, sign(body), NOW
    if change == "secret":
        secret = "whsec_" + base64.b64encode(b"another-secret-another-secret-00").decode()
    elif change == "body":
        body = b'{"a": 2}'
    elif change == "old":
        ts = NOW + 301
    elif change == "future":
        ts = NOW - 301
    elif change == "headers":
        headers = {}
    elif change == "format":
        secret = "not-a-whsec-secret"
    assert verify_signature(secret, headers, body, now=ts) is False


# --- options and settings -------------------------------------------------------------------------


def test_options_say_no_vexa_key_is_needed(make_api):
    meeting = call(make_api(), "GET", "/options")[2]["sources"]["meeting"]
    assert meeting["enabled"] is True and meeting["provider"] == "recall" and meeting["needs_vexa_key"] is False


def test_options_report_a_server_without_recall_settings_as_unavailable(make_api):
    meeting = call(make_api(recall="off"), "GET", "/options")[2]["sources"]["meeting"]
    assert meeting["enabled"] is False and "Recall" in meeting["reason"]


def test_a_misconfigured_server_refuses_the_call_and_closes_it(make_api, timeline):
    api = make_api(recall="off")
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    assert (status, out["error"]["code"]) == (502, "meeting_not_configured")
    assert [m["status"] for m in timeline.list_calls(limit=10)[0]] == ["ended"]


@pytest.mark.parametrize("env, fragment", [
    ({"PUBLIC_API_URL": "http://api.example.com"}, "https"),
    ({"PUBLIC_API_URL": PUBLIC_URL, "RECALL_REGION": "evil.example.com/x"}, "RECALL_REGION"),
    ({"PUBLIC_API_URL": PUBLIC_URL, "RECALL_STREAMING_MODE": "fast"}, "RECALL_STREAMING_MODE"),
    ({"PUBLIC_API_URL": PUBLIC_URL, "RECALL_LANGUAGE_CODE": "fr"}, "'en'"),
])
def test_settings_are_checked(env, fragment):
    with pytest.raises(ValueError, match=fragment):
        RecallSettings.from_env(env, api_key=lambda: "k", webhook_secret=lambda: "s")


def test_the_api_starts_without_recall_when_its_settings_are_missing(caplog):
    caplog.set_level(logging.ERROR)
    assert _recall_from_env({"PUBLIC_API_URL": PUBLIC_URL}, "us-east-1", object()) is None
    assert _recall_from_env({"RECALL_API_KEY": "k", "RECALL_VERIFY_SECRET": SECRET, "PUBLIC_API_URL": "x"},
                            "us-east-1", object()) is None
    ok = _recall_from_env({"RECALL_API_KEY": "k", "RECALL_VERIFY_SECRET": SECRET, "PUBLIC_API_URL": PUBLIC_URL},
                          "us-east-1", object())
    assert isinstance(ok, RecallSource)


def test_the_turn_source_matches_the_one_the_runtime_uses():
    from app.meeting_source import SOURCE as RUNTIME_SOURCE

    assert SOURCE == RUNTIME_SOURCE


# --- starting a call ----------------------------------------------------------------------------------


def test_a_meeting_call_makes_the_api_send_the_bot(make_api, recall_http, timeline):
    api = make_api()
    sid = start_call(api, bot_name="Ingram <b>Assist</b>")
    assert [e["type"] for e in make_api.sender.events] == ["session.start"]  # no meeting.join: the runtime holds nothing
    method, url, headers, body = recall_http.calls[0]
    assert (method, url) == ("POST", "https://us-west-2.recall.ai/api/v1/bot/")
    assert headers["Authorization"] == API_KEY
    assert body["meeting_url"] == TEAMS_URL and body["bot_name"] == "Ingram bAssistb"
    assert body["metadata"] == {"session_id": sid, "ba_names": "Ravi Menon"}
    config = body["recording_config"]
    assert config["transcript"]["provider"] == {"recallai_streaming": {
        "mode": "prioritize_low_latency", "language_code": "en"}}
    assert config["transcript"]["diarization"] == {"use_separate_streams_when_available": True}
    assert config["realtime_endpoints"] == [{
        "type": "webhook", "url": f"{PUBLIC_URL}/recall/transcript", "events": ["transcript.data"]}]
    meta = timeline.get_meta(sid)
    assert meta["source"] == "meeting" and meta["exclusive_source"] == "meeting"


def test_vexa_keys_are_not_needed_and_are_never_forwarded(make_api, recall_http):
    api = make_api()
    start_call(api, vexa_api_key="vxa_should_be_ignored_123")
    assert "vxa_should_be_ignored_123" not in json.dumps(recall_http.calls)
    assert "vxa_should_be_ignored_123" not in json.dumps(make_api.sender.events)


def test_an_unsupported_link_is_refused_before_anything_is_created(make_api, recall_http, timeline):
    status, _, out = call(make_api(), "POST", "/calls", {"source": {
        "type": "meeting", "meeting_url": "https://evil.example.com/x"}})
    assert (status, out["error"]["code"]) == (400, "invalid_meeting_link")
    assert recall_http.calls == [] and timeline.list_calls(limit=10)[0] == []


@pytest.mark.parametrize("code, http, api_code", [
    (401, 502, "meeting_not_authorized"), (403, 502, "meeting_not_authorized"), (429, 429, "meeting_limit"),
    (400, 400, "invalid_meeting_link"), (507, 502, "meeting_unavailable"), (500, 502, "meeting_unavailable"),
])
def test_bot_failures_map_to_clear_errors_and_close_the_call(make_api, recall_http, timeline, code, http, api_code):
    recall_http.create = (code, {"detail": "no"})
    status, _, out = call(make_api(), "POST", "/calls", {"source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    assert (status, out["error"]["code"]) == (http, api_code)
    assert API_KEY not in json.dumps(out)
    assert [m["status"] for m in timeline.list_calls(limit=10)[0]] == ["ended"]


def test_a_missing_api_key_is_reported_as_not_configured(make_api, timeline):
    api = make_api(api_key=lambda: None)
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    assert (status, out["error"]["code"]) == (502, "meeting_not_configured")


# --- meeting title, description and pre-questions ------------------------------------------------------------------


def start_event_of(api_factory):
    return api_factory.sender.events[0]


def test_title_and_description_go_to_the_agent_and_ask_for_pre_questions(make_api, timeline):
    api = make_api()
    status, _, out = call(api, "POST", "/calls", {
        "title": "Acme data centre migration", "description": "  Lease ends in May.\r\nAbout 120 VMs.  ",
        "source": {"type": "meeting", "meeting_url": TEAMS_URL, "ba_names": "Ravi Menon"}})
    assert status == 201 and out["title"] == "Acme data centre migration"
    event = start_event_of(make_api)
    assert event["type"] == "session.start" and event["title"] == "Acme data centre migration"
    assert event["description"] == "Lease ends in May.\nAbout 120 VMs." and event["prepare_questions"] is True
    assert make_api.prepared == [out["session_id"]]
    assert timeline.get_meta(out["session_id"])["description"] == "Lease ends in May.\nAbout 120 VMs."


def test_a_title_alone_is_enough_to_ask_for_pre_questions(make_api):
    call(make_api(), "POST", "/calls", {"title": "Acme", "source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    event = start_event_of(make_api)
    assert event["prepare_questions"] is True and "description" not in event


def test_a_description_alone_is_enough_too(make_api):
    call(make_api(), "POST", "/calls", {"description": "Agenda", "source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    event = start_event_of(make_api)
    assert event["prepare_questions"] is True and event["title"] == "Live meeting"


def test_without_title_or_description_no_pre_questions_are_asked_for(make_api):
    call(make_api(), "POST", "/calls", {"source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    event = start_event_of(make_api)
    assert "prepare_questions" not in event and "description" not in event and make_api.prepared == []


def test_other_sources_do_not_ask_for_pre_questions(make_api):
    call(make_api(), "POST", "/calls", {"title": "Notes", "description": "Agenda", "source": {
        "type": "transcript", "text": "Ravi Menon: hi\nAlice: hello", "ba_names": "Ravi Menon"}})
    assert "prepare_questions" not in start_event_of(make_api) and make_api.prepared == []


@pytest.mark.parametrize("description", [5, ["a"], "x" * 2001])
def test_a_bad_description_is_refused_before_anything_is_created(make_api, recall_http, timeline, description):
    status, _, out = call(make_api(), "POST", "/calls", {
        "description": description, "source": {"type": "meeting", "meeting_url": TEAMS_URL}})
    assert (status, out["error"]["code"]) == (400, "invalid_request") and "description" in out["error"]["message"]
    assert recall_http.calls == [] and timeline.list_calls(limit=10)[0] == []


def test_the_call_view_carries_the_description_and_the_pre_question_status(make_api):
    api = make_api()
    sid = call(api, "POST", "/calls", {"title": "Acme", "description": "Agenda", "source": {
        "type": "meeting", "meeting_url": TEAMS_URL}})[2]["session_id"]
    view = call(api, "GET", f"/calls/{sid}")[2]
    assert view["description"] == "Agenda" and view["prequestions"] == {"status": "none"}  # the job is faked here


# --- transcript webhook -------------------------------------------------------------------------------------


def test_an_utterance_becomes_a_turn_with_the_right_role(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/transcript", utterance(sid, name="Alice")) == (200, {"accepted": True})
    assert post(api, "/recall/transcript", utterance(
        sid, name="Ravi Menon (Partner)", words=("Tell", "me", "more."), participant=1, start=20.0))[0] == 200
    first, second = turns(timeline, sid)
    assert (first["seq"], first["speaker"], first["speaker_name"], first["text"]) == (1, "cx", "Alice", "We run on-prem.")
    assert (second["speaker"], second["speaker_name"], second["text"]) == ("ba", "Ravi Menon (Partner)", "Tell me more.")
    assert first["source"] == "meeting" and first["event_id"] == "recall-bot-1-2-12500"


def test_a_speaker_without_a_name_is_the_customer(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/transcript", utterance(sid, name=None))[0] == 200
    turn = turns(timeline, sid)[0]
    assert (turn["speaker"], turn["speaker_name"]) == ("cx", "Unknown")


def test_the_route_needs_no_access_key_but_other_routes_still_do(make_api):
    api = make_api()
    assert call(api, "GET", "/options", key=None)[0] == 401
    sid = start_call(api)
    assert post(api, "/recall/transcript", utterance(sid))[0] == 200  # no x-access-key header


@pytest.mark.parametrize("how", ["unsigned", "wrong_secret", "tampered", "stale"])
def test_unsigned_or_forged_webhooks_are_refused(make_api, timeline, how):
    api = make_api()
    sid = start_call(api)
    raw = json.dumps(utterance(sid)).encode()
    headers = {
        "unsigned": {},
        "wrong_secret": sign(raw, secret="whsec_" + base64.b64encode(b"x" * 32).decode()),
        "tampered": sign(raw + b" "),
        "stale": sign(raw, ts=NOW - 3600),
    }[how]
    status, out = post(api, "/recall/transcript", None, headers=headers, raw=raw)
    assert (status, out["error"]["code"]) == (401, "invalid_signature")
    assert turns(timeline, sid) == []


def test_a_missing_verification_secret_refuses_everything(make_api):
    api = make_api(webhook_secret=lambda: None)
    sid = start_call(api)
    status, out = post(api, "/recall/transcript", utterance(sid))
    assert (status, out["error"]["code"]) == (500, "recall_not_configured")


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(event="participant_events.join"),
    lambda e: e["data"]["data"].update(words=[]),
    lambda e: e["data"]["data"].update(words=[{"text": "  "}]),
    lambda e: e["data"]["bot"].update(metadata={}),
    lambda e: e["data"]["bot"]["metadata"].update(session_id="short"),
    lambda e: e["data"]["bot"].update(id=None),
])
def test_events_with_nothing_to_use_are_acknowledged_and_ignored(make_api, timeline, mutate):
    api = make_api()
    sid = start_call(api)
    event = utterance(sid)
    mutate(event)
    assert post(api, "/recall/transcript", event) == (200, {"ignored": True})
    assert turns(timeline, sid) == []


def test_a_turn_for_an_unknown_call_is_dropped_without_a_retry(make_api):
    api = make_api()
    assert post(api, "/recall/transcript", utterance("u" * 40)) == (200, {"accepted": False, "code": "unknown_session"})


def test_a_turn_after_the_call_ended_makes_the_bot_leave(make_api, recall_http, timeline):
    api = make_api()
    sid = start_call(api)
    assert call(api, "POST", f"/calls/{sid}/end", {})[0] == 200
    assert post(api, "/recall/transcript", utterance(sid)) == (200, {"accepted": False, "code": "session_ended"})
    assert recall_http.urls("POST")[-1] == "https://us-west-2.recall.ai/api/v1/bot/bot-1/leave_call/"
    assert turns(timeline, sid) == []


def test_a_bot_that_cannot_be_removed_does_not_fail_the_webhook(make_api, recall_http):
    api = make_api()
    sid = start_call(api)
    call(api, "POST", f"/calls/{sid}/end", {})
    recall_http.leave = (500, {"detail": "boom"})
    assert post(api, "/recall/transcript", utterance(sid))[0] == 200


def test_an_unreachable_agent_is_a_server_error_so_recall_resends(make_api):
    api = make_api(recall_sender=BrokenSender())
    sid = start_call(make_api())  # the call itself must exist; use a working api to create it
    status, out = post(api, "/recall/transcript", utterance(sid))
    assert (status, out["error"]["code"]) == (502, "agent_unavailable")


# --- status webhook -----------------------------------------------------------------------------------------------


def test_the_call_ends_when_the_meeting_ends(make_api, recall_http, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", status_event("call_ended"))[0] == 200
    meta = timeline.get_meta(sid)
    assert meta["status"] == "ended" and meta["end_reason"] == "meeting ended"
    assert recall_http.urls("GET") == ["https://us-west-2.recall.ai/api/v1/bot/bot-1/"]
    assert make_api.sleeps == [END_GRACE_SECONDS]  # the last sentences get a moment to arrive


def test_a_failed_bot_ends_the_call_with_its_reason(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", status_event("fatal", sub_code="Meeting_Not_Found!"))[0] == 200
    meta = timeline.get_meta(sid)
    assert meta["status"] == "ended" and meta["end_reason"] == "the meeting bot failed (meeting_not_found)"
    assert make_api.sleeps == []


def per_status_event(name, session_id, *, sub_code=None, bot="bot-1", with_metadata=True):
    """The newer webhook shape: one event per status, with the bot and its metadata attached."""
    bot_part = {"id": bot, "metadata": {"session_id": session_id, "ba_names": "Ravi Menon"} if with_metadata else {}}
    return {"event": name, "data": {"data": {"code": name.split(".")[1], "sub_code": sub_code,
                                             "updated_at": "2027-01-15T08:00:00Z"}, "bot": bot_part}}


def test_the_newer_call_ended_event_ends_the_call_without_asking_recall(make_api, recall_http, timeline):
    api = make_api()
    sid = start_call(api)
    calls_before = len(recall_http.calls)
    assert post(api, "/recall/status", per_status_event("bot.call_ended", sid))[0] == 200
    meta = timeline.get_meta(sid)
    assert meta["status"] == "ended" and meta["end_reason"] == "meeting ended"
    assert len(recall_http.calls) == calls_before  # the metadata came with the event
    assert make_api.sleeps == [END_GRACE_SECONDS]


def test_the_newer_fatal_event_ends_the_call_with_its_reason(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", per_status_event("bot.fatal", sid, sub_code="Bot_Kicked!"))[0] == 200
    assert timeline.get_meta(sid)["end_reason"] == "the meeting bot failed (bot_kicked)"


def test_the_newer_event_without_metadata_falls_back_to_asking_recall(make_api, recall_http, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", per_status_event("bot.call_ended", sid, with_metadata=False))[0] == 200
    assert timeline.get_meta(sid)["status"] == "ended"
    assert recall_http.urls("GET") == ["https://us-west-2.recall.ai/api/v1/bot/bot-1/"]


@pytest.mark.parametrize("name", ["bot.done", "bot.joining_call", "bot.in_call_recording", "audio_mixed.done"])
def test_other_newer_events_do_nothing(make_api, timeline, name):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", per_status_event(name, sid)) == (200, {"ignored": True})
    assert timeline.get_meta(sid)["status"] == "live"


@pytest.mark.parametrize("code", ["joining_call", "in_waiting_room", "in_call_recording", "done"])
def test_other_status_changes_do_nothing(make_api, recall_http, timeline, code):
    api = make_api()
    sid = start_call(api)
    calls_before = len(recall_http.calls)
    assert post(api, "/recall/status", status_event(code)) == (200, {"ignored": True})
    assert len(recall_http.calls) == calls_before and timeline.get_meta(sid)["status"] == "live"


def test_a_repeated_end_is_harmless(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    assert post(api, "/recall/status", status_event("call_ended"))[0] == 200
    assert post(api, "/recall/status", status_event("fatal"))[0] == 200
    assert timeline.get_meta(sid)["end_reason"] == "meeting ended"


def test_a_bot_recall_does_not_know_is_ignored(make_api, recall_http):
    api = make_api()
    start_call(api)
    recall_http.get_status = 404
    assert post(api, "/recall/status", status_event("call_ended")) == (200, {"ignored": True})


def test_a_bot_without_our_metadata_is_ignored(make_api, recall_http):
    api = make_api()
    start_call(api)
    recall_http.metadata = {"something": "else"}
    assert post(api, "/recall/status", status_event("call_ended")) == (200, {"ignored": True})


def test_status_webhooks_are_verified_too(make_api, timeline):
    api = make_api()
    sid = start_call(api)
    status, _ = post(api, "/recall/status", status_event("call_ended"), headers={})
    assert status == 401 and timeline.get_meta(sid)["status"] == "live"


def test_neither_the_api_key_nor_the_secret_is_logged(make_api, caplog):
    caplog.set_level(logging.DEBUG)
    api = make_api()
    sid = start_call(api)
    post(api, "/recall/transcript", utterance(sid))
    post(api, "/recall/transcript", utterance(sid), headers={})
    post(api, "/recall/status", status_event("call_ended"))
    assert API_KEY not in caplog.text and SECRET not in caplog.text

