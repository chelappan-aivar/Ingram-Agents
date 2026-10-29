"""End-to-end local test of the Funding check flow, before deploying.

Real:      API Lambda (api/handler.py + api/funding_api.py), agent HTTP server (agent/main.py on :8098), the funding
           pipeline, the demo Dynamics store, Amazon Bedrock (Claude) for fact classification.
Simulated: DynamoDB (in-memory), InvokeAgentRuntime (forwarded to the local server), the Microsoft session
           (a session item with a user_id, as after device-code sign-in).

    cd deployment && build/testenv/bin/python scripts/local_funding_e2e.py
"""

import json
import os
import sys
import threading
import time
import types
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8098
os.environ.update(
    AWS_REGION=os.environ.get("AWS_REGION", "us-east-1"), RUNS_TABLE="local-intake-runs",
    BEDROCK_MODEL_ID=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ENTRA_TENANT_ID="local", ENTRA_CLIENT_ID="local", DYNAMICS_URL="",
    AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/ingram_intake_agent-local")
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "api"), str(ROOT / "shared" / "python")]

DB: dict[tuple[str, str], dict] = {}
LOCK = threading.Lock()


def _dec(v):
    return json.loads(json.dumps(v, default=str), parse_float=Decimal)


def _top_level(expr):
    """Split 'a = :a, b = if_not_exists(b, :t)' at commas outside parentheses."""
    parts, depth, cur = [], 0, ""
    for ch in expr:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


class MemoryTable:
    """The DynamoDB calls used by the funding flow (SET / list_append / if_not_exists, GSI query, batch get)."""
    name = "local-intake-runs"

    def __init__(self):
        self.meta = types.SimpleNamespace(client=types.SimpleNamespace(batch_get_item=self._batch))

    def put_item(self, Item, **_):
        with LOCK:
            DB[(Item["pk"], Item["sk"])] = _dec(Item)

    def get_item(self, Key, **_):
        with LOCK:
            item = DB.get((Key["pk"], Key["sk"]))
            return {"Item": json.loads(json.dumps(item, default=str), parse_float=Decimal)} if item else {}

    def delete_item(self, Key, **_):
        with LOCK:
            DB.pop((Key["pk"], Key["sk"]), None)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None, ExpressionAttributeNames=None, **_):
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        with LOCK:
            item = DB.setdefault((Key["pk"], Key["sk"]), dict(Key))
            if UpdateExpression.startswith("ADD "):
                item["hits"] = item.get("hits", 0) + 1
                item.setdefault("ttl", values[":ttl"])
                return {"Attributes": {"hits": item["hits"]}}
            expr_all, _, removes = UpdateExpression.partition(" REMOVE ")
            if expr_all.startswith("REMOVE "):
                expr_all, removes = "", expr_all.removeprefix("REMOVE ")
            for attr in filter(None, (a.strip() for a in removes.split(","))):
                item.pop(names.get(attr, attr), None)
            for part in (_top_level(expr_all.removeprefix("SET ")) if expr_all else []):
                name, expr = (s.strip() for s in part.split(" = ", 1))
                name = names.get(name, name)
                if expr.startswith("list_append("):          # list_append(if_not_exists(x, :e), :l)
                    item[name] = list(item.get(name, [])) + list(values[expr.rsplit(", ", 1)[1].rstrip(")")])
                elif expr.startswith("if_not_exists("):       # if_not_exists(x, :t)
                    item.setdefault(name, values[expr.rsplit(", ", 1)[1].rstrip(")")])
                else:
                    item[name] = values[expr]
            return {"Attributes": {}}

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None, **_):
        value = KeyConditionExpression.get_expression()["values"][1]
        with LOCK:
            if IndexName is None:
                return {"Items": [dict(v) for (p, _s), v in sorted(DB.items()) if p == value]}
            items = sorted((dict(v) for v in DB.values() if v.get("owner") == value and "created_at" in v),
                           key=lambda v: v["created_at"], reverse=not ScanIndexForward)
        return {"Items": items[:Limit] if Limit else items}

    def _batch(self, RequestItems):
        keys = RequestItems[self.name]["Keys"]
        with LOCK:
            return {"Responses": {self.name: [dict(DB[(k["pk"], k["sk"])]) for k in keys if (k["pk"], k["sk"]) in DB]}}


import boto3  # noqa: E402

_real = boto3.resource
TABLE = MemoryTable()
boto3.resource = lambda name, *a, **k: (types.SimpleNamespace(Table=lambda _n: TABLE) if name == "dynamodb"
                                        else _real(name, *a, **k))

