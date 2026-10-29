"""Every row of the AWS Funding Programs Decision Framework, its boundaries, unknown-fact handling, the form schema
and the Dataverse client (against a local mock of the Dataverse Web API). No AWS calls.

    cd deployment && python3 -m pytest tests -q
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "shared" / "python")]

from funding.rules import decide, resolve_arr  # noqa: E402
from funding import facts as F  # noqa: E402
import opportunity_form as form  # noqa: E402


def d(**facts):
    return decide(facts)


# ---------------------------------------------------------------- migration table (PDF page 2)
@pytest.mark.parametrize("segment,mrr,vms,program,rule", [
    ("non_startup", 50_000, None, "MAP", "non_startup.migration.above_500k"),
    ("non_startup", 20_000, None, "MAP Lite", "non_startup.migration.100k_500k"),
    ("non_startup", 5_000, None, "Promotional credits", "non_startup.migration.below_100k"),
    ("startup", 50_000, None, "MAP", "startup.migration.above_500k"),
    ("startup", 30_000, None, "MAP Lite", "startup.migration.264k_500k"),
    ("startup", 15_000, True, "MAP Lite", "startup.migration.100k_264k.vms"),
    ("startup", 15_000, False, "IW Migrate", "startup.migration.100k_264k.no_vms"),
    ("startup", 5_000, None, "IW Migrate", "startup.migration.below_100k"),
])
def test_migration_table(segment, mrr, vms, program, rule):
    r = d(segment=segment, workload="migration", mrr_usd=mrr, vms=vms)
    assert r["program"] == program and r["rule"] == rule


def test_map_lite_amounts_are_25_percent_split_5_and_20():
    r = d(segment="non_startup", workload="migration", mrr_usd=20_000)    # ARR 240k
    assert [(c["name"], c["amount_usd"]) for c in r["components"]] == [("MAP Lite Assess", 12_000), ("MAP Lite Mobilize", 48_000)]
    assert r["total_usd"] == 60_000 and r["arr_usd"] == 240_000


def test_non_startup_below_100k_gets_promotional_credits():
    r = d(segment="non_startup", workload="migration", arr_usd=80_000)
    assert r["status"] == "decided" and r["program"] == "Promotional credits" and r["assumptions"] == []


# ---------------------------------------------------------------- boundaries: "above" is strict, bands include the top
@pytest.mark.parametrize("arr,rule", [
    (500_000, "startup.migration.264k_500k"), (500_001, "startup.migration.above_500k"),
    (264_000, "startup.migration.100k_264k.no_vms"), (264_001, "startup.migration.264k_500k"),
    (100_000, "startup.migration.below_100k"), (100_001, "startup.migration.100k_264k.no_vms"),
])
def test_startup_boundaries(arr, rule):
    assert d(segment="startup", workload="migration", arr_usd=arr, vms=False)["rule"] == rule


def test_mrr_thresholds_from_pdf():
    # PDF: MAP above 41,666 MRR; IW Migrate ceiling 22,000 MRR; MAP Lite floor above 8,333 MRR
    assert d(segment="startup", workload="migration", mrr_usd=41_667)["program"] == "MAP"
    assert d(segment="startup", workload="migration", mrr_usd=41_666)["program"] == "MAP Lite"
    assert d(segment="startup", workload="migration", mrr_usd=22_000, vms=False)["program"] == "IW Migrate"
    assert d(segment="non_startup", workload="migration", mrr_usd=8_334)["program"] == "MAP Lite"
    assert d(segment="non_startup", workload="migration", mrr_usd=8_333)["program"] == "Promotional credits"


# ---------------------------------------------------------------- Gen AI table
def test_genai():
    assert d(segment="non_startup", workload="genai", genai_stage="poc", arr_usd=300_000)["total_usd"] == 30_000
    assert d(segment="non_startup", workload="genai", genai_stage="production", arr_usd=300_000)["total_usd"] == 45_000
    fresh = d(segment="startup", workload="genai", genai_type="fresh", arr_usd=100_000)
    assert fresh["program"] == "IW Assess, then IW Build" and fresh["total_usd"] == 30_000
    mod = d(segment="startup", workload="genai", genai_type="modernization")
    assert mod["program"] == "POC only" and mod["status"] == "decided"


def test_genai_percent_without_spend_is_decided_with_spend_assumption():
    r = d(segment="non_startup", workload="genai", genai_stage="poc")
    assert r["status"] == "decided" and r["program"] == "POC" and r["total_usd"] is None
    assert [a["fact"] for a in r["assumptions"]] == ["spend"]


def test_genai_unknown_stage_assumes_poc():
    r = d(segment="non_startup", workload="genai", arr_usd=200_000)
    assert r["status"] == "decided" and r["program"] == "POC" and r["total_usd"] == 20_000
    assert [a["fact"] for a in r["assumptions"]] == ["genai_stage"]


def test_startup_genai_unknown_type_assumes_fresh():
    r = d(segment="startup", workload="genai", arr_usd=100_000)
    assert r["program"] == "IW Assess, then IW Build" and [a["fact"] for a in r["assumptions"]] == ["genai_type"]


# ---------------------------------------------------------------- special tracks and mapped workloads
def test_isv_gets_promotional_credits_without_segment():
    r = d(workload="isv_migration")
    assert (r["status"], r["program"], r["assumptions"]) == ("decided", "Promotional credits", [])


@pytest.mark.parametrize("workload,mrr,program", [
    ("sap_migration", 50_000, "MAP"), ("oracle_migration", 35_000, "MAP Lite"), ("other", 2_500, "Promotional credits"),
])
def test_sap_oracle_other_are_mapped_to_the_closest_programme(workload, mrr, program):
    r = d(segment="non_startup", workload=workload, mrr_usd=mrr)
    assert r["status"] == "decided" and r["program"] == program and r["assumptions"] == []
    assert r["rule"].startswith(workload)


def test_other_without_spend_gets_promotional_credits():
    r = d(segment="non_startup", workload="other")
    assert r["program"] == "Promotional credits" and [a["fact"] for a in r["assumptions"]] == ["spend"]


# ---------------------------------------------------------------- unknown facts: best-fit assumptions, never a blocked status
def test_nothing_known_still_maps_to_a_programme():
    r = d()
    assert r["status"] == "decided" and r["program"] == "Promotional credits"
    assert [a["fact"] for a in r["assumptions"]] == ["workload", "segment", "spend"] and r["missing"] == []


def test_unknown_workload_with_genai_hints_is_genai():
    r = d(segment="non_startup", genai_stage="production", arr_usd=200_000)
    assert r["program"] == "Production funding" and r["assumptions"][0]["fact"] == "workload"


def test_unknown_segment_same_outcome_has_no_alternatives():
    r = d(workload="migration", mrr_usd=50_000)
    assert r["status"] == "decided" and r["program"] == "MAP" and r["alternatives"] == []


def test_unknown_segment_assumes_non_startup_and_shows_startup_alternative():
    r = d(workload="migration", mrr_usd=15_000)
    assert r["status"] == "decided" and r["program"] == "MAP Lite"
    assert [a["fact"] for a in r["assumptions"]] == ["segment"]
    assert [(a["when"], a["program"]) for a in r["alternatives"]] == [("Startup", "IW Migrate")]


def test_startup_100k_264k_unknown_vms_follows_the_source():
    onprem = d(segment="startup", workload="migration", mrr_usd=15_000, migration_source="on_premises")
    cloud = d(segment="startup", workload="migration", mrr_usd=15_000, migration_source="other_cloud")
    assert (onprem["program"], cloud["program"]) == ("MAP Lite", "IW Migrate")
    assert [a["fact"] for a in onprem["assumptions"]] == ["vms"]


def test_migration_without_spend_gets_promotional_credits():
    r = d(segment="startup", workload="migration")
    assert r["status"] == "decided" and r["program"] == "Promotional credits"
    assert [a["fact"] for a in r["assumptions"]] == ["spend"]


@pytest.mark.parametrize("facts", [
    {}, {"workload": "other"}, {"workload": "sap_migration"}, {"segment": "startup", "workload": "genai"},
    {"segment": "non_startup", "workload": "migration", "arr_usd": 1}, {"workload": "isv_migration"},
])
def test_status_is_always_decided(facts):
    r = d(**facts)
    assert r["status"] == "decided" and r["program"] and r["missing"] == []


def test_mrr_wins_over_arr_and_flags_mismatch():
    arr, how, notes = resolve_arr(10_000, 500_000)
    assert arr == 120_000 and "MRR" in how and notes
    assert resolve_arr(None, 240_000)[0] == 240_000
    assert resolve_arr(10_000, 120_000)[2] == []


# ---------------------------------------------------------------- facts: form + verified model facts
LABELS = {k: v["label"] for k, v in form.FIELDS.items()}


def test_form_facts():
    got = F.from_form({"fund_segment": "Startup", "aws_opportunity_type": "Migration", "source_environment": "VMware",
                       "fund_vms": "Yes", "estimated_arr": "$350k"}, LABELS)
    assert {k: v["value"] for k, v in got.items()} == {"segment": "startup", "workload": "migration",
                                                     "migration_source": "on_premises", "vms": True, "arr_usd": 350_000}


def test_fund_workload_beats_aws_opportunity_type_and_not_sure_is_unknown():
    got = F.from_form({"fund_workload": "SAP migration", "aws_opportunity_type": "Migration", "fund_segment": "Not sure"}, LABELS)
    assert got["workload"]["value"] == "sap_migration" and "segment" not in got


def test_merge_form_wins_unverified_model_fact_not_used():
    form_facts = {"segment": {"value": "startup", "source": "form", "field": "x", "evidence": "Startup", "verified": True}}
    agent = {"segment": {"value": "non_startup", "source": "agent", "field": "t", "evidence": "global bank", "verified": True},
             "workload": {"value": "genai", "source": "agent", "field": "t", "evidence": "made up", "verified": False},
             "mrr_usd": {"value": 25_000.0, "source": "agent", "field": "t", "evidence": "25,000 USD per month", "verified": True}}
    facts, rows, conflicts = F.merge(form_facts, agent)
    assert facts["segment"] == "startup" and facts["workload"] is None and facts["mrr_usd"] == 25_000
    assert len(conflicts) == 1
    wl = next(r for r in rows if r["fact"] == "workload")
    assert not wl["used"] and wl["suggested"] == "Gen AI project"


@pytest.mark.parametrize("text,value", [
    ("350,000", 350_000), ("$350k", 350_000), ("1.2M", 1_200_000), ("USD 45000", 45_000), ("350.000", 350_000),
    (12000, 12_000), ("", None), ("between 100k and 200k", None), ("0", None),
])
def test_parse_amount(text, value):
    assert form.parse_amount(text) == value


# ---------------------------------------------------------------- form schema
def test_form_covers_every_xlsx_question():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(ROOT.parent / "Form input questions .xlsx")
    norm = lambda s: " ".join(str(s).replace("\xa0", " ").split()).lower()  # noqa: E731
    questions = [norm(r[0]) for r in wb.active.iter_rows(values_only=True) if r[0]]
    skipped = {"id", "start time", "completion time", "email", "name", "(optional) fileupload1", "(optional) fileupload"}
    labels = {norm(f["label"]) for f in form.FIELDS.values()}
    missing = [q for q in questions if q not in skipped and q not in labels]
    assert not missing, missing


def test_validate_keeps_visible_answers_only_and_types_numbers():
    a = form.validate({"request_type": "Cloud Services Opportunity", "company_name": "P", "ec_name": "C",
                       "cs_vendor": "AWS", "aws_opportunity_type": "Migration", "ms_opportunity_type": "hidden",
                       "fund_mrr_usd": "15,000", "copilot_licenses": "5", "unknown_field": "x"})
    assert a["fund_mrr_usd"] == 15_000 and "ms_opportunity_type" not in a and "copilot_licenses" not in a
    assert "unknown_field" not in a


def test_validate_errors():
    with pytest.raises(form.FormError) as e:
        form.validate({"request_type": "Nope", "partner_contact_email": "bad"})
    assert set(e.value.errors) == {"request_type", "company_name", "ec_name", "partner_contact_email"}


# ---------------------------------------------------------------- Dataverse client against a mock Web API
class MockDataverse(BaseHTTPRequestHandler):
    store: dict = {}
    calls: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/oauth2/v2.0/token"):   # form-encoded client-credentials request
            self.calls.append(("POST", self.path, raw.decode(), None))
            return self._send(200, {"access_token": "tok", "expires_in": 3600})
        body = json.loads(raw)
        self.calls.append(("POST", self.path, body, self.headers.get("Authorization")))
        entity = self.path.rsplit("/", 1)[1]
        new_id = {"accounts": "11111111-1111-1111-1111-111111111111", "opportunities": "22222222-2222-2222-2222-222222222222",
                  "annotations": "33333333-3333-3333-3333-333333333333"}[entity]
        self.store.setdefault(entity, []).append({**body, "id": new_id})
        self._send(204, headers={"OData-EntityId": f"http://x/api/data/v9.2/{entity}({new_id})"})

    def do_GET(self):
        self.calls.append(("GET", self.path, None, self.headers.get("Authorization")))
        if self.path.startswith("/api/data/v9.2/accounts"):
            return self._send(200, {"value": []})
        if self.path.startswith("/api/data/v9.2/opportunities(22222222"):
            opp = self.store["opportunities"][0]
            return self._send(200, {"name": opp["name"], "description": opp["description"], "estimatedvalue": opp.get("estimatedvalue"),
                                    "_customerid_value@OData.Community.Display.V1.FormattedValue": "Tailwind Robotics"})
        if self.path.startswith("/api/data/v9.2/opportunities("):
            return self._send(404, {"error": {"message": "Does not exist"}})
        if self.path.startswith("/api/data/v9.2/annotations"):
            return self._send(200, {"value": [{"subject": n["subject"], "notetext": n["notetext"]}
                                              for n in reversed(self.store.get("annotations", []))]})
        if self.path.startswith("/api/data/v9.2/opportunities"):
            return self._send(200, {"value": [{"opportunityid": "22222222-2222-2222-2222-222222222222", "name": "x"}]})
        self._send(404, {})


@pytest.fixture()
def dataverse(monkeypatch):
    import dynamics
    import urllib.request

    MockDataverse.store, MockDataverse.calls = {}, []
    server = HTTPServer(("127.0.0.1", 0), MockDataverse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    real = urllib.request.urlopen

    def urlopen(req, timeout=None):   # route https://org.crm.dynamics.com and login.microsoftonline.com to the mock
        url = req.full_url.replace("https://org.crm.dynamics.com", base).replace("https://login.microsoftonline.com/t", base)
        new = urllib.request.Request(url, data=req.data, headers=dict(req.header_items()), method=req.get_method())
        return real(new, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(dynamics, "_client_secret", lambda: "secret")
    monkeypatch.setenv("DYNAMICS_URL", "https://org.crm.dynamics.com/")
    monkeypatch.setenv("DYNAMICS_TENANT_ID", "t")
    monkeypatch.setenv("DYNAMICS_CLIENT_ID", "c")
    dynamics._token.clear()
    yield dynamics
    server.shutdown()


def test_dataverse_create_get_note(dataverse, monkeypatch):
    monkeypatch.setenv("DYNAMICS_FORM_STORAGE", "note")   # environments without the ingr_ columns
    answers = {"request_type": "Cloud Services Opportunity", "ec_name": "Tailwind Robotics", "fund_mrr_usd": 15000}
    created = dataverse.create_opportunity({"name": "Tailwind VMware exit", "customer": "Tailwind Robotics",
                                            "description": "35 VMs", "estimated_value": 180000}, answers)
    assert created["id"] == "22222222-2222-2222-2222-222222222222" and created["source"] == "dynamics"
    assert "etn=opportunity" in created["url"]
    opp = MockDataverse.store["opportunities"][0]
    assert opp["customerid_account@odata.bind"] == "/accounts(11111111-1111-1111-1111-111111111111)"
    assert opp["estimatedvalue"] == 180000.0
    note = MockDataverse.store["annotations"][0]
    assert note["subject"] == dataverse.ANSWERS_SUBJECT and json.loads(note["notetext"]) == answers
    assert note["objectid_opportunity@odata.bind"] == f"/opportunities({created['id']})"

    got = dataverse.get_opportunity(created["id"])
    assert got["answers"] == answers and got["customer"] == "Tailwind Robotics" and got["notes"] == []

    dataverse.write_decision(created["id"], "MAP Lite", "details")
    assert MockDataverse.store["annotations"][-1]["subject"] == "AWS funding check: MAP Lite"
    # the decision note is not fed back to the agent as an opportunity note
    assert dataverse.get_opportunity(created["id"])["notes"] == []
    # one token request for all calls; every Dataverse call is authorised
    assert sum(1 for c in MockDataverse.calls if c[1].endswith("/token")) == 1
    assert all(c[3] == "Bearer tok" for c in MockDataverse.calls if "/api/data/" in c[1])


def test_dataverse_errors_and_search(dataverse):
    with pytest.raises(dataverse.DynamicsError) as e:
        dataverse.get_opportunity("99999999-9999-9999-9999-999999999999")
    assert e.value.status == 404
    with pytest.raises(dataverse.DynamicsError):
        dataverse.get_opportunity("not-a-guid")
    assert dataverse.search_opportunities("O'Brien")[0]["source"] == "dynamics"
    search = next(c[1] for c in MockDataverse.calls if c[1].startswith("/api/data/v9.2/opportunities?"))
    assert "contains(name,'O''Brien')" in search and "%20desc" in search   # OData quoting, spaces encoded
