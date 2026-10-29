"""End-to-end local smoke test of the Intake agent, before deploying.

Real:      agent HTTP server (main.py on :8099), Lambda handler (api/handler.py) incl. sessions, rate limits,
           file parsing, Amazon Bedrock (Claude), background run; Microsoft Graph when --outlook is used.
Simulated: DynamoDB (in-memory), InvokeAgentRuntime (forwarded to the local server), Microsoft's
           device-code + refresh endpoints (they hand out the Graph token piped on stdin).

Inputs: ../test-data (CRM CSV with 5 rows, 3 seller notes; Contoso appears in both CRM and notes).

Run from deployment/ with a Python that has bedrock-agentcore, boto3, requests, pypdf, openpyxl:
    python scripts/local_e2e.py                      # CRM + seller notes only, no Microsoft
    <print delegated Graph token> | python scripts/local_e2e.py --outlook --days 7    # all three sources
"""

import argparse
import base64
import json
import os
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / "test-data"
PORT = 8099

parser = argparse.ArgumentParser()
parser.add_argument("--outlook", action="store_true", help="also read Outlook with a Graph token from stdin")
parser.add_argument("--days", type=int, default=7, choices=[1, 7, 30])
parser.add_argument("--timeout", type=int, default=900, help="seconds to wait for the run")
args = parser.parse_args()
graph_token = sys.stdin.read().strip() if args.outlook else ""
if args.outlook and not graph_token:
    sys.exit("--outlook needs a delegated Graph token on stdin")

os.environ.update(
    AWS_REGION=os.environ.get("AWS_REGION", "us-east-1"), RUNS_TABLE="local-intake-runs",
    BEDROCK_MODEL_ID=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ENTRA_TENANT_ID="local", ENTRA_CLIENT_ID="local", MAX_EMAILS="300", RUNS_PER_IP_HOUR="3",
    AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/ingram_intake_agent-local",
)
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "api"), str(ROOT / "shared" / "python")]

# ---------------------------------------------------------------- in-memory DynamoDB (shared by agent + Lambda)
DB: dict[tuple[str, str], dict] = {}
DB_LOCK = threading.Lock()