import main  # noqa: E402

threading.Thread(target=lambda: main.app.run(port=PORT), daemon=True).start()


def agent(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body else None,
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

import handler  # noqa: E402


def invoke_agent_runtime(**kw):
    assert len(kw["runtimeSessionId"]) >= 33
    out = agent("/invocations", json.loads(kw["payload"]))
    return {"response": types.SimpleNamespace(read=lambda: json.dumps(out).encode())}


handler._agentcore = types.SimpleNamespace(invoke_agent_runtime=invoke_agent_runtime)


def call(method, path, body=None, token="", path_params=None, query=None):
    route = path if not path_params else path
    event = {"routeKey": f"{method} {route}", "headers": {"authorization": f"Bearer {token}"} if token else {},
             "body": json.dumps(body) if body is not None else None, "pathParameters": path_params,
             "queryStringParameters": query, "requestContext": {"http": {"sourceIp": "127.0.0.1"}}}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def wait(check_id, token, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        code, c = call("GET", "/funding/checks/{id}", token=token, path_params={"id": check_id})
        assert code == 200, c
        if c["status"] in ("COMPLETED", "FAILED"):
            return c
        time.sleep(1)
    sys.exit(f"check {check_id} timed out")


ok = True


def expect(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else f'  -> {detail}'}")


# ---------------------------------------------------------------- session without / with Microsoft
code, s = call("POST", "/session")
anon = s["session"]
code, body = call("GET", "/funding/opportunities", token=anon)
print("Sign-in gate")
expect("funding routes need Microsoft (403)", code == 403 and "microsoft" in body, (code, body))
code, s = call("POST", "/session")
token = s["session"]
key = handler._session_key(handler._hash(token))
DB[(key["pk"], key["sk"])].update(user_id="user-1", user_email="priya@example.com", user_name="Priya",
                                  refresh_token="r", ms_expires=int(time.time()) + 3600)
code, cfg = call("GET", "/funding/config", token=token)
expect("config says demo mode", code == 200 and cfg["mode"] == "demo", cfg)
code, bad = call("POST", "/funding/opportunities", {"answers": {"request_type": "Cloud Services Opportunity"}}, token)
expect("form validation errors (400)", code == 400 and set(bad["fields"]) == {"company_name", "ec_name"}, bad)

# ---------------------------------------------------------------- 1. form fully answered -> decided by the form alone
SCENARIOS = [
    ("Startup migration, 15k MRR, VMs (form answers)", {
        "request_type": "Cloud Services Opportunity", "company_name": "Aivar Innovations", "ec_name": "Tailwind Robotics",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration", "cs_opportunity_title": "Tailwind Robotics VMware exit",
        "cs_opportunity_details": "Seed-funded robotics startup running 35 VMs on VMware in a Bengaluru colo.",
        "fund_segment": "Startup", "fund_workload": "Migration", "fund_migration_source": "On-premises",
        "fund_vms": "Yes", "fund_mrr_usd": "15000"},
     lambda d, r: d["status"] == "decided" and d["program"] == "MAP Lite" and d["total_usd"] == 45_000),
    ("Enterprise Gen AI, facts only in the text (Claude)", {
        "request_type": "Data & AI Opportunity", "company_name": "Contoso Partners", "ec_name": "Northwind Health",
        "dai_vendor": "AWS", "dai_title": "Clinical notes summarisation on Amazon Bedrock",
        "dai_details": "Northwind Health is a 12,000-employee hospital group. They want a proof of concept for "
                       "summarising clinical notes with Amazon Bedrock. Expected AWS spend is about 25,000 USD per "
                       "month once live.", "fund_segment": "Not sure", "fund_workload": "Not sure"},
     lambda d, r: d["status"] == "decided" and d["program"] == "POC" and d["total_usd"] == 30_000
     and all(f["source"] == "agent" and f["verified"] for f in r["facts"] if f["fact"] in ("segment", "workload", "mrr_usd"))),
    ("Unknown segment: assumed non-startup, startup shown as alternative", {
        "request_type": "Cloud Services Opportunity", "company_name": "P", "ec_name": "Fabrikam",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration", "cs_opportunity_title": "Fabrikam rehost",
        "estimated_arr": "180,000"},
     lambda d, r: d["status"] == "decided" and d["program"] == "MAP Lite"
     and [a["fact"] for a in d["assumptions"]] == ["segment"] and [a["program"] for a in d["alternatives"]] == ["IW Migrate"]),
    ("SAP migration without spend: mapped to the closest programme", {
        "request_type": "Cloud Services Opportunity", "company_name": "P", "ec_name": "Contoso Retail",
        "cs_vendor": "AWS", "aws_opportunity_type": "SAP migration", "cs_opportunity_title": "SAP ECC to AWS",
        "fund_workload": "SAP migration"},
     lambda d, r: d["status"] == "decided" and d["program"] == "Promotional credits" and d["rule"].startswith("sap_migration")),
]

created = []
for name, answers, check in SCENARIOS:
    print(name)
    t0 = time.time()
    code, res = call("POST", "/funding/opportunities", {"answers": answers, "check": True}, token)
    expect("opportunity created in the demo store (201) and check started", code == 201 and "check" in res, res)
    if code != 201 or "check" not in res:
        continue
    created.append(res["opportunity"]["id"])
    c = wait(res["check"]["check_id"], token)
    r = c.get("result") or {}
    d = r.get("decision") or {}
    expect(f"check completed in {time.time() - t0:.1f}s", c["status"] == "COMPLETED", c.get("error"))
    expect(f"decision: {d.get('status')} / {d.get('program')} / {d.get('total_usd')}", d and check(d, r),
           json.dumps({k: d.get(k) for k in ("status", "program", "funding", "total_usd", "missing")}, default=str))
    facts = {f["fact"]: (f["display"], f.get("source"), f.get("verified")) for f in r.get("facts", []) if f["used"]}
    print(f"        facts used: {facts}")
    print(f"        rationale:  {d.get('rationale')}")
    demo = DB[(f"DYNOPP#{res['opportunity']['id']}", "META")]
    expect("decision written back to the opportunity", r.get("written_back") and len(demo.get("decisions", [])) == 1)

# ---------------------------------------------------------------- list, re-check by ID, ownership
print("List and ownership")
code, lst = call("GET", "/funding/opportunities", token=token)
expect("list shows all opportunities, newest first, with latest decision",
       code == 200 and created and [o["id"] for o in lst["opportunities"]] == created[::-1]
       and all(o["latest"] for o in lst["opportunities"]), lst)
code, again = call("POST", "/funding/checks", {"opportunity_id": created[0]}, token)
expect("re-check by opportunity ID (202)", code == 202, again)
wait(again["check_id"], token)
code, s2 = call("POST", "/session")
other = s2["session"]
k2 = handler._session_key(handler._hash(other))
DB[(k2["pk"], k2["sk"])].update(user_id="user-2", refresh_token="r", ms_expires=int(time.time()) + 3600)
code, _ = call("GET", "/funding/checks/{id}", token=other, path_params={"id": again["check_id"]})
expect("another user cannot read the check (404)", code == 404)
code, lst2 = call("GET", "/funding/opportunities", token=other)
expect("another user's list is empty", lst2["opportunities"] == [], lst2)
code, nf = call("POST", "/funding/checks", {"opportunity_id": "99999999-9999-9999-9999-999999999999"}, token)
expect("unknown opportunity -> 404", code == 404, nf)
code, existing = call("GET", "/runs", token=token)
expect("existing Intake route still works", code == 200 and "runs" in existing, existing)

# ---------------------------------------------------------------- Funding Desk: list from Dynamics, batch check, detail
print("Funding Desk")
code, seeded = call("POST", "/funding/demo/seed", {}, token)
expect("sample opportunities seeded", code == 200 and seeded["seeded"] == 11, seeded)
code, desk = call("GET", "/funding/dynamics", token=token, query={"state": "open"})
ids = [o["id"] for o in desk["opportunities"]]
sample_names = [o["name"] for o in desk["opportunities"] if o["latest"] is None]
expect("desk lists demo store opportunities, newest first", code == 200 and desk["mode"] == "demo" and len(ids) >= 12
       and sample_names[0].startswith("Tailwind") and sample_names[-1].startswith("Fabrikam Logistics"), sample_names[:3])
samples = [o for o in desk["opportunities"] if o["latest"] is None]
expect("the 11 samples start unchecked", len(samples) == 11, len(samples))
code, srch = call("GET", "/funding/dynamics", token=token, query={"search": "bank"})
expect("search by name", [o["customer"] for o in srch["opportunities"]] == ["Woodgrove Bank"], srch)
code, bad = call("POST", "/funding/checks/batch", {"opportunity_ids": ["x"] * 1}, token)
expect("batch rejects bad IDs (400)", code == 400, bad)
t0 = time.time()
code, batch = call("POST", "/funding/checks/batch", {"opportunity_ids": [o["id"] for o in samples]}, token)
expect("batch of 11 started with one agent call (202)", code == 202 and len(batch["checks"]) == 11, batch)
# Northwind needs Claude (segment "Not sure"), so it is still running; form-settled checks finish in under a second.
northwind = next(o for o in samples if o["customer"] == "Northwind Health")
code, again = call("POST", "/funding/checks/batch", {"opportunity_ids": [northwind["id"]]}, token)
expect("re-submitting a running opportunity is skipped", code == 202 and again["skipped_running"] == 1, again)
code, mid = call("GET", "/funding/dynamics", token=token)
expect("list shows them running", sum(o["running"] for o in mid["opportunities"]) >= 1)
for c in batch["checks"]:
    wait(c["check_id"], token)
print(f"        batch finished in {time.time() - t0:.1f}s")
code, desk = call("GET", "/funding/dynamics", token=token)
by_customer = {o["customer"]: o for o in desk["opportunities"] if o["id"] in {s_["id"] for s_ in samples}}
EXPECT = {"Tailwind Robotics": ("decided", "MAP Lite", 45000), "Northwind Health": ("decided", "POC", 30000),
          "Woodgrove Bank": ("decided", "MAP", None), "Litware Inc": ("decided", "MAP Lite", 60000),
          "Proseware": ("decided", "IW Assess, then IW Build", 29000), "Contoso Retail": ("decided", "MAP", None),
          "A. Datum Software": ("decided", "Promotional credits", None), "Fabrikam Logistics": ("decided", "MAP Lite", 45000),
          "Nimbus Payments": ("decided", "IW Migrate", None), "Pioneer Mutual": ("decided", "Production funding", 72000),
          "Quantum Learn": ("decided", "POC only", None)}
for customer, want in EXPECT.items():
    o = by_customer.get(customer) or {}
    lt = o.get("latest") or {}
    got = (lt.get("status"), lt.get("program"), lt.get("total_usd"))
    expect(f"{customer}: {got[0]} / {got[1]} / {got[2]}", got == want and not o.get("running"), f"expected {want}")
code, det = call("GET", "/funding/dynamics/{id}", token=token, path_params={"id": by_customer["Northwind Health"]["id"]})
expect("detail returns the Dynamics record, form answers and the full decision",
       code == 200 and det["opportunity"]["answers"].get("ec_name") == "Northwind Health"
       and det["result"]["decision"]["program"] == "POC" and det["latest"]["by"] == "priya@example.com", det.get("latest"))
code, other_view = call("GET", "/funding/dynamics", token=other)
expect("decisions are shared: another user sees the same latest decision",
       next(o for o in other_view["opportunities"] if o["customer"] == "Tailwind Robotics")["latest"]["program"] == "MAP Lite")

# ---------------------------------------------------------------- live status + clear (Fetch & check all / Clear results)
print("Live status and clear")
code, b = call("POST", "/funding/checks/batch", {"opportunity_ids": [o["id"] for o in samples[:3]]}, token)
ids3 = [c["check_id"] for c in b["checks"]]
stages = set()
for _ in range(60):
    code, st = call("POST", "/funding/checks/status", {"check_ids": ids3}, token)
    stages |= {c["stage"] for c in st["checks"]}
    if all(c["status"] == "COMPLETED" for c in st["checks"]):
        break
    time.sleep(0.3)
expect("status returns stage, agent log and decision for every check",
       code == 200 and len(st["checks"]) == 3 and all(c["decision"] and c["logs"] for c in st["checks"]), st)
print(f"        stages seen while polling: {sorted(stages)}")
code, st_other = call("POST", "/funding/checks/status", {"check_ids": ids3}, other)
expect("another user's status call sees none of these checks", st_other["checks"] == [], st_other)
code, bad = call("POST", "/funding/checks/status", {"check_ids": ["x"]}, token)
expect("status rejects bad check IDs (400)", code == 400)
code, cl = call("POST", "/funding/clear", {"opportunity_ids": [o["id"] for o in samples]}, token)
code, after = call("GET", "/funding/dynamics", token=token)
left = [o for o in after["opportunities"] if o["id"] in {s_["id"] for s_ in samples} and o["latest"]]
expect(f"clear resets the samples to Not checked ({cl.get('cleared')} cleared)", code == 200 and cl["cleared"] == 11 and not left, cl)
expect("check history is kept after clear", call("GET", "/funding/checks/{id}", token=token,
       path_params={"id": ids3[0]})[1].get("status") == "COMPLETED")

print("\nALL PASSED" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
