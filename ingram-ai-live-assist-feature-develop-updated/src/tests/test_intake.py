"""Intake Agent opportunities in Live Assist: listing funding-checked opportunities and the call history map."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main
from api.app import Api, Services
from app.intake_feed import IntakeFeed
from app.intake_link_store import IntakeLinkStore
from tests.conftest import TEST_TIMELINE_TABLE
from tests.test_api import BUCKET, EntryPointSender, call

KEY = "test-access-key"
INTAKE_TABLE = "test-ingram-intake-runs"
WOOD = "11111111-1111-4111-8111-111111111111"
NORTH = "22222222-2222-4222-8222-222222222222"
WOOD_2 = "33333333-3333-4333-8333-333333333333"


def _latest(opp_id, name, customer, checked_at, status, program, total):
    return {
        "pk": f"FUNDLATEST#{opp_id}", "sk": "META", "owner": "FUNDING#LATEST", "created_at": checked_at,
        "latest": {"status": status, "program": program, "total_usd": total, "at": checked_at},
        "result": {
            "summary": f"{customer} summary",
            "opportunity": {"id": opp_id, "name": name, "customer": customer, "source": "dynamics"},
            "decision": {"status": status, "program": program, "funding": "25% of ARR", "total_usd": total,
                         "arr_usd": 240000, "rationale": "because", "components": [{"name": "Assess", "amount_usd": 1}],
                         "path": [{"step": "Segment", "value": "Startup", "known": True}],
                         "missing": [], "assumptions": [{"fact": "vms", "assumed": "Yes", "question": "Are VMs in scope?"}],
                         "notes": ["note"]},
            "conflicts": [], "facts": [{"label": "Workload", "display": "Migration", "source": "form", "used": True},
                                       {"label": "Gen AI stage", "display": "Unknown", "used": False}],
        },
    }


@pytest.fixture
def intake_table(dynamodb_resource):
    table = dynamodb_resource.create_table(
        TableName=INTAKE_TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "owner", "created_at")],
        GlobalSecondaryIndexes=[{"IndexName": "owner-created", "KeySchema": [
            {"AttributeName": "owner", "KeyType": "HASH"}, {"AttributeName": "created_at", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["run_id", "status"]}}],
        BillingMode="PAY_PER_REQUEST")
    table.put_item(Item=_latest(WOOD, "Woodgrove - DC exit", "Woodgrove Bank", "2026-10-27T10:00:00Z", "decided", "MAP", None))
    table.put_item(Item=_latest(NORTH, "Northwind - Gen AI", "Northwind Health", "2026-10-27T11:00:00Z", "decided", "POC", 30000))
    table.put_item(Item=_latest(WOOD_2, "Woodgrove - backup", "Woodgrove Bank", "2026-10-27T09:00:00Z", "decided", "MAP Lite", 60000))
    # Not part of the feed: an Intake run and a session (other owners) and a latest item mid-check (no result yet).
    table.put_item(Item={"pk": "RUN#abc", "sk": "META", "owner": "sessionhash", "created_at": "2026-10-27T12:00:00Z"})
    table.put_item(Item={"pk": "FUNDLATEST#44444444-4444-4444-8444-444444444444", "sk": "META",
                         "pending": {"check_id": "x"}})
    return table


def _api(timeline, session_store, accounts, monkeypatch, feed=None, links=None):
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(schedule=lambda _sid, **_kw: None))
    return Api(Services(
        timeline=timeline, store=session_store, sender=EntryPointSender(), s3=None, bucket=BUCKET,
        start_source=lambda job: None, access_key=lambda: KEY, accounts=accounts,
        intake_feed=feed, intake_links=links,
    ))


@pytest.fixture
def intake_api(timeline, session_store, accounts, dynamodb_resource, intake_table, monkeypatch):
    feed = IntakeFeed(INTAKE_TABLE, resource=dynamodb_resource)
    links = IntakeLinkStore(accounts, table_name=TEST_TIMELINE_TABLE, resource=dynamodb_resource)
    return _api(timeline, session_store, accounts, monkeypatch, feed, links)


def _start_call(api, link, title="Discovery"):
    status, _, out = call(api, "POST", "/calls", {"title": title, "account_id": link["account_id"],
                                                  "opportunity_id": link["opportunity_id"]})
    assert status == 201, out
    return out["session_id"]


def test_lists_checked_opportunities_newest_first(intake_api):
    status, _, out = call(intake_api, "GET", "/intake/opportunities")
    assert status == 200 and out["enabled"] is True
    assert [o["intake_opportunity_id"] for o in out["opportunities"]] == [NORTH, WOOD, WOOD_2]
    north = out["opportunities"][0]
    assert north["decision"]["program"] == "POC" and north["decision"]["total_usd"] == 30000
    assert north["customer"] == "Northwind Health" and north["call_count"] == 0 and north["last_call_at"] is None


def test_detail_has_path_questions_and_used_facts(intake_api):
    status, _, out = call(intake_api, "GET", f"/intake/opportunities/{WOOD}")
    assert status == 200
    assert out["decision"]["path"] == [{"step": "Segment", "value": "Startup"}]
    assert out["decision"]["missing"] == ["Are VMs in scope?"] and out["decision"]["notes"] == ["note"]
    assert out["decision"]["assumptions"] == [{"fact": "vms", "assumed": "Yes", "question": "Are VMs in scope?"}]
    assert out["facts"] == [{"label": "Workload", "value": "Migration", "source": "form"}]
    assert out["link"] is None and out["calls"] == []


def test_call_history_map(intake_api):
    link = call(intake_api, "POST", f"/intake/opportunities/{WOOD}/link")[2]
    assert call(intake_api, "POST", f"/intake/opportunities/{WOOD}/link")[2] == link   # stable
    first = _start_call(intake_api, link, "First call")
    status, _, entry = call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": first})
    assert status == 201 and entry["decision"] == {"status": "decided", "program": "MAP", "funding": "25% of ARR",
                                                   "total_usd": None}
    assert call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": first})[0] == 201  # idempotent
    second = _start_call(intake_api, link, "Second call")
    call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": second})

    calls = call(intake_api, "GET", f"/intake/opportunities/{WOOD}/calls")[2]["calls"]
    assert {c["session_id"] for c in calls} == {first, second} and all(c["status"] == "live" for c in calls)
    listed = {o["intake_opportunity_id"]: o for o in call(intake_api, "GET", "/intake/opportunities")[2]["opportunities"]}
    assert listed[WOOD]["call_count"] == 2 and listed[WOOD]["last_call_at"]

    call(intake_api, "POST", f"/calls/{first}/end", {})
    calls = call(intake_api, "GET", f"/intake/opportunities/{WOOD}")[2]["calls"]
    assert {c["session_id"]: c["status"] for c in calls}[first] == "ended"
    # The Live Assist opportunity behind it lists the same calls (existing account routes, memory across calls).
    acct = call(intake_api, "GET", f"/accounts/{link['account_id']}/calls",
                query={"opportunity_id": link["opportunity_id"]})[2]["calls"]
    assert {c["session_id"] for c in acct} == {first, second}


def test_same_customer_reuses_one_account(intake_api):
    a = call(intake_api, "POST", f"/intake/opportunities/{WOOD}/link")[2]
    b = call(intake_api, "POST", f"/intake/opportunities/{WOOD_2}/link")[2]
    c = call(intake_api, "POST", f"/intake/opportunities/{NORTH}/link")[2]
    assert a["account_id"] == b["account_id"] != c["account_id"]
    assert a["opportunity_id"] != b["opportunity_id"]


def test_only_linked_calls_join_the_history(intake_api):
    wood = call(intake_api, "POST", f"/intake/opportunities/{WOOD}/link")[2]
    north = call(intake_api, "POST", f"/intake/opportunities/{NORTH}/link")[2]
    northwind_call = _start_call(intake_api, north)
    status, _, out = call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": northwind_call})
    assert status == 409 and out["error"]["code"] == "call_not_linked"
    plain_call = call(intake_api, "POST", "/calls", {"title": "No account"})[2]["session_id"]
    assert call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": plain_call})[0] == 409
    assert wood["opportunity_id"]   # linked, but nothing recorded
    assert call(intake_api, "GET", f"/intake/opportunities/{WOOD}/calls")[2]["calls"] == []


def test_errors(intake_api):
    unknown = "99999999-9999-4999-8999-999999999999"
    assert call(intake_api, "GET", f"/intake/opportunities/{unknown}")[0] == 404
    assert call(intake_api, "POST", f"/intake/opportunities/{unknown}/link")[0] == 404
    assert call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": "x"})[0] == 400
    assert call(intake_api, "POST", f"/intake/opportunities/{WOOD}/calls",
                {"session_id": "call-" + "a" * 40})[2]["error"]["code"] == "call_not_found"
    assert call(intake_api, "GET", "/intake/opportunities", key="wrong")[0] == 401


def test_disabled_without_intake_table(timeline, session_store, accounts, monkeypatch):
    api = _api(timeline, session_store, accounts, monkeypatch)
    status, _, out = call(api, "GET", "/intake/opportunities")
    assert status == 200 and out == {"enabled": False, "opportunities": []}
    assert call(api, "GET", f"/intake/opportunities/{WOOD}")[2]["error"]["code"] == "intake_not_configured"


# ---------------------------------------------------------------- Discovery agent integration
from app.discovery_link import DiscoveryUnavailable, engagement_name, summarise_report, transcript_text  # noqa: E402

REPORT = {"meta": {"generated_at": "2026-10-28T10:00:00Z"},
          "use_cases": [{"id": "UC-2", "rank": 2, "title": "Second", "category": "LATENT", "confidence": "low"},
                        {"id": "UC-1", "rank": 1, "title": "First", "category": "EXPLICIT", "confidence": "high"}],
          "top_hidden_opportunities": [{"use_case_id": "UC-2", "title": "Second", "why_it_matters": "x"}],
          "gaps_and_open_questions": [{"question": "Who owns approvals?", "status": "open"},
                                      {"question": "Resolved one", "status": "resolved"}]}


class FakeDiscovery:
    ui_url = "https://disc.example"

    def __init__(self):
        self.state, self.reports, self.fail = "running", 0, False

    def status(self, engagement, run_id):
        if self.fail:
            raise DiscoveryUnavailable("down")
        return {"state": self.state, "steps_done": 7 if self.state == "succeeded" else 3, "error": None,
                "updated_at": "2026-10-28T10:00:00Z"}

    def report(self, engagement, run_id):
        self.reports += 1
        return summarise_report(REPORT)

    def run_url(self, engagement, run_id):
        return f"{self.ui_url}/#run={engagement}/{run_id}"


@pytest.fixture
def disc_api(timeline, session_store, accounts, dynamodb_resource, intake_table, monkeypatch):
    fake = FakeDiscovery()
    feed = IntakeFeed(INTAKE_TABLE, resource=dynamodb_resource)
    links = IntakeLinkStore(accounts, table_name=TEST_TIMELINE_TABLE, resource=dynamodb_resource)
    api = _api(timeline, session_store, accounts, monkeypatch, feed, links)
    api.intake.discovery = fake
    return api, fake


def _turn(api, sid, speaker, name, text):
    assert call(api, "POST", f"/calls/{sid}/turns", {"speaker": speaker, "speaker_name": name, "text": text})[0] == 202


def test_export_has_intake_record_and_ended_call_transcripts(disc_api):
    api, _ = disc_api
    link = call(api, "POST", f"/intake/opportunities/{WOOD}/link")[2]
    ended = _start_call(api, link, "Discovery call")
    _turn(api, ended, "ba", "Ravi Menon", "How many VMs do you run?")
    _turn(api, ended, "cx", "Hannah Clarke", "About 400 VMs across two data centres.")
    call(api, "POST", f"/calls/{ended}/end", {})
    live = _start_call(api, link, "Still live")
    _turn(api, live, "cx", "Hannah Clarke", "Not finished yet.")
    for sid in (ended, live):
        call(api, "POST", f"/intake/opportunities/{WOOD}/calls", {"session_id": sid})

    status, _, out = call(api, "GET", f"/intake/opportunities/{WOOD}/export")
    assert status == 200 and out["engagement"] == "Woodgrove Bank Woodgrove - DC exit"
    assert out["call_ids"] == [ended]   # live calls are not exported
    names = [d["name"] for d in out["documents"]]
    assert names[0] == "00_intake_record.md" and names[1].startswith("01_") and names[1].endswith("_transcript.txt")
    intake = out["documents"][0]["text"]
    assert "Program: MAP" in intake and "Are VMs in scope?" in intake and "Workload: Migration" in intake
    lines = out["documents"][1]["text"].splitlines()
    assert lines[:3] == ["Ravi Menon", "0:00", "How many VMs do you run?"] and lines[3] == "Hannah Clarke"


def test_transcript_format_is_what_discovery_detects():
    import re
    view = {"turns": [{"speaker": "cx", "speaker_name": f"P{i}", "text": f"line {i}",
                       "spoken_at": f"2026-10-28T10:{i:02d}:05Z"} for i in range(12)]}
    text = transcript_text(view)
    assert len(re.findall(r"^\d+:\d{2}(:\d{2})?$", text, re.M)) == 12   # Discovery: >= 10 timestamps -> transcript
    assert text.splitlines()[-2] == "11:00"


def test_discovery_run_registered_and_summary_cached(disc_api):
    api, fake = disc_api
    bad = call(api, "POST", f"/intake/opportunities/{WOOD}/discovery", {"engagement": "../x", "run_id": "r"})
    assert bad[0] == 400
    body = {"engagement": "Woodgrove Bank", "run_id": "2026-10-28T10-00-00Z-abc123", "call_ids": ["nope"]}
    status, _, run = call(api, "POST", f"/intake/opportunities/{WOOD}/discovery", body)
    assert status == 201 and run["state"] == "starting" and run["call_ids"] == []
    assert call(api, "POST", f"/intake/opportunities/{WOOD}/discovery", body)[0] == 201   # idempotent

    runs = call(api, "GET", f"/intake/opportunities/{WOOD}/discovery")[2]["runs"]
    assert len(runs) == 1 and runs[0]["state"] == "running" and runs[0]["url"].startswith("https://disc.example")
    fake.state = "succeeded"
    runs = call(api, "GET", f"/intake/opportunities/{WOOD}/discovery")[2]["runs"]
    s = runs[0]["summary"]
    assert runs[0]["state"] == "succeeded" and [u["id"] for u in s["use_cases"]] == ["UC-1", "UC-2"]
    assert s["open_questions"] == ["Who owns approvals?"] and s["open_question_count"] == 1
    call(api, "GET", f"/intake/opportunities/{WOOD}/discovery")
    assert fake.reports == 1   # cached once finished

    fake.fail = True   # Discovery down: the cached summary is still served
    assert call(api, "GET", f"/intake/opportunities/{WOOD}/discovery")[2]["runs"][0]["summary"]["use_case_count"] == 2


def test_discovery_down_while_running_is_marked_stale(disc_api):
    api, fake = disc_api
    call(api, "POST", f"/intake/opportunities/{NORTH}/discovery", {"engagement": "Northwind", "run_id": "r-1"})
    fake.fail = True
    run = call(api, "GET", f"/intake/opportunities/{NORTH}/discovery")[2]["runs"][0]
    assert run["stale"] is True and run["state"] == "starting"


def test_engagement_name_is_safe():
    assert engagement_name("Woodgrove Bank - data centre exit", "Woodgrove Bank") == "Woodgrove Bank - data centre exit"
    assert engagement_name("ERP / move (phase 2)", "A. Datum") == "A. Datum ERP move phase 2"
    assert len(engagement_name("x" * 300, "")) == 101