class MemoryTable:
    def put_item(self, Item, **_):
        with DB_LOCK:
            DB[(Item["pk"], Item["sk"])] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None, **_):
        names = ExpressionAttributeNames or {}
        with DB_LOCK:
            item = DB.setdefault((Key["pk"], Key["sk"]), dict(Key))
            if UpdateExpression.startswith("ADD "):     # quota counter: ADD hits :one SET #t = if_not_exists(...)
                item["hits"] = item.get("hits", 0) + 1
                item.setdefault("ttl", ExpressionAttributeValues[":ttl"])
                return {"Attributes": {"hits": item["hits"]}}
            for part in UpdateExpression.removeprefix("SET ").split(", "):
                name, value = part.split(" = ")
                item[names.get(name, name)] = ExpressionAttributeValues[value]
            return {"Attributes": {}}

    def get_item(self, Key, **_):
        with DB_LOCK:
            item = DB.get((Key["pk"], Key["sk"]))
            return {"Item": dict(item)} if item else {}

    def delete_item(self, Key, **_):
        with DB_LOCK:
            DB.pop((Key["pk"], Key["sk"]), None)

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None,
              ExclusiveStartKey=None, **_):
        value = KeyConditionExpression.get_expression()["values"][1]
        with DB_LOCK:
            if IndexName is None:
                return {"Items": [dict(v) for (p, _s), v in sorted(DB.items()) if p == value]}
            assert IndexName == "owner-created"   # GSI: owner + created_at
            items = sorted((dict(v) for v in DB.values() if v.get("owner") == value and "created_at" in v),
                           key=lambda v: v["created_at"], reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            at = next(i for i, v in enumerate(items) if v["pk"] == ExclusiveStartKey["pk"])
            items = items[at + 1:]
        page = items[:Limit] if Limit else items
        out = {"Items": page}
        if Limit and len(items) > Limit:
            last = page[-1]
            out["LastEvaluatedKey"] = {k: last[k] for k in ("pk", "sk", "owner", "created_at")}
        return out


import boto3  # noqa: E402

_real_resource = boto3.resource
boto3.resource = lambda name, *a, **k: (types.SimpleNamespace(Table=lambda _n: MemoryTable())
                                        if name == "dynamodb" else _real_resource(name, *a, **k))

import main  # noqa: E402  (agent/main.py)

threading.Thread(target=lambda: main.app.run(port=PORT), daemon=True).start()


def agent(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


for _ in range(50):
    try:
        agent("/ping")
        break
    except OSError:
        time.sleep(0.2)
else:
    sys.exit("agent server did not start")

import handler  # noqa: E402  (api/handler.py)


def invoke_agent_runtime(**kw):
    assert len(kw["runtimeSessionId"]) >= 33, "AgentCore requires a session id of 33+ chars"
    out = agent("/invocations", json.loads(kw["payload"]))
    return {"response": types.SimpleNamespace(read=lambda: json.dumps(out).encode())}


handler._agentcore = types.SimpleNamespace(invoke_agent_runtime=invoke_agent_runtime)
POLLS = {"n": 0}


def fake_entra(path, data):
    if path == "devicecode":
        return {"device_code": "dc-secret", "user_code": "ABCD1234", "verification_uri": "https://microsoft.com/devicelogin",
                "interval": 0, "expires_in": 900}
    if data["grant_type"].endswith("device_code"):
        assert data["device_code"] == "dc-secret", "device_code must come from the server, not the browser"
        POLLS["n"] += 1
        return {"error": "authorization_pending"} if POLLS["n"] == 1 else {"access_token": graph_token, "refresh_token": "rt-1"}
    if data["grant_type"] == "refresh_token":
        return {"access_token": graph_token, "refresh_token": "rt-2"}
    raise AssertionError(data)


handler._entra = fake_entra


def api(route, body=None, run_id=None, token=None, ip="10.0.0.1"):
    event = {"routeKey": route, "body": json.dumps(body) if body is not None else None,
             "pathParameters": {"id": run_id} if run_id else None,
             "headers": {"authorization": f"Bearer {token}"} if token else {},
             "requestContext": {"http": {"sourceIp": ip}}}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def f(name):
    return {"name": name, "content_base64": base64.b64encode((DATA / name).read_bytes()).decode()}


checks: list[bool] = []


def check(name, ok):
    checks.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")


# ---------------------------------------------------------------- sessions, no Microsoft needed
print("\nSession (no sign-in page)")
check("runs need a session (401)", api("POST /runs", {})[0] == 401)
status, s = api("POST /session")
token = s.get("session")
check("POST /session returns an anonymous session", status == 200 and token)
check("only its hash is stored", token not in json.dumps([v for (p, _), v in DB.items() if p.startswith("SESSION#")], default=str))
check("GET /session: Outlook not connected", api("GET /session", token=token)[1].get("microsoft") is None)
check("no sources -> 400", api("POST /runs", {}, token=token)[0] == 400)
check("Outlook without connecting -> 409", api("POST /runs", {"outlook": {"days": 7}}, token=token)[0] == 409)
check("bad file -> 400", api("POST /runs", {"crm_files": [{"name": "a.xls", "content_base64": "eA=="}]}, token=token)[0] == 400)

if args.outlook:
    print("\nConnect Outlook (device code)")
    _, start = api("POST /auth/device/start", token=token)
    check("start hides the device_code", start.get("user_code") and "dc-secret" not in json.dumps(start))
    _, other = api("POST /session", ip="10.0.0.9")
    check("another session cannot use this login", api("POST /auth/device/poll", {"login_id": start["login_id"]}, token=other["session"])[0] == 410)
    check("first poll pending", api("POST /auth/device/poll", {"login_id": start["login_id"]}, token=token)[1].get("status") == "pending")
    status, done = api("POST /auth/device/poll", {"login_id": start["login_id"]}, token=token)
    check("second poll connects (real Graph /me)", status == 200 and done.get("status") == "done")
    check("GET /session shows the account", api("GET /session", token=token)[1].get("microsoft", {}).get("email"))

# ---------------------------------------------------------------- the consolidated run
body = {"crm_files": [f("crm-records.csv")],
        "notes_files": [f("seller-notes-contoso-retail.docx"), f("seller-notes-tailspin-toys.docx"),
                        f("seller-notes-margies-travel.pdf")],
        "outlook": {"days": args.days} if args.outlook else None}
print(f"\nPOST /runs  CRM (5 rows) + 3 seller notes{f' + Outlook {args.days}d' if args.outlook else ''}")
t0 = time.time()
status, started = api("POST /runs", body, token=token)
check("202 + run_id within 10 s", status == 202 and started.get("run_id") and time.time() - t0 < 10)
check("agent reports HealthyBusy while running", agent("/ping")["status"] == "HealthyBusy")
run_id, last = started["run_id"], None
while time.time() - t0 < args.timeout:
    _, run = api("GET /runs/{id}", run_id=run_id, token=token)
    c = run["counts"]
    line = f"{run['status']:9} {run.get('phase'):9} triaged {c.get('triaged', 0)}/{c.get('candidates', 0)} built {c.get('built', 0)}/{c.get('groups', 0)}"
    if line != last:
        print(f"  {time.time() - t0:6.1f}s  {line}")
        last = line
    if run["status"] in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}:
        break
    time.sleep(3)

c, opps = run["counts"], run["opportunities"]
check("run completed", run["status"] == "COMPLETED")
check("every record triaged", c["triaged"] == c["candidates"] > 0 and c["built"] == c["groups"])
check("matched + unmatched = opportunities", c["matched"] + c["unmatched"] == c["opportunities"] == len(opps))
included = run["sources_included"]
check("sources included recorded", set(included) >= {"crm", "notes"})
by_name = {(o.get("customer_name") or "").lower(): o for o in opps}
contoso = next((o for n, o in by_name.items() if "contoso" in n), None)
check("Contoso Retail matched across CRM + seller notes", contoso and contoso["matched"]
      and set(contoso["source_types"]) >= {"crm", "notes"} and len(contoso["sources"]) >= 2)
check("Contoso has conflicts (server count / timeline)", contoso and len(contoso.get("conflicts", [])) >= 1)
check("Contoso fields attributed to their document", contoso and {f.get("from") for f in contoso["fields"]} >= {"crm", "notes"})
tailspin = next((o for n, o in by_name.items() if "tailspin" in n), None)
check("Tailspin (notes only) unmatched, missing CRM", tailspin and not tailspin["matched"] and "crm" in tailspin["missing_sources"])
check("printer lease (Adventure Works) not an opportunity", not any("adventure" in n for n in by_name))
# The same customer may have several distinct projects; the same project must not appear twice.
names = [((o.get("customer_name") or "").lower(), (o.get("opportunity_title") or "").lower()) for o in opps]
check("no duplicate opportunities", len(set(names)) == len(names))
check("test-data customers each appear once", all(sum(n[0].startswith(c) for n in names) == 1
      for c in ("contoso", "northwind", "fabrikam", "woodgrove", "tailspin", "margie")))
check("matched listed first", [o["matched"] for o in opps] == sorted([o["matched"] for o in opps], reverse=True))
check("most passages verified", sum(o["verified_count"] for o in opps) >= 0.85 * max(sum(o["field_count"] for o in opps), 1))
check("decisions stored for every CRM row and note (DOC#)",
      len([1 for (p, s) in DB if p == f"RUN#{run_id}" and s.startswith("DOC#")]) == c["crm_rows"] + c["notes"])
_, other = api("POST /session", ip="10.0.0.8")
check("another session cannot read the run (404)", api("GET /runs/{id}", run_id=run_id, token=other["session"])[0] == 404)
check("agent returns to Healthy", agent("/ping")["status"] == "Healthy")

print("\nAny one source is enough (each run on its own)")
single = {"CRM file only": {"crm_files": [f("crm-records.xlsx")]},
          "seller-note file only": {"notes_files": [f("seller-notes-margies-travel.pdf")]},
          "pasted notes only": {"notes_text": "Call with Lamna Healthcare (Oslo): they want to move their patient portal, "
                                              "12 Linux VMs and a PostgreSQL database, to AWS eu-north-1 by March 2027."}}
if args.outlook:
    single["Outlook only"] = {"outlook": {"days": 1}}
started_single = {}
for n, (name, b) in enumerate(single.items()):
    status, res = api("POST /runs", b, token=token, ip=f"10.1.0.{n}")
    started_single[name] = res.get("run_id")
    check(f"{name}: accepted (202)", status == 202)
for name, rid in started_single.items():
    t1 = time.time()
    while time.time() - t1 < args.timeout:
        _, r = api("GET /runs/{id}", run_id=rid, token=token)
        if r["status"] in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}:
            break
        time.sleep(3)
    rc = r["counts"]
    print(f"    {name}: {r['status']} · {rc.get('opportunities')} opportunities from {rc.get('candidates')} records")
    check(f"{name}: completed with opportunities, no matched/unmatched split",
          r["status"] == "COMPLETED" and len(r["sources_included"]) == 1
          and (rc.get("opportunities", 0) >= 1 or name == "Outlook only"))   # a quiet inbox day may have none

