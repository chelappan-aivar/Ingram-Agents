import pytest

from app.session_store import (
    FIELD_SOURCE_AGENT,
    FIELD_STATUS_CAPTURED,
    SessionNotFoundError,
    SessionState,
)


def test_create_and_get_roundtrip(session_store):
    state = session_store.create("session-1")
    assert state.opportunity_type is None
    assert session_store.get("session-1").session_id == "session-1"


def test_get_missing_session_returns_none(session_store):
    assert session_store.get("does-not-exist") is None


def test_require_missing_session_raises(session_store):
    with pytest.raises(SessionNotFoundError):
        session_store.require("does-not-exist")


def test_fields_roundtrip_through_dynamodb(session_store):
    state = session_store.create("session-2")
    state.problem_summary = "Data center lease ends; wants to exit on-prem."
    state.add_field("problem_statement", label="Problem", required=True)
    state.add_field("custom_sap", label="SAP integration", description="Which SAP modules?",
                    source=FIELD_SOURCE_AGENT)
    state.set_field_captured("problem_statement", "exit DC", "cx")
    session_store.save(state)

    fetched = session_store.require("session-2")
    assert fetched.problem_summary.startswith("Data center")
    assert fetched.fields["problem_statement"].status == FIELD_STATUS_CAPTURED
    assert fetched.fields["custom_sap"].source == FIELD_SOURCE_AGENT
    assert isinstance(fetched.fields["custom_sap"].order, int)  # not Decimal


def test_open_fields_required_first_then_order():
    state = SessionState.new("s")
    state.add_field("a", label="A", required=False)
    state.add_field("b", label="B", required=True)
    state.add_field("c", label="C", required=True)
    state.set_field_captured("c", "x", "cx")
    assert [fid for fid, _ in state.open_fields()] == ["b", "a"]


def test_add_field_merges_guidance_definition_but_keeps_status():
    state = SessionState.new("s")
    state.add_field("current_environment", label="General env", description="generic", required=False)
    state.set_field_captured("current_environment", "60 VMs", "cx")

    added = state.add_field("current_environment", label="Specific env", description="specific", required=True)

    fs = state.fields["current_environment"]
    assert added is False
    assert fs.label == "Specific env" and fs.required is True
    assert fs.status == FIELD_STATUS_CAPTURED and fs.value == "60 VMs"


def test_not_applicable_is_not_open():
    state = SessionState.new("s")
    state.add_field("budget_range", label="Budget")
    state.set_field_not_applicable("budget_range", "customer funded by AWS credits")
    assert state.open_fields() == []
    assert state.dismissed_fields()[0][1].note.startswith("customer funded")
