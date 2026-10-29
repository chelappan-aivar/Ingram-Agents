import pytest

from app.assist_config import AssistConfig
from app.events import (
    InvalidEventError,
    SessionEnd,
    SessionStart,
    SessionUpdate,
    Turn,
    parse_event,
)

SID = "test-call-0001-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret (test session id)


def _event(kind, **fields):
    return {"schema": "ila.event.v1", "type": kind, "session_id": SID, **fields}


def test_session_start_defaults_and_config():
    event = parse_event(_event("session.start", assist_config={"scope": "open"}))
    assert event == SessionStart(SID, "unknown", None, AssistConfig(scope="open"))


def test_turn_normalizes_fields():
    event = parse_event(_event(
        "turn", speaker="cx", text="  We run on-prem.  ", speaker_name="Helen\n[x]",
        spoken_at="2026-10-08T15:30:20+05:30", source="recording", event_id="rec-1",
    ))
    assert event == Turn(
        SID, "recording", "cx", "We run on-prem.", speaker_name="Helen x",
        spoken_at="2026-10-08T10:00:20Z", event_id="rec-1",
    )
    assert event.to_chunk().format_line() == "[2026-10-08T10:00:20Z | cx | Helen x]: We run on-prem."


def test_session_update_and_end():
    update = parse_event(_event("session.update", status="paused"))
    assert update == SessionUpdate(SID, None, "paused")
    assert parse_event(_event("session.end", reason="done")) == SessionEnd(SID, "done")


@pytest.mark.parametrize(
    "payload",
    [
        "not an object",
        {"type": "turn", "session_id": SID, "speaker": "cx", "text": "x"},  # no schema
        _event("call.start"),
        {**_event("session.start"), "session_id": "too-short"},
        {**_event("session.start"), "session_id": "bad/chars-" + "x" * 30},
        _event("session.start", unexpected=1),
        _event("turn", speaker="nobody", text="x"),
        _event("turn", speaker="cx", text="   "),
        _event("turn", speaker="cx", text="x" * 10_001),
        _event("turn", speaker="cx", text="x", is_final=False),
        _event("turn", speaker="cx", text="x", spoken_at="yesterday"),
        _event("turn", speaker="cx", text="x", speaker_name=5),
        _event("session.update"),
        _event("session.update", status="ended"),
        _event("session.update", assist_config={"scope": "everything"}),
        _event("session.end", reason=""),
    ],
)
def test_invalid_events_rejected(payload):
    with pytest.raises(InvalidEventError):
        parse_event(payload)


def test_error_messages_use_contract_field_names():
    with pytest.raises(InvalidEventError, match="spoken_at"):
        parse_event(_event("turn", speaker="cx", text="x", spoken_at="yesterday"))


def test_session_summarize_parses_and_rejects_extra_keys():
    from app.events import SessionSummarize

    assert parse_event(_event("session.summarize")) == SessionSummarize(SID)
    with pytest.raises(InvalidEventError, match="unknown keys"):
        parse_event(_event("session.summarize", reason="x"))
