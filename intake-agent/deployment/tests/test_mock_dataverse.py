"""The real Dataverse client (shared/python/dynamics.py) against the mock Dynamics (mock-dataverse/app.py), over HTTP.

The mock's Lambda handler is served by a local HTTP server that builds API Gateway v2 events, with in-memory
tables in place of DynamoDB. Then the seed data is loaded exactly as scripts/seed_dynamics.py does, and the funding
facts and rules run on what comes back. No AWS calls.
"""

import json
import sys
import threading
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "shared" / "python"), str(ROOT / "mock-dataverse"), str(ROOT / "scripts")]

SECRET = "s3cret-for-tests"
CLIENT = "a11ce000-0000-4000-8000-00000000d365"


class MemTable:
    def __init__(self, key):
        self.key, self.items = key, {}

    def put_item(self, Item):
        self.items[Item[self.key]] = Item

    def delete_item(self, Key):
        self.items.pop(Key[self.key], None)

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": item} if item else {}

    def scan(self, FilterExpression=None, **_):
        items = list(self.items.values())
        if FilterExpression is not None:   # Attr(x).eq(v)
            name, value = FilterExpression.get_expression()["values"]
            items = [i for i in items if i.get(name.name) == value]
        return {"Items": items}


@pytest.fixture(scope="module")
def mock(tmp_path_factory):
    import os
    os.environ.update(ACCOUNTS_TABLE="a", OPPORTUNITIES_TABLE="o", ANNOTATIONS_TABLE="n", CLIENT_ID=CLIENT,
                      CLIENT_SECRET_ARN="arn:test")
    import boto3
    real = boto3.resource
    tables = {"a": MemTable("accountid"), "o": MemTable("opportunityid"), "n": MemTable("annotationid")}
    boto3.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda name: tables[name])
    import app
    boto3.resource = real
    app._secret_cache["v"] = SECRET

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            parsed = urllib.parse.urlsplit(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            event = {"rawPath": parsed.path, "rawQueryString": parsed.query,
                     "headers": {k.lower(): v for k, v in self.headers.items()},
                     "body": self.rfile.read(n).decode() if n else None, "isBase64Encoded": False,
                     "requestContext": {"http": {"method": method}}}
            out = app.handler(event, None)
            raw = (out.get("body") or "").encode()
            self.send_response(out["statusCode"])
            for k, v in out["headers"].items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

        def do_DELETE(self):
            self._do("DELETE")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", tables
    server.shutdown()


@pytest.fixture()
def client(mock, monkeypatch):
    base, _ = mock
    import dynamics
    import urllib.request
    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: real(
        urllib.request.Request(req.full_url.replace("https://mock.example", base), data=req.data,
                               headers=dict(req.header_items()), method=req.get_method()), timeout=timeout))
    monkeypatch.setenv("DYNAMICS_URL", "https://mock.example")
    monkeypatch.setenv("DYNAMICS_TOKEN_URL", "https://mock.example/oauth2/v2.0/token")
    monkeypatch.setenv("DYNAMICS_CLIENT_ID", CLIENT)
    monkeypatch.setenv("DYNAMICS_MOCK", "true")
    monkeypatch.setattr(dynamics, "_client_secret", lambda: SECRET)
    dynamics._token.clear()
    return dynamics


def seed(dynamics):
    from dynamics_seed_data import load
    return load(dynamics, log=lambda *_: None)


def test_wrong_secret_is_refused(client, monkeypatch):
    monkeypatch.setattr(client, "_client_secret", lambda: "wrong")
    with pytest.raises(client.DynamicsError, match="Invalid client secret"):
        client._access_token()


