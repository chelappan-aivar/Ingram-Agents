"""Long-term memory: ids, scopes, the memory client, prior context and end-of-call writes."""

from __future__ import annotations

import pytest

from app.account_store import AccountNotFoundError, new_entity_id
from app.assist_config import AssistConfig
from app.call_memory import load_prior_context, remember_call
from app.events import ENTITY_ID_RE, InvalidEventError, SessionStart, Turn, parse_event
from app.memory import RETENTION_DAYS, MemoryScope, account_delete_prefixes, facts_namespace
from app.session_store import SessionState

SID = "test-call-0001-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret (test session id)
OTHER_SID = "test-call-0002-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret (test session id)


def _start_event(**fields):
    return {"schema": "ila.event.v1", "type": "session.start", "session_id": SID, **fields}


# --- ids and events ---------------------------------------------------------------

@pytest.mark.parametrize("name", ["Acme Corp", "  ÄÖ  ", "x" * 80, "a--b", "123"])
def test_generated_ids_are_valid(name):
    entity_id = new_entity_id(name)
    assert ENTITY_ID_RE.match(entity_id) and "--" not in entity_id and len(entity_id) <= 40


def test_session_start_carries_account_scope():
    event = parse_event(_start_event(account_id="acme-1a2b3c", opportunity_id="erp-move-4d5e6f",
                                     opportunity_type="cloud_migration"))
    assert (event.account_id, event.opportunity_id, event.preset_opportunity_type) == (
        "acme-1a2b3c", "erp-move-4d5e6f", "cloud_migration")


@pytest.mark.parametrize("fields", [
    {"opportunity_id": "erp-1"},          # Needs an account.
    {"account_id": "Acme"},                # Uppercase.
    {"account_id": "acme--x"},             # Double hyphen would break actor ids.
    {"account_id": "a" * 41},
])
def test_session_start_rejects_bad_scope(fields):
    with pytest.raises(InvalidEventError):
        parse_event(_start_event(**fields))


# --- scope --------------------------------------------------------------------------

def test_scope_actors_and_delete_prefixes():
    scope = MemoryScope("acme", "erp")
    assert scope.actors() == [("account", "acct-acme"), ("opportunity", "opp-acme--erp")]
    assert scope.actors("opportunity") == [("opportunity", "opp-acme--erp")]
    assert MemoryScope("acme").actors() == [("account", "acct-acme")]
    assert account_delete_prefixes("acme") == ["acct-acme/", "opp-acme--"]
    assert account_delete_prefixes("acme", "erp") == ["opp-acme--erp/"]
    assert MemoryScope.from_meta({"account_id": "acme"}) == MemoryScope("acme", None)
    assert MemoryScope.from_meta({}) is None


# --- memory client ------------------------------------------------------------------

def test_search_and_list_stay_in_scope_and_drop_old_records(memory, memory_client):
    memory_client.add_record(facts_namespace("acct-acme"), "Acme hosts on VMware in Dallas", score=0.9)
    memory_client.add_record(facts_namespace("opp-acme--erp"), "ERP move must finish by June", score=0.8)
    memory_client.add_record(facts_namespace("acct-acme"), "Acme hosts on old mainframe",
                             age_days=RETENTION_DAYS + 1)
    memory_client.add_record(facts_namespace("acct-acmex"), "Acmex hosts on Azure")  # Other account.

    hits = memory.search(MemoryScope("acme", "erp"), "hosts", "both", 5)
    assert [h.text for h in hits] == ["Acme hosts on VMware in Dallas"]

    listed = memory.list_facts(MemoryScope("acme", "erp"), 10)
    assert {(f.level, f.text) for f in listed} == {
        ("account", "Acme hosts on VMware in Dallas"), ("opportunity", "ERP move must finish by June")}


def test_delete_account_removes_all_its_levels_only(memory, memory_client):
    memory_client.add_record(facts_namespace("acct-acme"), "a")
    memory_client.add_record(facts_namespace("opp-acme--erp"), "b")
    memory_client.add_record(facts_namespace("opp-acme--crm"), "c")
    keep = memory_client.add_record(facts_namespace("opp-acme-2--erp"), "other account")

    assert memory.delete(account_delete_prefixes("acme", "erp")) == 1
    assert memory.delete(account_delete_prefixes("acme")) == 2
    assert list(memory_client.records) == [keep]


