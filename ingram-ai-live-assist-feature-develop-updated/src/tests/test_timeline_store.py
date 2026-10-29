import time

import pytest

from app.assist_config import AssistConfig
from app.events import SessionStart, Turn
from app.session_store import SessionNotFoundError
from app.timeline_store import SessionEndedError, TTL_SECONDS


def _start(sid, **kw):
    return SessionStart(sid, kw.get("source", "recording"), kw.get("title"), kw.get("config", AssistConfig()))


def _turn(sid, text="hi", speaker="cx"):
    return Turn(sid, "recording", speaker, text)


def test_start_is_idempotent(timeline, session_id):
    meta, created = timeline.start_session(_start(session_id, title="Call", config=AssistConfig(max_suggestions=2)))
    assert created and meta["status"] == "live" and meta["next_seq"] == 0
    assert meta["assist_config"]["max_suggestions"] == 2

    again, created = timeline.start_session(_start(session_id, title="Other"))
    assert not created and again["title"] == "Call"


def test_turns_get_increasing_seq_and_ttl(timeline, session_id):
    timeline.start_session(_start(session_id))
    assert [timeline.append_turn(_turn(session_id, f"t{i}")) for i in range(3)] == [1, 2, 3]

    pending = timeline.pending_turns(session_id, after_seq=1, limit=10)
    assert [t["seq"] for t in pending] == [2, 3]
    assert pending[0]["text"] == "t1"
    assert pending[0]["expires_at"] > time.time() + TTL_SECONDS - 60
    assert [t["seq"] for t in timeline.pending_turns(session_id, after_seq=0, limit=2)] == [1, 2]


def test_turn_for_unknown_or_ended_session_rejected(timeline, session_id):
    with pytest.raises(SessionNotFoundError):
        timeline.append_turn(_turn(session_id))

    timeline.start_session(_start(session_id))
    meta, ended_now = timeline.end_session(session_id, "done")
    assert ended_now and meta["status"] == "ended" and meta["end_reason"] == "done"
    with pytest.raises(SessionEndedError):
        timeline.append_turn(_turn(session_id))

    _, ended_now = timeline.end_session(session_id, "again")
    assert not ended_now


def test_end_unknown_session_rejected(timeline, session_id):
    with pytest.raises(SessionNotFoundError):
        timeline.end_session(session_id, None)


def test_update_config_and_status(timeline, session_id):
    timeline.start_session(_start(session_id))
    meta = timeline.update_session(session_id, assist_config=AssistConfig(scope="open"), status="paused")
    assert meta["status"] == "paused" and meta["assist_config"]["scope"] == "open"

    # A paused call still accepts turns: pause is for the source, not the agent.
    assert timeline.append_turn(_turn(session_id)) == 1

    timeline.end_session(session_id, None)
    with pytest.raises(SessionEndedError):
        timeline.update_session(session_id, status="live")


def test_runs_overwrite_by_batch_and_items_are_ordered(timeline, session_id):
    timeline.start_session(_start(session_id))
    timeline.append_turn(_turn(session_id))
    timeline.put_run(session_id, 1, {"status": "error", "duration_ms": 1.5})
    timeline.put_run(session_id, 1, {"status": "ok", "duration_ms": 900})

    sks = [i["sk"] for i in timeline.items(session_id)]
    assert sks == ["meta", "run#000001", "turn#000001"]
    run = timeline.items(session_id)[1]
    assert run["status"] == "ok" and run["duration_ms"] == 900


def test_list_calls_newest_first_with_cursor(timeline):
    ids = [f"test-call-{i:04d}-abcdefghijklmnopqrstuvwxyz" for i in range(3)]
    for i, sid in enumerate(ids):
        meta, _ = timeline.start_session(_start(sid))
        timeline._table.update_item(  # Distinct creation times for a stable order.
            Key={"session_id": sid, "sk": "meta"}, UpdateExpression="SET created_at = :c",
            ExpressionAttributeValues={":c": f"2026-10-13T10:00:0{i}Z"},
        )
    page, cursor = timeline.list_calls(limit=2)
    assert [m["session_id"] for m in page] == [ids[2], ids[1]] and cursor
    rest, cursor = timeline.list_calls(limit=2, cursor=cursor)
    assert [m["session_id"] for m in rest] == [ids[0]]


def test_exclusive_source_owns_the_turns(timeline, session_id):
    from app.timeline_store import SourceMismatchError

    meta, _ = timeline.start_session(_start(session_id, source="transcribe"))
    assert meta["exclusive_source"] == "transcribe"
    assert timeline.append_turn(Turn(session_id, "transcribe", "cx", "From audio.")) == 1
    with pytest.raises(SourceMismatchError):
        timeline.append_turn(Turn(session_id, "manual", "ba", "Typed."))
    assert timeline.get_meta(session_id)["next_seq"] == 1  # Rejected turn didn't take a seq.


def test_manual_and_unknown_sources_are_not_exclusive(timeline, session_id):
    meta, _ = timeline.start_session(_start(session_id, source="manual"))
    assert "exclusive_source" not in meta
    assert timeline.append_turn(Turn(session_id, "recording", "cx", "Anything goes.")) == 1