print("\nPrevious runs (saved, newest first)")
_, hist = api("GET /runs", token=token)
ids = [h["run_id"] for h in hist["runs"]]
check("GET /runs lists this session's runs", run_id in ids and all(i in ids for i in started_single.values()))
check("newest first", [h["created_at"] for h in hist["runs"]] == sorted([h["created_at"] for h in hist["runs"]], reverse=True))
check("each entry has status, inputs and counts", all(h["status"] and "counts" in h and "inputs" in h for h in hist["runs"]))
old = next(h for h in hist["runs"] if h["run_id"] == run_id)
check("first run's summary kept (opportunities, matched)", old["counts"].get("opportunities") == c["opportunities"]
      and old["counts"].get("matched") == c["matched"])
check("an old run can be opened again", api("GET /runs/{id}", run_id=run_id, token=token)[1].get("opportunities"))
handler.HISTORY_PAGE = 2
_, p1 = api("GET /runs", token=token)
ev = {"routeKey": "GET /runs", "headers": {"authorization": f"Bearer {token}"},
      "queryStringParameters": {"cursor": p1["next_cursor"]}, "requestContext": {"http": {"sourceIp": "10.0.0.1"}}}
p2 = json.loads(handler.handler(ev, None)["body"])
check("paging: 2 per page, next page continues", len(p1["runs"]) == 2 and p1["next_cursor"]
      and p2["runs"] and p2["runs"][0]["run_id"] not in {r["run_id"] for r in p1["runs"]})