def test_write_notes_is_idempotent(memory, memory_client):
    memory.write_notes(actor="acct-acme", session_id=SID, text="notes")
    memory.write_notes(actor="acct-acme", session_id=SID, text="notes")
    assert len(memory_client.events) == 1
    assert memory_client.events[0]["payload"][0]["conversational"]["role"] == "USER"


# --- prior context and end-of-call writes ---------------------------------------------

MINUTES = {
    "status": "ok", "generated_at": "2026-10-15T10:00:00Z",
    "minutes": {
        "summary": "Acme wants to move ERP.", "attendees": [{"speaker": "cx", "name": "Helen"}],
        "key_points": [], "decisions": [{"text": "Start with finance.", "citations": [2]}],
        "action_items": [{"text": "Send inventory.", "owner": "cx", "citations": [3]}],
        "risks": [], "open_questions": [{"text": "Target region?", "citations": []}], "next_steps": [],
    },
}


def _ended_call(timeline, sid, *, account="acme", opportunity="erp", minutes=MINUTES):
    timeline.start_session(SessionStart(sid, "manual", "Kickoff", AssistConfig(), account, opportunity))
    timeline.append_turn(Turn(sid, "manual", "cx", "We run VMware."))
    timeline.end_session(sid, "done")
    if minutes:
        timeline.put_minutes(sid, minutes)


def test_prior_context_has_facts_and_earlier_calls(timeline, memory, memory_client):
    _ended_call(timeline, OTHER_SID)
    memory_client.add_record(facts_namespace("acct-acme"), "Acme runs VMware")
    timeline.start_session(SessionStart(SID, "manual", "Follow-up", AssistConfig(), "acme", "erp"))

    prior = load_prior_context(SID, MemoryScope("acme", "erp"), memory=memory, timeline=timeline)

    assert prior["account"] == ["Acme runs VMware"] and prior["opportunity"] == []
    assert [c["session_id"] for c in prior["previous_calls"]] == [OTHER_SID]  # Not the current call.
    assert prior["previous_calls"][0]["decisions"] == ["Start with finance."]


def test_prior_context_without_memory_still_has_earlier_calls(timeline):
    _ended_call(timeline, OTHER_SID)
    prior = load_prior_context(SID, MemoryScope("acme", "erp"), memory=None, timeline=timeline)
    assert prior["account"] == [] and len(prior["previous_calls"]) == 1


def test_prior_context_is_none_when_memory_is_down(timeline, memory, memory_client):
    memory_client.fail = True
    assert load_prior_context(SID, MemoryScope("acme"), memory=memory, timeline=timeline) is None


def test_prior_context_is_none_when_earlier_calls_cannot_be_read(timeline, memory, monkeypatch):
    """Regression: a denied index query used to fail the whole agent run."""
    from botocore.exceptions import ClientError

    def denied(*_a, **_kw):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Query")

    monkeypatch.setattr(timeline, "list_account_calls", denied)
    assert load_prior_context(SID, MemoryScope("acme"), memory=memory, timeline=timeline) is None


def test_earlier_calls_of_another_opportunity_are_not_included(timeline):
    _ended_call(timeline, OTHER_SID, opportunity="crm")
    prior = load_prior_context(SID, MemoryScope("acme", "erp"), memory=None, timeline=timeline)
    assert prior["previous_calls"] == []


def test_remember_call_writes_each_level_once(timeline, session_store, memory, memory_client):
    _ended_call(timeline, SID)
    state = SessionState.new(SID)
    state.add_field("current_environment", label="Current environment")
    state.set_field_captured("current_environment", "VMware in Dallas", "cx", [1])
    state.add_field("scope", label="Scope")
    state.set_field_captured("scope", "Finance ERP", "cx", [1])
    session_store.save(state)

    assert remember_call(SID, timeline=timeline, store=session_store, memory=memory) is True
    by_actor = {e["actorId"]: e["payload"][0]["conversational"]["content"]["text"] for e in memory_client.events}
    assert set(by_actor) == {"acct-acme", "opp-acme--erp"}
    assert "VMware in Dallas" in by_actor["acct-acme"] and "Finance ERP" not in by_actor["acct-acme"]
    assert "Customer contacts: Helen" in by_actor["acct-acme"]
    deal = by_actor["opp-acme--erp"]
    assert "Finance ERP" in deal and "Start with finance." in deal and "Send inventory. (owner: cx)" in deal
    # Facts only: no call header (extracted as "the user had a call") and no open questions.
    assert "Kickoff" not in deal and "Target region?" not in deal
    assert "Kickoff" not in by_actor["acct-acme"]

    assert remember_call(SID, timeline=timeline, store=session_store, memory=memory) is False
    assert timeline.get_minutes(SID)["memory_written_for"] == MINUTES["generated_at"]


