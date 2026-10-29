"""API tests: real handlers and entry point, moto DynamoDB and S3, no agent runs."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config

import main
from api import handler as lambda_api
from api.app import Api, Request, Services

KEY = "test-access-key"
BUCKET = "test-uploads"
CONVERSATION = {"conversation": [
    {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "text": "Hello."},
    {"timestamp": "2026-10-08T10:00:05Z", "speaker": "cx", "text": "We run on-prem."},
]}


class EntryPointSender:
    """Delivers events through main.agent_invocation, like the runtime would."""

    def send(self, event: dict) -> dict:
        return asyncio.run(main.agent_invocation(event, SimpleNamespace(session_id=event["session_id"])))


class BrokenSender:
    def send(self, event: dict) -> dict:
        raise ConnectionError("runtime down")


@pytest.fixture
def s3(dynamodb_resource):
    client = boto3.client("s3", region_name="us-east-1", config=Config(signature_version="s3v4"))
    client.create_bucket(Bucket=BUCKET)
    return client


@pytest.fixture
def jobs():
    return []


@pytest.fixture
def api(timeline, session_store, s3, jobs, monkeypatch):
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(schedule=lambda _sid, **_kw: None))
    services = Services(
        timeline=timeline, store=session_store, sender=EntryPointSender(), s3=s3, bucket=BUCKET,
        start_source=jobs.append, access_key=lambda: KEY,
    )
    return Api(services)


def call(api, method, path, body=None, headers=None, key=KEY, query=None):
    h = {"x-access-key": key} if key else {}
    h.update(headers or {})
    raw = json.dumps(body).encode() if body is not None else b""
    status, out_headers, payload = api.handle(Request(method, path, h, query or {}, raw)).encoded()
    return status, out_headers, (json.loads(payload) if payload else None)


def _start(api, **body):
    status, _, out = call(api, "POST", "/calls", body)
    assert status == 201, out
    return out["session_id"]


# --- auth and meta -----------------------------------------------------------------

def test_health_needs_no_key(api):
    assert call(api, "GET", "/health", key=None)[2] == {"status": "ok"}


@pytest.mark.parametrize("path", ["/options", "/calls", "/calls/" + "x" * 40 + "/end", "/nope"])
def test_cors_preflight_is_answered_without_a_key(api, path):
    # The Amplify console calls the API from another origin, so the browser sends OPTIONS first and
    # refuses to continue unless it gets a 2xx.
    status, _, out = call(api, "OPTIONS", path, key=None, headers={
        "origin": "https://main.example.amplifyapp.com", "access-control-request-method": "POST"})
    assert status == 204 and out is None


@pytest.mark.parametrize("key", [None, "wrong"])
def test_missing_or_wrong_key_rejected(api, key):
    status, _, out = call(api, "GET", "/options", key=key)
    assert status == 401 and out["error"]["code"] == "unauthorized"


def test_options_lists_topics_and_limits(api):
    status, _, out = call(api, "GET", "/options")
    assert status == 200
    assert "business_goal" in {t["field_id"] for t in out["topics"]}
    assert out["sources"]["audio"]["speed"]["max"] == 2.0
    assert out["assist_config"]["defaults"]["scope"] == "checklist"


@pytest.mark.parametrize("method, path, status, code", [
    ("GET", "/nope", 404, "not_found"),
    ("DELETE", "/calls", 405, "method_not_allowed"),
    ("GET", "/calls/short", 404, "call_not_found"),
    ("GET", "/calls/" + "x" * 40, 404, "call_not_found"),
])
def test_routing_errors(api, method, path, status, code):
    got_status, _, out = call(api, method, path)
    assert got_status == status and out["error"]["code"] == code


def test_invalid_json_rejected(api):
    status, _, payload = api.handle(Request("POST", "/calls", {"x-access-key": KEY}, {}, b"{nope")).encoded()
    assert status == 400 and json.loads(payload)["error"]["code"] == "invalid_json"
    status, _, out = call(api, "POST", "/calls", body=[1, 2])
    assert status == 400 and out["error"]["code"] == "invalid_json"


# --- manual call --------------------------------------------------------------------

def test_manual_call_flow(api):
    sid = _start(api, title="Demo", assist_config={"scope": "guided", "max_suggestions": 2})

    status, headers, view = call(api, "GET", f"/calls/{sid}")
    assert status == 200 and view["title"] == "Demo" and view["accepts_manual_turns"] is True
    assert view["assist_config"]["scope"] == "guided"
    etag = headers["ETag"]
    assert call(api, "GET", f"/calls/{sid}", headers={"if-none-match": etag})[0] == 304

    status, _, out = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "cx", "text": "We run on-prem."})
    assert status == 202 and out["seq"] == 1
    status, headers, view = call(api, "GET", f"/calls/{sid}", headers={"if-none-match": etag})
    assert status == 200 and headers["ETag"] != etag
    assert view["turns"][0]["text"] == "We run on-prem." and view["turns"][0]["speaker_name"] == "Customer"

    status, _, out = call(api, "PATCH", f"/calls/{sid}", {"status": "paused", "assist_config": {"scope": "open"}})
    assert status == 200 and out["status"] == "paused" and out["assist_config"]["scope"] == "open"

    status, _, out = call(api, "PATCH", f"/calls/{sid}", {"assist_config": {"scope": "everything"}})
    assert status == 400 and out["error"]["code"] == "invalid_event"

    status, _, out = call(api, "POST", f"/calls/{sid}/end", {})
    assert status == 200 and out == {"status": "ended", "ended_now": True}

    status, _, out = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "cx", "text": "late"})
    assert status == 409 and out["error"]["code"] == "session_ended"

    status, _, handoff = call(api, "GET", f"/calls/{sid}/handoff")
    assert status == 200 and handoff["session_id"] == sid and handoff["status"] == "ended"


@pytest.mark.parametrize("body", [
    {"title": "   "},
    {"title": "x" * 201},
    {"assist_config": {"scope": "everything"}},
    {"source": {"type": "zoom"}},
])
def test_create_call_validation(api, body):
    status, _, out = call(api, "POST", "/calls", body)
    assert status == 400 and out["error"]["code"] == "invalid_request"


def test_bad_turn_rejected(api):
    sid = _start(api)
    status, _, out = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "boss", "text": "hi"})
    assert status == 400 and out["error"]["code"] == "invalid_event"


def test_unknown_call(api):
    sid = "call-" + "0" * 32
    assert call(api, "GET", f"/calls/{sid}")[0] == 404
    status, _, out = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "cx", "text": "hi"})
    assert status == 404 and out["error"]["code"] == "unknown_session"


def test_list_calls_pages(api):
    ids = {_start(api, title=f"Call {i}") for i in range(3)}
    status, _, first = call(api, "GET", "/calls", query={"limit": "2"})
    assert status == 200 and len(first["calls"]) == 2 and first["next_cursor"]
    _, _, second = call(api, "GET", "/calls", query={"limit": "2", "cursor": first["next_cursor"]})
    seen = [c["session_id"] for c in first["calls"] + second["calls"]]
    assert set(seen) == ids and len(seen) == 3
    assert call(api, "GET", "/calls", query={"limit": "0"})[0] == 400
    assert call(api, "GET", "/calls", query={"cursor": "%%%"})[0] == 400


def test_agent_unavailable(api):
    api.s.sender = BrokenSender()
    status, _, out = call(api, "POST", "/calls", {})
    assert status == 502 and out["error"]["code"] == "agent_unavailable"


# --- uploads and sources ------------------------------------------------------------

def test_recording_upload_url(api):
    status, _, out = call(api, "POST", "/uploads", {"kind": "recording"})
    assert status == 201 and len(out["upload_id"]) == 32 and out["files"][0]["path"] == "conversation.json"
    url = out["files"][0]["url"]
    assert f"uploads/recording/{out['upload_id']}/conversation.json" in url and "X-Amz-Signature" in url


@pytest.mark.parametrize("body", [
    {"kind": "video"},
    {"kind": "audio"},
    {"kind": "audio", "files": ["speakers/a.pcm"]},
    {"kind": "audio", "files": ["manifest.json", "../etc/passwd"]},
    {"kind": "audio", "files": ["manifest.json", "manifest.json"]},
])
def test_upload_validation(api, body):
    assert call(api, "POST", "/uploads", body)[0] == 400


def _upload_recording(api, s3, content=CONVERSATION):
    _, _, out = call(api, "POST", "/uploads", {"kind": "recording"})
    body = content if isinstance(content, bytes) else json.dumps(content).encode()
    s3.put_object(Bucket=BUCKET, Key=f"uploads/recording/{out['upload_id']}/conversation.json", Body=body)
    return out["upload_id"]


def test_recording_call_starts_source_and_locks_turns(api, s3, jobs):
    upload_id = _upload_recording(api, s3)
    sid = _start(api, title="Rec", source={"type": "recording", "upload_id": upload_id, "speed": 20})

    assert jobs == [{
        "mode": "recording", "bucket": BUCKET, "key": f"uploads/recording/{upload_id}/conversation.json",
        "speed": 20.0, "max_gap_seconds": 8.0, "session_id": sid, "title": "Rec",
        "assist_config": jobs[0]["assist_config"], "send_start": False,
    }]
    _, _, view = call(api, "GET", f"/calls/{sid}")
    assert view["source"] == "recording" and view["accepts_manual_turns"] is False

    status, _, out = call(api, "POST", f"/calls/{sid}/turns", {"speaker": "ba", "text": "Me too"})
    assert status == 409 and out["error"]["code"] == "source_mismatch"
    ack = EntryPointSender().send({"schema": "ila.event.v1", "type": "turn", "session_id": sid,
                                   "source": "recording", "speaker": "cx", "text": "From the recording."})
    assert ack["accepted"]


@pytest.mark.parametrize("source, status, code", [
    ({"type": "recording", "upload_id": "f" * 32}, 404, "upload_not_found"),
    ({"type": "recording", "upload_id": "nope"}, 400, "invalid_request"),
    ({"type": "recording", "upload_id": None, "speed": 0}, 400, "invalid_request"),
])
def test_recording_source_errors(api, source, status, code):
    got, _, out = call(api, "POST", "/calls", {"source": source})
    assert got == status and out["error"]["code"] == code


def test_recording_with_invalid_content(api, s3):
    upload_id = _upload_recording(api, s3, b'{"conversation": []}')
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "recording", "upload_id": upload_id}})
    assert status == 400 and out["error"]["code"] == "invalid_upload"


def _upload_audio(api, s3):
    files = ["manifest.json", "speakers/ravi.pcm", "speakers/helen.pcm"]
    _, _, out = call(api, "POST", "/uploads", {"kind": "audio", "files": files})
    prefix = f"uploads/audio/{out['upload_id']}/"
    manifest = {"audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}, "duration_sec": 1.0,
                "speakers": [{"speaker_id": "ravi", "role": "ba", "pcm": "speakers/ravi.pcm"},
                             {"speaker_id": "helen", "role": "cx", "pcm": "speakers/helen.pcm"}]}
    s3.put_object(Bucket=BUCKET, Key=prefix + "manifest.json", Body=json.dumps(manifest).encode())
    for name in ("ravi", "helen"):
        s3.put_object(Bucket=BUCKET, Key=f"{prefix}speakers/{name}.pcm", Body=b"\x00\x00" * 16000)
    return out["upload_id"], prefix


def test_audio_call_validates_manifest_and_speed(api, s3, jobs):
    upload_id, prefix = _upload_audio(api, s3)
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "audio", "upload_id": upload_id, "speed": 3}})
    assert status == 400 and out["error"]["code"] == "invalid_request"

    sid = _start(api, source={"type": "audio", "upload_id": upload_id, "speed": 1.5, "limit_seconds": 60})
    assert jobs[0]["mode"] == "audio" and jobs[0]["prefix"] == prefix and jobs[0]["speed"] == 1.5
    assert jobs[0]["limit_seconds"] == 60.0 and jobs[0]["session_id"] == sid
    _, _, view = call(api, "GET", f"/calls/{sid}")
    assert view["source"] == "transcribe" and view["accepts_manual_turns"] is False


def test_audio_upload_missing_track(api, s3):
    upload_id, prefix = _upload_audio(api, s3)
    s3.delete_object(Bucket=BUCKET, Key=prefix + "speakers/helen.pcm")
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "audio", "upload_id": upload_id}})
    assert status == 400 and out["error"]["code"] == "invalid_upload"


def test_source_start_failure_ends_the_call(api, s3, timeline):
    def fail(job):
        raise RuntimeError("lambda down")

    api.s.start_source = fail
    upload_id = _upload_recording(api, s3)
    status, _, out = call(api, "POST", "/calls", {"source": {"type": "recording", "upload_id": upload_id}})
    assert status == 502 and out["error"]["code"] == "source_start_failed"
    (meta,), _ = timeline.list_calls()
    assert meta["status"] == "ended" and meta["end_reason"] == "source failed to start"


# --- Lambda adapter -----------------------------------------------------------------

def test_lambda_handler_translates_http_api_events(api, monkeypatch):
    monkeypatch.setattr(lambda_api, "_api", api)
    event = {
        "rawPath": "/calls",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"X-Access-Key": KEY},
        "body": base64.b64encode(json.dumps({"title": "From Lambda"}).encode()).decode(),
        "isBase64Encoded": True,
    }
    out = lambda_api.handler(event, None)
    assert out["statusCode"] == 201 and out["headers"]["Content-Type"] == "application/json"
    assert json.loads(out["body"])["title"] == "From Lambda"


# --- minutes -----------------------------------------------------------------------

READY = {
    "status": "ok", "generated_at": "2026-10-15T10:00:00Z", "turn_count": 1,
    "minutes": {
        "summary": "They want to migrate.", "attendees": [], "key_points": [
            {"text": "Runs on-prem.", "citations": [1], "uncited": False}],
        "decisions": [], "action_items": [], "risks": [], "open_questions": [], "next_steps": [],
    },
}


def _ended(api, timeline):
    sid = _start(api)
    assert call(api, "POST", f"/calls/{sid}/turns", {"speaker": "cx", "text": "We run on-prem."})[0] == 202
    assert call(api, "POST", f"/calls/{sid}/end", {})[0] == 200
    return sid


def test_minutes_status_none_for_live_call(api):
    sid = _start(api)
    status, _, out = call(api, "GET", f"/calls/{sid}/minutes")
    assert status == 200 and out["status"] == "none" and out["call_status"] == "live"


def test_ready_minutes_as_json_markdown_and_in_handoff(api, timeline):
    sid = _ended(api, timeline)
    timeline.put_minutes(sid, READY)

    status, _, out = call(api, "GET", f"/calls/{sid}/minutes")
    assert status == 200 and out["minutes"]["key_points"][0]["citations"] == [1]

    raw_status, headers, body = api.handle(
        Request("GET", f"/calls/{sid}/minutes", {"x-access-key": KEY}, {"format": "md"})).encoded()
    assert raw_status == 200 and headers["Content-Type"].startswith("text/markdown")
    assert b"Runs on-prem. [#1]" in body

    handoff = call(api, "GET", f"/calls/{sid}/handoff")[2]
    assert handoff["summary"] == "They want to migrate." and handoff["minutes"]["status"] == "ok"


def test_markdown_before_ready_is_conflict(api, timeline):
    sid = _ended(api, timeline)
    status, _, out = call(api, "GET", f"/calls/{sid}/minutes", query={"format": "md"})
    assert status == 409 and out["error"]["code"] == "minutes_not_ready"


def test_bad_format_rejected(api):
    sid = _start(api)
    assert call(api, "GET", f"/calls/{sid}/minutes", query={"format": "pdf"})[0] == 400


def test_regenerate_needs_ended_call(api, timeline, monkeypatch):
    scheduled = []
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(
        schedule=lambda sid, **kw: scheduled.append((sid, kw))))
    sid = _start(api)

    status, _, out = call(api, "POST", f"/calls/{sid}/minutes")
    assert status == 409 and out["error"]["code"] == "session_live"

    call(api, "POST", f"/calls/{sid}/end", {})
    status, _, out = call(api, "POST", f"/calls/{sid}/minutes")
    assert status == 202 and out["minutes_status"] == "queued"
    assert scheduled[-1] == (sid, {"regenerate_minutes": True})


def test_unknown_call_and_missing_key(api):
    unknown = "x" * 40
    assert call(api, "GET", f"/calls/{unknown}/minutes")[0] == 404
    assert call(api, "POST", f"/calls/{unknown}/minutes")[0] == 404
    assert call(api, "GET", f"/calls/{unknown}/minutes", key=None)[0] == 401
    assert call(api, "POST", f"/calls/{unknown}/minutes", key=None)[0] == 401


def test_options_tell_the_console_how_to_upload(api):
    assert call(api, "GET", "/options")[2]["sources"]["audio"]["upload"] == "proxy"
    api.s.direct_upload = True
    assert call(api, "GET", "/options")[2]["sources"]["audio"]["upload"] == "direct"
