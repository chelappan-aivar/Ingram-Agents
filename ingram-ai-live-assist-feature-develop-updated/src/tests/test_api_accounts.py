"""API routes for accounts, opportunities, account calls and long-term memory."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main
from api.app import Api, Services
from app.memory import facts_namespace
from tests.test_api import BUCKET, EntryPointSender, call

KEY = "test-access-key"


def _api(timeline, session_store, accounts, memory, monkeypatch):
    monkeypatch.setattr(main, "get_default_timeline", lambda: timeline)
    monkeypatch.setattr(main, "get_processor", lambda: SimpleNamespace(schedule=lambda _sid, **_kw: None))
    return Api(Services(
        timeline=timeline, store=session_store, sender=EntryPointSender(), s3=None, bucket=BUCKET,
        start_source=lambda job: None, access_key=lambda: KEY, accounts=accounts, memory=memory,
    ))


@pytest.fixture
def app_api(timeline, session_store, accounts, memory, monkeypatch):
    return _api(timeline, session_store, accounts, memory, monkeypatch)


@pytest.fixture
def no_memory_api(timeline, session_store, accounts, monkeypatch):
    return _api(timeline, session_store, accounts, None, monkeypatch)


def _account(api, name="Acme Corp"):
    status, _, out = call(api, "POST", "/accounts", {"name": name})
    assert status == 201, out
    return out["account_id"]


def _opportunity(api, account_id, name="ERP move", opportunity_type=None):
    status, _, out = call(api, "POST", f"/accounts/{account_id}/opportunities",
                          {"name": name, "opportunity_type": opportunity_type})
    assert status == 201, out
    return out["opportunity_id"]


def test_create_list_and_get_accounts(app_api):
    acme = _account(app_api)
    erp = _opportunity(app_api, acme, opportunity_type="cloud_migration")

    out = call(app_api, "GET", "/accounts")[2]
    assert [a["account_id"] for a in out["accounts"]] == [acme]
    out = call(app_api, "GET", f"/accounts/{acme}")[2]
    assert out["name"] == "Acme Corp" and out["opportunities"][0]["opportunity_id"] == erp
    assert call(app_api, "GET", f"/accounts/{acme}/opportunities")[2]["opportunities"][0]["opportunity_type"] \
        == "cloud_migration"


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 101}, {"name": 5}])
def test_account_name_validated(app_api, body):
    assert call(app_api, "POST", "/accounts", body)[0] == 400


def test_opportunity_type_must_be_a_type_id(app_api):
    # Whether the type exists is checked against the call's config when a call starts.
    acme = _account(app_api)
    for bad in ("Not Valid", "x", 5):
        status, _, _ = call(app_api, "POST", f"/accounts/{acme}/opportunities", {"name": "x", "opportunity_type": bad})
        assert status == 400, bad
    assert call(app_api, "POST", f"/accounts/{acme}/opportunities",
                {"name": "x", "opportunity_type": "custom_type"})[0] == 201


def test_unknown_account_is_404(app_api):
    for method, path in (("GET", "/accounts/missing-1"), ("GET", "/accounts/missing-1/calls"),
                         ("GET", "/accounts/missing-1/memory"), ("DELETE", "/accounts/missing-1/memory"),
                         ("POST", "/accounts/missing-1/opportunities")):
        assert call(app_api, method, path, {"name": "x"} if method == "POST" else None)[0] == 404, path


def test_call_with_account_and_opportunity(app_api, started):
    acme = _account(app_api)
    erp = _opportunity(app_api, acme, opportunity_type="cloud_migration")

    status, _, out = call(app_api, "POST", "/calls", {"account_id": acme, "opportunity_id": erp})
    assert status == 201 and (out["account_id"], out["opportunity_id"]) == (acme, erp)
    start = started[-1]
    assert (start.account_id, start.opportunity_id, start.preset_opportunity_type) == (acme, erp, "cloud_migration")

    view = call(app_api, "GET", f"/calls/{out['session_id']}")[2]
    assert (view["account_id"], view["opportunity_id"]) == (acme, erp)
    calls = call(app_api, "GET", f"/accounts/{acme}/calls", query={"opportunity_id": erp})[2]["calls"]
    assert [c["session_id"] for c in calls] == [out["session_id"]]
    assert call(app_api, "GET", "/calls")[2]["calls"][0]["account_id"] == acme


@pytest.mark.parametrize("make_body", [
    lambda acme, other_opp: {"account_id": "missing-1"},
    lambda acme, other_opp: {"opportunity_id": other_opp},
    lambda acme, other_opp: {"account_id": acme, "opportunity_id": other_opp},  # Belongs to another account.
    lambda acme, other_opp: {"account_id": "Bad Id"},
])
def test_call_scope_validated(app_api, make_body):
    acme = _account(app_api)
    other_opp = _opportunity(app_api, _account(app_api, "Globex"))
    assert call(app_api, "POST", "/calls", make_body(acme, other_opp))[0] == 400


def test_call_without_account_is_unchanged(app_api, started):
    status, _, out = call(app_api, "POST", "/calls", {})
    assert status == 201 and out["account_id"] is None and started[-1].account_id is None


def test_memory_view_and_erase(app_api, memory_client):
    acme = _account(app_api)
    erp = _opportunity(app_api, acme)
    memory_client.add_record(facts_namespace(f"acct-{acme}"), "Acme runs VMware")
    memory_client.add_record(facts_namespace(f"opp-{acme}--{erp}"), "ERP by June")

    out = call(app_api, "GET", f"/accounts/{acme}/memory", query={"opportunity_id": erp})[2]
    assert out["enabled"] and [f["text"] for f in out["account"]] == ["Acme runs VMware"]
    assert [f["text"] for f in out["opportunity"]] == ["ERP by June"]

    status, _, out = call(app_api, "DELETE", f"/accounts/{acme}/memory", query={"opportunity_id": erp})
    assert status == 200 and out["deleted"] == 1 and out["forgotten_at"]
    assert call(app_api, "DELETE", f"/accounts/{acme}/memory")[2]["deleted"] == 1
    assert memory_client.records == {}
    assert call(app_api, "GET", f"/accounts/{acme}/memory")[2]["forgotten_at"]


def test_memory_errors(app_api, no_memory_api, memory_client):
    acme = _account(app_api)
    assert call(app_api, "GET", f"/accounts/{acme}/memory", query={"opportunity_id": "nope-1"})[0] == 404
    memory_client.fail = True
    assert call(app_api, "GET", f"/accounts/{acme}/memory")[0] == 502
    assert call(no_memory_api, "GET", f"/accounts/{acme}/memory")[2]["enabled"] is False
    # Without long-term memory, Forget still stops earlier calls from feeding new ones.
    status, _, out = call(no_memory_api, "DELETE", f"/accounts/{acme}/memory")
    assert status == 200 and out["deleted"] == 0 and out["forgotten_at"]


def test_account_routes_need_the_key(app_api):
    acme = _account(app_api)
    for method, path in (("POST", "/accounts"), ("GET", "/accounts"), ("GET", f"/accounts/{acme}"),
                         ("GET", f"/accounts/{acme}/memory"), ("DELETE", f"/accounts/{acme}/memory")):
        assert call(app_api, method, path, key=None)[0] == 401, path
        assert call(app_api, method, path, key="wrong")[0] == 401, path


def test_options_lists_memory_settings(app_api):
    out = call(app_api, "GET", "/options")[2]
    assert out["memory"] == {"enabled": True}
    assert out["assist_config"]["use_prior_context"] == ["off", "confirm", "skip"]
    assert "cloud_migration" in out["opportunity_types"]