def test_bad_token_is_refused(client, mock):
    base, _ = mock
    import urllib.request, urllib.error
    req = urllib.request.Request(f"{base}/api/data/v9.2/opportunities", headers={"Authorization": "Bearer x.y"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 401


def test_seed_list_get_and_decide(client):
    from dynamics_seed_data import OPPORTUNITIES
    seed(client)
    assert client.flavour() == "mock"
    everything = client.search_opportunities("", 100, "all")
    assert len(everything) == len(OPPORTUNITIES)
    assert [o["created_on"] for o in everything] == sorted((o["created_on"] for o in everything), reverse=True)
    assert all(o["url"] is None and o["source"] == "dynamics" for o in everything)
    open_ = client.search_opportunities("", 100, "open")
    assert {o["state"] for o in open_} == {"Open"} and len(open_) == len(OPPORTUNITIES)
    assert client.search_opportunities("", 100, "won") == []
    hit = client.search_opportunities("Relecloud - SaaS rehost, 18", 10, "all")   # ',' '-' and spaces in a contains() filter
    assert [o["customer"] for o in hit] == ["Relecloud"]
    assert client.search_opportunities("O'Brien & Sons", 10, "all") == []   # quotes and & are encoded safely

    assert all(o["request_type"] for o in everything)   # list includes the ingr_requesttype column
    initech = next(o for o in everything if o["customer"] == "Initech Energy")
    full = client.get_opportunity(initech["id"])
    assert full["answers_from"] == "columns" and full["answers"]["request_type"] == "Cloud Services Opportunity"
    assert full["answers"]["fund_migration_source"] == "Another cloud"   # choice column -> label
    assert full["estimated_value"] == 780000 and full["state"] == "Open"

    tail = next(o for o in everything if o["customer"] == "Tailwind Robotics")
    t = client.get_opportunity(tail["id"])
    assert t["answers"]["fund_mrr_usd"] == 15000 and t["answers"]["_submitted_by"] == "priya.raman@aivar.tech"

    from funding import facts as F
    from funding.rules import decide
    import opportunity_form
    labels = {k: v["label"] for k, v in opportunity_form.FIELDS.items()}
    cols = {k: c["column"] for k, c in opportunity_form.COLUMN_OF.items()}
    form = F.from_form(t["answers"], labels, cols)
    assert form["mrr_usd"]["column"] == "ingr_fundmrrusd"
    d = decide(F.merge(form, {})[0])
    assert (d["program"], d["total_usd"]) == ("MAP Lite", 45000)
    assert decide(F.merge(F.from_form(full["answers"], labels, cols), {})[0])["program"] == "MAP"

    client.write_decision(tail["id"], "MAP Lite", "decision text")
    again = client.get_opportunity(tail["id"])
    assert again["notes"] == [] and again["answers"]["fund_vms"] == "Yes"   # decision notes are not re-read
    assert seed(client) == 0   # idempotent


def test_column_types_are_enforced(client):
    with pytest.raises(client.DynamicsError, match="not a valid option"):
        client._request("POST", "opportunities", {"name": "x", "ingr_requesttype": 999})
    with pytest.raises(client.DynamicsError, match="Edm.Int32"):
        client._request("POST", "opportunities", {"name": "x", "ingr_totalservers": "forty"})
    with pytest.raises(client.DynamicsError, match="Invalid property"):
        client._request("POST", "opportunities", {"name": "x", "ingr_notacolumn": "y"})


def test_delete_and_reset(client):
    o = client.create_opportunity({"name": "Throwaway", "customer": "T Co"}, {"request_type": "General Inquiry"})
    client.write_decision(o["id"], "x", "y")
    client.delete_opportunity(o["id"])
    with pytest.raises(client.DynamicsError):
        client.get_opportunity(o["id"])


def test_mock_errors_match_dataverse(client):
    with pytest.raises(client.DynamicsError) as e:
        client.get_opportunity("99999999-9999-4999-8999-999999999999")
    assert e.value.status == 404
    with pytest.raises(client.DynamicsError, match="Invalid property"):
        client._request("POST", "opportunities", {"name": "x", "ownerid": "y"})
    with pytest.raises(client.DynamicsError, match="does not support this filter"):
        client._request("GET", "opportunities?$filter=startswith(name,'x')")


def test_create_through_form_path(client):
    created = client.create_opportunity({"name": "Form-made opp", "customer": "Brand New Co", "description": "d",
                                         "estimated_value": 1000}, {"request_type": "General Inquiry"})
    got = client.get_opportunity(created["id"])
    assert got["customer"] == "Brand New Co" and got["answers"] == {"request_type": "General Inquiry"}
    assert got["answers_from"] == "columns"
    assert created["url"] is None and created["source"] == "dynamics"


def test_column_creation_definitions(monkeypatch):
    import importlib.util
    import opportunity_form as form
    monkeypatch.setattr(sys, "argv", ["create_dynamics_columns.py"])
    spec = importlib.util.spec_from_file_location("create_cols", ROOT / "scripts" / "create_dynamics_columns.py")
    mod = importlib.util.module_from_spec(spec)
    with pytest.raises(SystemExit):     # dry run exits after listing
        spec.loader.exec_module(mod)
    kinds = {c["column"]: mod.definition(c)["@odata.type"].rsplit(".", 1)[1] for c in form.COLUMNS}
    assert kinds["ingr_requesttype"] == "PicklistAttributeMetadata"
    assert kinds["ingr_totalservers"] == "IntegerAttributeMetadata"
    assert kinds["ingr_fundmrrusd"] == "DecimalAttributeMetadata"
    assert kinds["ingr_anticipatedstartdate"] == "DateTimeAttributeMetadata"
    assert kinds["ingr_csopportunitydetails"] == "MemoAttributeMetadata"
    opts = mod.definition(form.BY_COLUMN["ingr_requesttype"])["OptionSet"]["Options"]
    assert [o["Value"] for o in opts][:2] == [100000000, 100000001] and len(opts) == 8
