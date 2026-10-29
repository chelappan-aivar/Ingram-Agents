import json

import pytest

from app.conversation_import import parse_conversation
from replayer.core import DEADLINE, ENDED, FINISHED, ReplayError, new_session_id, replay

SID = "call-0001-abcdefghijklmnopqrstuvwxyz0123"
RECORDING = parse_conversation({"conversation": [
    {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "name": "Ravi", "text": "Hello."},
    {"timestamp": "2026-10-08T10:00:10Z", "speaker": "cx", "name": "Helen", "text": "Hi."},
    {"timestamp": "2026-10-08T10:00:40Z", "speaker": "cx", "text": "We run on-prem."},
]})


class FakeSender:
    def __init__(self, reject_turn_at=None, code=None):
        self.events = []
        self.reject_turn_at = reject_turn_at
        self.code = code

    def send(self, event):
        self.events.append(event)
        if event["type"] == "turn" and len(self.turns()) - 1 == self.reject_turn_at:
            return {"accepted": False, "code": self.code, "error": "nope"}
        return {"accepted": True, "seq": len(self.turns())}

    def turns(self):
        return [e for e in self.events if e["type"] == "turn"]


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


def test_full_replay_sends_events_at_scaled_capped_pace():
    sender, clock = FakeSender(), FakeClock()
    result = replay(session_id=SID, turns=RECORDING, sender=sender, speed=2, max_gap_seconds=8,
                    title="Demo", assist_config={"scope": "open"}, sleep=clock.sleep, now=clock.now)

    assert result.stopped == FINISHED and result.turns_sent == 3
    assert [e["type"] for e in sender.events] == ["session.start", "turn", "turn", "turn", "session.end"]
    assert sender.events[0]["title"] == "Demo" and sender.events[0]["assist_config"] == {"scope": "open"}
    assert clock.sleeps == [4.0, 4.0]  # 10s and 30s gaps, capped at 8, divided by 2.

    turn = sender.events[2]
    assert turn["speaker_name"] == "Helen" and turn["spoken_at"] == "2026-10-08T10:00:10Z"
    assert turn["is_final"] is True and turn["event_id"] == "rec-1"
    assert sender.events[3]["speaker_name"] == "Customer"  # Default display name.
    json.dumps(sender.events)  # Events must be JSON-serializable.


def test_pause_waits_and_end_stops():
    statuses = iter(["live", "paused", "paused", "live", "ended"])
    sender, clock = FakeSender(), FakeClock()
    result = replay(session_id=SID, turns=RECORDING, sender=sender, max_gap_seconds=0,
                    get_status=lambda _sid: next(statuses), sleep=clock.sleep, now=clock.now)

    assert result.stopped == ENDED and result.turns_sent == 2
    assert clock.sleeps.count(1.0) == 2  # Two pause polls.
    assert sender.events[-1]["type"] == "turn"  # No session.end: someone else ended it.


def test_deadline_returns_resume_index_and_continuation_skips_start():
    sender, clock = FakeSender(), FakeClock()
    result = replay(session_id=SID, turns=RECORDING, sender=sender, max_gap_seconds=30,
                    deadline=20, sleep=clock.sleep, now=clock.now)
    assert result.stopped == DEADLINE and result.next_index == 2 and result.turns_sent == 2

    resumed = FakeSender()
    result = replay(session_id=SID, turns=RECORDING, sender=resumed, start_index=2,
                    sleep=FakeClock().sleep, now=FakeClock().now)
    assert result.stopped == FINISHED
    assert [e["type"] for e in resumed.events] == ["turn", "session.end"]


def test_rejections():
    ended = replay(session_id=SID, turns=RECORDING, sender=FakeSender(reject_turn_at=1, code="session_ended"),
                   sleep=FakeClock().sleep)
    assert ended.stopped == ENDED

    with pytest.raises(ReplayError):
        replay(session_id=SID, turns=RECORDING, sender=FakeSender(reject_turn_at=0, code="invalid_event"),
               sleep=FakeClock().sleep)


def test_new_session_id_meets_runtime_minimum():
    sid = new_session_id()
    assert 33 <= len(sid) <= 100 and sid != new_session_id()
