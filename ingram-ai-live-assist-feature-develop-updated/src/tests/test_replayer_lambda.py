import json
from types import SimpleNamespace

import boto3
import pytest

import replayer.lambda_handler as lh
from tests.test_transcribe_source import FakeTranscriber

SID = "call-0001-abcdefghijklmnopqrstuvwxyz0123"
CONVERSATION = {"conversation": [
    {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "text": "Hello."},
    {"timestamp": "2026-10-08T10:00:20Z", "speaker": "cx", "text": "Hi."},
    {"timestamp": "2026-10-08T10:00:40Z", "speaker": "cx", "text": "On-prem."},
]}


class FakeSender:
    def __init__(self, reject=None):
        self.events, self.reject = [], reject

    def send(self, event):
        self.events.append(event)
        if event["type"] == "turn" and self.reject:
            return {"accepted": False, "code": self.reject, "error": "no"}
        return {"accepted": True, "seq": len(self.events)}

    def kinds(self):
        return [e["type"] for e in self.events]


@pytest.fixture
def s3(dynamodb_resource):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    client.put_object(Bucket="test-bucket", Key="uploads/recording/1/conversation.json", Body=json.dumps(CONVERSATION).encode())
    return client


def _recording_job(**kw):
    return {"mode": "recording", "session_id": SID, "bucket": "test-bucket",
            "key": "uploads/recording/1/conversation.json", "speed": 1000, "send_start": False, **kw}


def test_recording_job_from_s3(s3):
    sender = FakeSender()
    result, continuation = lh.run_job(_recording_job(), sender=sender, s3=s3)
    assert result["stopped"] == "finished" and continuation is None
    assert sender.kinds() == ["turn", "turn", "turn", "session.end"]  # API sent session.start.


def test_recording_job_continues_near_deadline(s3):
    clock = {"t": 0.0}
    result, continuation = lh.run_job(_recording_job(speed=1), sender=FakeSender(), s3=s3,
                                      deadline=5.0, clock=lambda: clock["t"])
    assert result["stopped"] == "deadline" and result["turns_sent"] == 1
    assert continuation == {"start_index": 1, "send_start": False}


def test_no_progress_fails_and_ends_the_call(s3):
    sender = FakeSender()
    result, continuation = lh.run_safely(_recording_job(speed=1, start_index=1), sender=sender, s3=s3,
                                         deadline=1.0, clock=lambda: 0.0)
    assert result["stopped"] == "error" and continuation is None
    assert sender.events[-1] == {"schema": "ila.event.v1", "type": "session.end", "session_id": SID,
                                 "reason": "source failed: ReplayError"}


def test_rejected_turn_ends_the_call(s3):
    sender = FakeSender(reject="invalid_event")
    result, _ = lh.run_safely(_recording_job(), sender=sender, s3=s3)
    assert result["stopped"] == "error" and sender.kinds()[-1] == "session.end"


def test_audio_job_from_s3(s3):
    prefix = "uploads/audio/1/"
    manifest = {"audio": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}, "duration_sec": 0.5,
                "speakers": [{"speaker_id": "ravi", "role": "ba", "pcm": "ravi.pcm"}]}
    s3.put_object(Bucket="test-bucket", Key=prefix + "manifest.json", Body=json.dumps(manifest).encode())
    s3.put_object(Bucket="test-bucket", Key=prefix + "ravi.pcm", Body=b"\x01\x00" * 8000)
    FakeTranscriber.script = {b"\x01\x00": [(2, 0.0, 0.2, "Hello from audio.")]}

    sender = FakeSender()
    result, continuation = lh.run_job(
        {"mode": "audio", "session_id": SID, "bucket": "test-bucket", "prefix": prefix, "speed": 2, "send_start": False},
        sender=sender, s3=s3, transcriber_factory=FakeTranscriber,
    )
    assert result["stopped"] == "finished" and continuation is None
    turn = next(e for e in sender.events if e["type"] == "turn")
    assert turn["text"] == "Hello from audio." and turn["speaker"] == "ba" and turn["source"] == "transcribe"


def test_handler_reinvokes_itself_with_progress(monkeypatch):
    invoked = []
    monkeypatch.setattr(lh, "run_safely", lambda job, **kw: ({"stopped": "deadline", "turns_sent": 3},
                                                              {"start_index": 3, "send_start": False}))
    monkeypatch.setattr("replayer.clients.RuntimeSender", lambda *a, **k: FakeSender())
    real_client = boto3.client

    def client(service, *a, **k):
        if service == "lambda":
            return SimpleNamespace(invoke=lambda **kw: invoked.append(kw))
        return real_client(service, *a, region_name="us-east-1")

    monkeypatch.setattr(boto3, "client", client)
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 900_000,
                              invoked_function_arn="arn:aws:lambda:us-east-1:1:function:replayer")
    lh.handler({"mode": "recording", "session_id": SID, "runtime_arn": "arn:x", "bucket": "test-bucket", "key": "k"}, context)

    payload = json.loads(invoked[0]["Payload"])
    assert invoked[0]["InvocationType"] == "Event"
    assert payload["start_index"] == 3 and payload["send_start"] is False and payload["session_id"] == SID