def test_remember_call_skips_calls_without_account_or_minutes(timeline, session_store, memory, memory_client):
    timeline.start_session(SessionStart(SID, "manual", None, AssistConfig()))
    timeline.end_session(SID, "done")
    timeline.put_minutes(SID, MINUTES)
    assert remember_call(SID, timeline=timeline, store=session_store, memory=memory) is False

    _ended_call(timeline, OTHER_SID, minutes={"status": "error", "error_code": "timeout"})
    assert remember_call(OTHER_SID, timeline=timeline, store=session_store, memory=memory) is False
    assert memory_client.events == []


def test_call_without_opportunity_writes_deal_notes_to_the_account(timeline, session_store, memory, memory_client):
    _ended_call(timeline, SID, opportunity=None)
    remember_call(SID, timeline=timeline, store=session_store, memory=memory)
    assert {e["actorId"] for e in memory_client.events} == {"acct-acme"}


# --- accounts table -------------------------------------------------------------------

def test_forget_hides_earlier_calls_from_new_calls(timeline, accounts, monkeypatch):
    _ended_call(timeline, OTHER_SID)
    acme = accounts.create_account("Acme")
    monkeypatch.setattr(timeline, "list_account_calls",
                        lambda *a, **kw: ([dict(timeline.get_meta(OTHER_SID), ended_at="2026-10-10T10:00:00Z")], None))
    scope = MemoryScope(acme["account_id"])
    assert len(load_prior_context(SID, scope, memory=None, timeline=timeline, accounts=accounts)["previous_calls"]) == 1

    accounts.mark_forgotten(acme["account_id"])
    assert load_prior_context(SID, scope, memory=None, timeline=timeline, accounts=accounts)["previous_calls"] == []


def test_opportunity_forget_is_later_of_the_two(accounts):
    acme = accounts.create_account("Acme")["account_id"]
    erp = accounts.create_opportunity(acme, "ERP")["opportunity_id"]
    assert accounts.forgotten_at(acme, erp) is None
    at = accounts.mark_forgotten(acme, erp)
    assert accounts.forgotten_at(acme, erp) == at and accounts.forgotten_at(acme) is None


def test_accounts_and_opportunities(accounts, timeline):
    acme = accounts.create_account("Acme Corp")
    accounts.create_account("Globex")
    erp = accounts.create_opportunity(acme["account_id"], "ERP move", "cloud_migration")

    listed, _ = accounts.list_accounts()
    assert {a["name"] for a in listed} == {"Acme Corp", "Globex"}
    assert accounts.get_opportunity(acme["account_id"], erp["opportunity_id"])["opportunity_type"] == "cloud_migration"
    assert [o["name"] for o in accounts.list_opportunities(acme["account_id"])] == ["ERP move"]
    with pytest.raises(AccountNotFoundError):
        accounts.create_opportunity("missing-1", "x")

    # Accounts share the calls index but never show up as calls.
    assert timeline.list_calls()[0] == []


def test_account_calls_index(timeline):
    _ended_call(timeline, SID, minutes=None)
    _ended_call(timeline, OTHER_SID, opportunity="crm", minutes=None)
    calls, _ = timeline.list_account_calls("acme")
    assert {c["session_id"] for c in calls} == {SID, OTHER_SID}
    calls, _ = timeline.list_account_calls("acme", opportunity_id="crm")
    assert [c["session_id"] for c in calls] == [OTHER_SID]
    assert timeline.list_account_calls("globex")[0] == []


def test_retention_defaults_to_180_days():
    from app.timeline_store import TTL_SECONDS

    assert RETENTION_DAYS == 180 and TTL_SECONDS == 180 * 24 * 3600