ev["headers"] = {"authorization": f"Bearer {other['session']}"}
check("cursor of another session rejected", handler.handler(ev, None)["statusCode"] == 400)
handler.HISTORY_PAGE = 20
check("another session sees no runs", api("GET /runs", token=other["session"])[1]["runs"] == [])
sess = DB[(f"SESSION#{handler._hash(token)}", "META")]
check("session lasts ~90 days", sess["ttl"] > time.time() + 89 * 86400)
if args.outlook:
    sess["ms_expires"] = int(time.time()) - 1
    check("Microsoft connection expires on its own (12 h), runs stay", api("GET /session", token=token)[1]["microsoft"] is None
          and api("GET /runs", token=token)[1]["runs"])

print("\nRate limit (RUNS_PER_IP_HOUR=3 for this test)")
small = {"notes_text": "Hi, Lamna Healthcare wants a quote to move their patient portal to AWS. Thanks, Sam"}
codes = [api("POST /runs", small, token=token, ip="10.0.0.5")[0] for _ in range(4)]
check("4th run from one IP in an hour -> 429", codes[:3] == [202] * 3 and codes[3] == 429)

print(f"\nRun {run['status']} in {time.time() - t0:.0f}s · {c}")
for o in opps:
    fit = o.get("programme_fit", {})
    print(f"  • {'MATCHED  ' if o['matched'] else 'unmatched'} {o.get('customer_name')} · {o.get('opportunity_title')} "
          f"[{'+'.join(o['source_types'])}] missing {o['missing_sources']} | {o['field_count']} fields, "
          f"{o['verified_count']} verified | {len(o['gaps'])} gaps | {len(o.get('conflicts', []))} conflicts | MAP {fit.get('MAP', {}).get('fit')}")
    for x in o.get("conflicts", [])[:3]:
        print(f"      ! {x['label']}: {x['detail'][:120]}")
print("\nAgent log:")
for entry in run["logs"]:
    print(f"  {entry['at'][11:19]} {entry['level']:5} {entry['step']}: {entry['message'][:130]}")
time.sleep(2)   # let the small rate-limit runs settle
print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
