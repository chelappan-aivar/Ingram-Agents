import asyncio
import json

import pytest

from replayer.core import ENDED, FINISHED, ReplayError
from replayer.transcribe import (
    CHUNK_MS,
    InvalidManifestError,
    is_audio_manifest,
    load_manifest,
    stream_call,
)

SID = "call-0001-abcdefghijklmnopqrstuvwxyz0123"
RATE = 16000
CHUNK = RATE * 2 * CHUNK_MS // 1000  # bytes per 100 ms


def _manifest(tmp_path, seconds=1.0, **overrides):
    speakers = []
    for sid, role in (("ravi", "ba"), ("helen", "cx")):
        (tmp_path / f"{sid}.pcm").write_bytes(b"\x01\x00" * int(RATE * seconds))
        speakers.append({"speaker_id": sid, "name": sid.title(), "role": role, "pcm": f"{sid}.pcm"})
    data = {"audio": {"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
            "duration_sec": seconds, "dry_run": False, "speakers": speakers, **overrides}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    return path


class FakeTranscriber:
    """Consumes the audio and emits scripted final segments for its track."""

    script = {}  # track name -> [(after_chunks, start, end, text)]
    chunks_seen = {}

    def __init__(self):
        self.name = None

    async def run(self, audio, sample_rate, on_segment):
        count, pending = 0, None
        async for chunk in audio:
            assert len(chunk) <= CHUNK
            count += 1
            if pending is None:
                # Identify the track by its sample value (each test file is uniform per track).
                pending = list(self.script.get(chunk[:2], []))
                self.name = chunk[:2]
            while pending and pending[0][0] <= count:
                _, start, end, text = pending.pop(0)
                await on_segment(start, end, text, f"r{start}")
        FakeTranscriber.chunks_seen[self.name] = count


class FakeSender:
    def __init__(self, reject_code=None):
        self.events = []
        self.reject_code = reject_code

    def send(self, event):
        self.events.append(event)
        if event["type"] == "turn" and self.reject_code:
            return {"accepted": False, "code": self.reject_code, "error": "no"}
        return {"accepted": True, "seq": sum(e["type"] == "turn" for e in self.events)}


async def _no_sleep(_s):
    await asyncio.sleep(0)


def _two_track_manifest(tmp_path):
    path = _manifest(tmp_path, seconds=1.0)
    (tmp_path / "ravi.pcm").write_bytes(b"\x01\x00" * RATE)
    (tmp_path / "helen.pcm").write_bytes(b"\x02\x00" * RATE)
    return path


def test_load_manifest_validates(tmp_path):
    call = load_manifest(_manifest(tmp_path))
    assert call.sample_rate == RATE and [t.role for t in call.tracks] == ["ba", "cx"]
    assert is_audio_manifest(json.loads((tmp_path / "manifest.json").read_text()))
    assert not is_audio_manifest({"conversation": []})

    for bad in ({"dry_run": True}, {"audio": {"encoding": "mp3", "sample_rate": RATE, "channels": 1}},
                {"speakers": [{"speaker_id": "x", "role": "boss", "pcm": "ravi.pcm"}]},
                {"speakers": [{"speaker_id": "x", "role": "cx", "pcm": "missing.pcm"}]}):
        with pytest.raises(InvalidManifestError):
            load_manifest(_manifest(tmp_path, **bad))


async def test_final_segments_become_ordered_turns_with_speaker_from_track(tmp_path):
    FakeTranscriber.script = {
        b"\x01\x00": [(3, 0.0, 0.3, "Tell me about the project.")],
        b"\x02\x00": [(6, 0.4, 0.6, "We run on-prem."), (9, 0.7, 0.9, "About ten servers.")],
    }
    sender = FakeSender()
    result = await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                               sender=sender, transcriber_factory=FakeTranscriber, speed=50, max_speed=100,
                               title="Demo", assist_config={"scope": "open"}, sleep=_no_sleep)

    assert result.stopped == FINISHED and result.turns_sent == 3
    kinds = [e["type"] for e in sender.events]
    assert kinds[0] == "session.start" and kinds[-1] == "session.end" and kinds.count("turn") == 3
    assert sender.events[0]["source"] == "transcribe" and sender.events[0]["title"] == "Demo"
    turns = [e for e in sender.events if e["type"] == "turn"]
    assert {(t["speaker"], t["text"]) for t in turns} == {
        ("ba", "Tell me about the project."), ("cx", "We run on-prem."), ("cx", "About ten servers."),
    }
    assert all(t["is_final"] and t["source"] == "transcribe" and t["event_id"] for t in turns)
    assert FakeTranscriber.chunks_seen[b"\x01\x00"] == 10  # 1 s of audio in 100 ms chunks.


