"""API routes for config profiles, and picking a profile when starting a call."""

from __future__ import annotations

import copy

import pytest

from app.config_store import ConfigCatalog, ConfigStore
from app.guidance import BUILTIN_PROFILE_ID
from tests.conftest import TEST_TIMELINE_TABLE
from tests.test_api_accounts import _account, _api, _opportunity
from tests.test_api import call
from tests.test_configs import PROFILE


@pytest.fixture
def cfg_api(timeline, session_store, accounts, dynamodb_resource, monkeypatch):
    import main

    catalog = ConfigCatalog(ConfigStore(table_name=TEST_TIMELINE_TABLE, resource=dynamodb_resource))
    monkeypatch.setattr(main, "get_configs", lambda: catalog)  # The runtime resolves the same profiles.
    api = _api(timeline, session_store, accounts, None, monkeypatch)
    api.configs.catalog = catalog
    return api


def _create(api, body=None):
    status, _, out = call(api, "POST", "/configs", body or copy.deepcopy(PROFILE))
    assert status == 201, out
    return out


def test_list_starts_with_the_builtin_profile(cfg_api):
    configs = call(cfg_api, "GET", "/configs")[2]["configs"]
    assert configs[0]["config_id"] == BUILTIN_PROFILE_ID and configs[0]["builtin"]
    body = call(cfg_api, "GET", f"/configs/{BUILTIN_PROFILE_ID}")[2]["body"]
    assert "business_goal" in [f["id"] for f in body["general"]["fields"]]


def test_schema_and_validate(cfg_api):
    assert "properties" in call(cfg_api, "GET", "/configs/schema")[2]
    assert call(cfg_api, "POST", "/configs/validate", PROFILE)[2] == {"valid": True, "errors": []}
    bad = copy.deepcopy(PROFILE)
    bad["general"]["fields"][0]["id"] = "Bad Id"
    out = call(cfg_api, "POST", "/configs/validate", bad)[2]
    assert out["valid"] is False and out["errors"][0]["path"] == "general.fields.0.id"


def test_create_rejects_invalid_profiles_with_details(cfg_api):
    bad = copy.deepcopy(PROFILE)
    del bad["general"]
    status, _, out = call(cfg_api, "POST", "/configs", bad)
    assert status == 400 and out["error"]["code"] == "invalid_config"
    assert out["error"]["details"][0]["path"] == "general"


def test_clone_edit_and_conflict(cfg_api):
    clone = _create(cfg_api, {"from_id": BUILTIN_PROFILE_ID, "name": "My checklist"})
    cid = clone["config_id"]
    assert clone["name"] == "My checklist" and clone["version"] == 1 and not clone["builtin"]

    body = clone["body"]
    body["general"]["fields"].append({"id": "sla", "label": "SLA needs"})
    status, _, out = call(cfg_api, "PUT", f"/configs/{cid}", {**body, "expected_version": 1})
    assert status == 200 and out["version"] == 2

    status, _, out = call(cfg_api, "PUT", f"/configs/{cid}", {**body, "expected_version": 1})
    assert status == 409 and out["error"]["code"] == "version_conflict"
    assert call(cfg_api, "PUT", f"/configs/{cid}", body)[0] == 400  # expected_version is required.
    versions = call(cfg_api, "GET", f"/configs/{cid}/versions")[2]["versions"]
    assert [v["version"] for v in versions] == [2, 1]
    assert call(cfg_api, "GET", f"/configs/{cid}", query={"version": "1"})[2]["version"] == 1


def test_builtin_is_read_only_and_archive(cfg_api):
    assert call(cfg_api, "PUT", f"/configs/{BUILTIN_PROFILE_ID}", {**PROFILE, "expected_version": 0})[0] == 403
    assert call(cfg_api, "DELETE", f"/configs/{BUILTIN_PROFILE_ID}")[0] == 403
    cid = _create(cfg_api)["config_id"]
    assert call(cfg_api, "DELETE", f"/configs/{cid}")[2] == {"config_id": cid, "archived": True}
    assert cid not in [c["config_id"] for c in call(cfg_api, "GET", "/configs")[2]["configs"]]
    assert call(cfg_api, "POST", "/calls", {"config_id": cid})[0] == 400  # Archived: no new calls.
    assert call(cfg_api, "GET", "/configs/missing-1a2b3c")[0] == 404


def test_call_uses_the_chosen_profile(cfg_api, started):
    created = _create(cfg_api)
    status, _, out = call(cfg_api, "POST", "/calls", {"config_id": created["config_id"]})
    assert status == 201 and (out["config_id"], out["config_version"]) == (created["config_id"], 1)
    assert started[-1].config_id == created["config_id"]

    view = call(cfg_api, "GET", f"/calls/{out['session_id']}")[2]
    assert (view["config_id"], view["config_version"]) == (created["config_id"], 1)
    assert call(cfg_api, "GET", "/calls")[2]["calls"][0]["config_id"] == created["config_id"]

    default = call(cfg_api, "POST", "/calls", {})[2]
    assert (default["config_id"], default["config_version"]) == (BUILTIN_PROFILE_ID, 0)


def test_call_config_errors(cfg_api):
    assert call(cfg_api, "POST", "/calls", {"config_id": "missing-1a2b3c"})[2]["error"]["code"] == "unknown_config"
    assert call(cfg_api, "POST", "/calls", {"config_id": "Bad"})[0] == 400
    created = _create(cfg_api)
    assert call(cfg_api, "POST", "/calls", {"config_id": created["config_id"], "config_version": 9})[0] == 400

    # An opportunity whose preset type the chosen profile doesn't have.
    acme = _account(cfg_api)
    erp = _opportunity(cfg_api, acme, opportunity_type="cloud_migration")
    status, _, out = call(cfg_api, "POST", "/calls",
                          {"config_id": created["config_id"], "account_id": acme, "opportunity_id": erp})
    assert status == 400 and out["error"]["code"] == "opportunity_type_not_in_config"


def test_options_follow_the_profile(cfg_api):
    created = _create(cfg_api)
    out = call(cfg_api, "GET", "/options", query={"config_id": created["config_id"]})[2]
    assert [t["field_id"] for t in out["topics"]] == ["business_goal", "soc_tooling"]
    assert out["topics"][1]["memory_scope"] == "account"
    assert out["opportunity_types"] == ["mdr"] and out["config"]["version"] == 1
    assert call(cfg_api, "GET", "/options")[2]["config"]["config_id"] == BUILTIN_PROFILE_ID
    assert call(cfg_api, "GET", "/options", query={"config_id": "missing-1a2b3c"})[0] == 404


def test_config_routes_need_the_key(cfg_api):
    for method, path in (("GET", "/configs"), ("POST", "/configs"), ("GET", "/configs/schema"),
                         ("PUT", f"/configs/{BUILTIN_PROFILE_ID}"), ("DELETE", f"/configs/{BUILTIN_PROFILE_ID}")):
        assert call(cfg_api, method, path, key=None)[0] == 401, path