async def test_limit_seconds_stops_early(tmp_path):
    FakeTranscriber.script = {}
    await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                      sender=FakeSender(), transcriber_factory=FakeTranscriber, speed=50, max_speed=100,
                      sleep=_no_sleep, limit_seconds=0.25)
    assert FakeTranscriber.chunks_seen[b"\x01\x00"] == 3  # 100 + 100 + 50 ms


async def test_call_ended_elsewhere_stops_audio_and_skips_end(tmp_path):
    FakeTranscriber.script = {b"\x02\x00": [(2, 0.1, 0.2, "First."), (8, 0.7, 0.8, "Late.")]}
    statuses = iter(["live", "ended"])
    sender = FakeSender()
    result = await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                               sender=sender, transcriber_factory=FakeTranscriber, speed=1,
                               get_status=lambda _s: next(statuses, "ended"), status_poll_seconds=0.15)
    assert result.stopped == ENDED
    assert "session.end" not in [e["type"] for e in sender.events]
    assert FakeTranscriber.chunks_seen[b"\x02\x00"] < 10  # Audio stopped before the end.


async def test_rejected_turn_raises(tmp_path):
    FakeTranscriber.script = {b"\x01\x00": [(1, 0.0, 0.1, "Hi.")]}
    with pytest.raises(ReplayError):
        await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                          sender=FakeSender(reject_code="invalid_event"),
                          transcriber_factory=FakeTranscriber, speed=50, max_speed=100, sleep=_no_sleep)


async def test_speed_above_verified_maximum_rejected(tmp_path):
    with pytest.raises(ValueError):
        await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                          sender=FakeSender(), transcriber_factory=FakeTranscriber, speed=3)


# --- windows, readers and deadlines ------------------------------------------------

from replayer.core import DEADLINE  # noqa: E402
from replayer.transcribe import (  # noqa: E402
    AudioCall,
    S3Reader,
    Track,
    _rechunk,
    parse_manifest,
    plan_windows,
)


def _call_with_turns(turns, duration=1000.0):
    return AudioCall(16000, (), duration, tuple(turns))


def test_plan_windows_cuts_in_silence():
    # Turns every 100 s, 80 s long: gaps at 80-100, 180-200, ...
    turns = [(i * 100.0, i * 100.0 + 80.0, "a") for i in range(10)]
    windows = plan_windows(_call_with_turns(turns), 0, 1000, window=300)
    assert windows[0] == (0, 290.0)  # Last cut within 300 s.
    assert all(e - s <= 300 for s, e in windows)
    assert windows[-1][1] == 1000 and all(a[1] == b[0] for a, b in zip(windows, windows[1:]))
    # Every cut is in a gap, never inside a turn.
    for _, end in windows[:-1]:
        assert not any(s < end < e for s, e, _ in turns)


def test_plan_windows_long_turn_and_no_turns():
    assert plan_windows(_call_with_turns([(0, 700, "a"), (710, 720, "b")]), 0, 720, window=300) == [(0, 705.0), (705.0, 720)]
    assert plan_windows(_call_with_turns([]), 0, 900, window=300) == [(0, 900)]
    assert plan_windows(_call_with_turns([(0, 10, "a"), (20, 30, "b")]), 15, 30, window=300) == [(15, 30)]


def test_rechunk_gives_exact_sizes():
    assert list(_rechunk(iter([b"abc", b"defgh", b"i"]), 4)) == [b"abcd", b"efgh", b"i"]


def test_s3_reader_and_manifest(dynamodb_resource):
    import boto3

    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    s3.put_object(Bucket="test-bucket", Key="up/1/a.pcm", Body=bytes(range(200)))
    reader = S3Reader("test-bucket", "up/1/", client=s3)
    assert reader.size("a.pcm") == 200 and reader.size("missing.pcm") is None
    assert b"".join(reader.read("a.pcm", 10, 20)) == bytes(range(10, 20))

    manifest = {"audio": {"encoding": "pcm_s16le", "sample_rate": RATE, "channels": 1},
                "speakers": [{"speaker_id": "a", "role": "ba", "pcm": "a.pcm"}],
                "turns": [{"start_sec": 0, "end_sec": 0.005, "speaker_id": "a"}]}
    call = parse_manifest(manifest, reader)
    assert call.tracks[0].size_bytes == 200 and call.duration_sec == 200 / (RATE * 2)
    assert call.turns == ((0.0, 0.005, "a"),)

    for bad in ("../a.pcm", "/a.pcm", "x/../../a.pcm"):
        with pytest.raises(InvalidManifestError):
            parse_manifest({**manifest, "speakers": [{"speaker_id": "a", "role": "ba", "pcm": bad}]}, reader)


def _manifest_with_turns(tmp_path, turns, seconds=1.0):
    path = _two_track_manifest(tmp_path)
    data = json.loads(path.read_text())
    data["turns"] = [{"start_sec": s, "end_sec": e, "speaker_id": sid} for s, e, sid in turns]
    data["duration_sec"] = seconds
    path.write_text(json.dumps(data))
    return path


async def test_silent_speakers_are_not_streamed(tmp_path):
    FakeTranscriber.script, FakeTranscriber.chunks_seen = {}, {}
    path = _manifest_with_turns(tmp_path, [(0.0, 0.5, "ravi")])
    await stream_call(session_id=SID, call=load_manifest(path), sender=FakeSender(),
                      transcriber_factory=FakeTranscriber, speed=50, max_speed=100, sleep=_no_sleep)
    assert set(FakeTranscriber.chunks_seen) == {b"\x01\x00"}  # helen has no turn: no stream.


async def test_windows_open_new_streams_and_offset_timestamps(tmp_path):
    FakeTranscriber.script = {b"\x01\x00": [(1, 0.0, 0.1, "Hi.")]}
    # Gap at 0.4-0.6 s: two windows of at most 0.5 s.
    path = _manifest_with_turns(tmp_path, [(0.0, 0.4, "ravi"), (0.6, 1.0, "ravi")])
    sender = FakeSender()
    streams = []

    def factory():
        streams.append(FakeTranscriber())
        return streams[-1]

    result = await stream_call(session_id=SID, call=load_manifest(path), sender=sender, transcriber_factory=factory,
                               speed=50, max_speed=100, sleep=_no_sleep, window_seconds=0.5)
    assert result.stopped == FINISHED and len(streams) == 2
    turns = [e for e in sender.events if e["type"] == "turn"]
    assert len(turns) == 2  # One "Hi." per window.


async def test_deadline_stops_before_a_window_that_would_not_fit(tmp_path):
    FakeTranscriber.script = {}
    path = _manifest_with_turns(tmp_path, [(0.0, 0.4, "ravi"), (0.6, 1.0, "ravi")])
    clock = {"t": 0.0}
    result = await stream_call(session_id=SID, call=load_manifest(path), sender=FakeSender(),
                               transcriber_factory=FakeTranscriber, speed=1, sleep=_no_sleep,
                               clock=lambda: clock["t"], deadline=10.0, window_seconds=0.5)
    # Each window needs 0.5 s + 15 s tail, more than the 10 s left.
    assert result.stopped == DEADLINE and result.next_offset == 0


async def test_continuation_skips_start_and_resumes_at_offset(tmp_path):
    FakeTranscriber.script, FakeTranscriber.chunks_seen = {}, {}
    sender = FakeSender()
    result = await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)), sender=sender,
                               transcriber_factory=FakeTranscriber, speed=50, max_speed=100, sleep=_no_sleep,
                               send_start=False, start_offset=0.5)
    assert result.stopped == FINISHED
    assert [e["type"] for e in sender.events] == ["session.end"]
    assert FakeTranscriber.chunks_seen[b"\x01\x00"] == 5  # Only the second half.


async def test_pause_into_deadline_returns_pause_position(tmp_path):
    FakeTranscriber.script = {}
    clock = {"t": 0.0}

    async def tick(seconds):
        clock["t"] += seconds
        await asyncio.sleep(0)

    statuses = iter(["live"] * 3 + ["paused"] * 1000)
    result = await stream_call(session_id=SID, call=load_manifest(_two_track_manifest(tmp_path)),
                               sender=FakeSender(), transcriber_factory=FakeTranscriber, speed=1,
                               get_status=lambda _s: next(statuses), status_poll_seconds=0.1,
                               clock=lambda: clock["t"], sleep=tick, deadline=20.0)
    assert result.stopped == DEADLINE and 0 < result.next_offset < 1.0


def test_extend_to_silence():
    from replayer.transcribe import extend_to_silence

    call = _call_with_turns([(0.0, 10.0, "a"), (9.0, 20.0, "b"), (25.0, 30.0, "a")])
    assert extend_to_silence(call, 5.0) == 20.0   # Inside overlapping turns: to the end of both.
    assert extend_to_silence(call, 22.0) == 22.0  # Already in silence.
    assert extend_to_silence(_call_with_turns([]), 5.0) == 5.0


async def test_limit_does_not_cut_a_turn(tmp_path):
    FakeTranscriber.script, FakeTranscriber.chunks_seen = {}, {}
    path = _manifest_with_turns(tmp_path, [(0.0, 0.5, "ravi"), (0.7, 1.0, "helen")])
    await stream_call(session_id=SID, call=load_manifest(path), sender=FakeSender(),
                      transcriber_factory=FakeTranscriber, speed=50, max_speed=100, sleep=_no_sleep,
                      limit_seconds=0.25)
    assert FakeTranscriber.chunks_seen[b"\x01\x00"] == 5  # Ran to 0.5 s, the end of the turn.
