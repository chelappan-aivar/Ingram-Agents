#!/usr/bin/env python3
"""Local end-to-end test: real API + worker Lambda code, real agent server (agent/main.py app on 127.0.0.1:8099);
simulated DynamoDB, the Lambda async invoke and the AgentCore invoke hop (pointed at the local agent server).

    python scripts/local_e2e.py              MCPCalculatorTool.create_estimate returns a canned estimate (no AWS, no cost)
    python scripts/local_e2e.py --bedrock    the real agent: Bedrock + the calculator MCP server (needs credentials, node)

Needs the pinned agent requirements (agent/requirements.txt) in the Python running it. Prints PASS/FAIL per check
and exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_PORT = 8099

os.environ.update(RUNS_TABLE="local-runs", WORKER_FUNCTION="local-worker", RUNS_PER_IP_HOUR="4", RUNS_PER_DAY="6",
                  AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:local:000000000000:runtime/x",
                  AWS_DEFAULT_REGION=os.environ.get("AWS_REGION", "us-east-1"))
sys.path[:0] = [str(ROOT / "api"), str(ROOT / "agent")]

from botocore.exceptions import ClientError  # noqa: E402

import common  # noqa: E402
import handler  # noqa: E402
import worker  # noqa: E402

FAIL_MARKER = "PLEASE FAIL THIS ESTIMATE"


# ---------------------------------------------------------------- fakes
def _err(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeTable:
    """In-memory DynamoDB table: put/get/update (SET, ADD, the two condition forms the code uses)/query."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}
        self.lock = threading.Lock()

    def put_item(self, Item):
        with self.lock:
            self.items[(Item["pk"], Item["sk"])] = copy.deepcopy(Item)

    def get_item(self, Key):
        with self.lock:
            item = self.items.get((Key["pk"], Key["sk"]))
            return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None,
                    ConditionExpression=None):
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues
        with self.lock:
            existing = self.items.get((Key["pk"], Key["sk"]))
            item = copy.deepcopy(existing) if existing else dict(Key)
            if ConditionExpression:
                cap = re.fullmatch(r"attribute_not_exists\((\w+)\) OR (\w+) < (:\w+)", ConditionExpression)
                eq = re.fullmatch(r"(#?\w+) = (:\w+)", ConditionExpression)
                if cap:
                    ok = cap.group(1) not in item or item[cap.group(2)] < values[cap.group(3)]
                elif eq:
                    ok = existing is not None and item.get(names.get(eq.group(1), eq.group(1))) == values[eq.group(2)]
                else:
                    raise NotImplementedError(ConditionExpression)
                if not ok:
                    raise _err("ConditionalCheckFailedException", "UpdateItem")
            add = re.search(r"ADD (\w+) (:\w+)", UpdateExpression)
            if add:
                item[add.group(1)] = item.get(add.group(1), 0) + values[add.group(2)]
            set_part = UpdateExpression.split("SET", 1)[1] if "SET" in UpdateExpression else ""
            for name, value in re.findall(r"(#?\w+)\s*=\s*(:\w+)", set_part):
                item[names.get(name, name)] = copy.deepcopy(values[value])
            self.items[(Key["pk"], Key["sk"])] = item

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None, ExclusiveStartKey=None):
        attr, value = KeyConditionExpression._values[0].name, KeyConditionExpression._values[1]
        with self.lock:
            rows = [copy.deepcopy(i) for i in self.items.values() if i.get(attr) == value]
        rows.sort(key=lambda i: i.get("created_at" if IndexName else "sk") or "", reverse=not ScanIndexForward)
        return {"Items": rows[:Limit] if Limit else rows}


class FakeLambda:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def invoke(self, FunctionName, InvocationType, Payload):
        assert InvocationType == "Event"
        event = json.loads(Payload)
        t = threading.Thread(target=self._run, args=(event,), daemon=True)
        t.start()
        self.threads.append(t)
        return {"StatusCode": 202}

    @staticmethod
    def _run(event):
        try:
            worker.handler(event, None)
        except Exception as exc:  # a real async Lambda would just log it
            print(f"worker crashed: {exc!r}")


CANNED = {
    "estimate_name": "Cost Estimate",
    "estimate_id": "0123456789abcdef0123456789abcdef01234567",
    "calculator_url": "https://calculator.aws/#/estimate?id=0123456789abcdef0123456789abcdef01234567",
    "region": "us-east-1",
    "currency": "USD",
    "environments": {"Production": {"services": [
        {"name": "Amazon EC2", "service_code": "AmazonEC2", "description": "2 x t3.medium web servers",
         "config": {"location": "us-east-1", "instanceType": "t3.medium", "operatingSystem": "Linux", "tenancy": "Shared",
                    "preInstalledSw": "NA", "capacitystatus": "Used", "quantity": 2, "rate_type": "hourly"}},
        {"name": "Amazon S3", "service_code": "AmazonS3", "description": "S3 Standard storage",
         "config": {"location": "us-east-1", "volumeType": "Standard", "quantity": 500, "rate_type": "flat"}},
        {"name": "Elastic Load Balancing", "service_code": "AWSELB", "description": "Application Load Balancer",
         "config": {"location": "us-east-1", "quantity": 1, "rate_type": "hourly", "price_per_lcu": 0.008}},
    ]}},
}


def install_fake_model(calls: list[str]) -> None:
    """Replace only the model/MCP step: everything else (main.py routing, orchestrator, validate_cost_data) is real."""
    from cost_estimate.mcp_tool import MCPCalculatorTool

    def fake_create_estimate(self, user_input, report=None):
        calls.append(user_input)
        if report is not None:
            report.update(checklist=[{"name": "Amazon EC2", "environment": ""}, {"name": "Amazon S3", "environment": ""},
                                     {"name": "Application Load Balancer", "environment": ""}],
                          attempts=1, preflight_rejections=0, coverage={"saved": 3, "requested": 3, "missing": []})
        if FAIL_MARKER in user_input:
            raise RuntimeError("MCP failed after 3 attempts: simulated")
        time.sleep(0.3)
        return copy.deepcopy(CANNED)

    MCPCalculatorTool.create_estimate = fake_create_estimate


def make_invoke(invocations: list[dict]):
    def invoke_agent_runtime(agentRuntimeArn, qualifier, runtimeSessionId, contentType, accept, payload):
        assert len(runtimeSessionId) >= 33, "runtimeSessionId must be >= 33 characters"
        invocations.append({"session": runtimeSessionId, **json.loads(payload)})
        return {"response": io.BytesIO(agent_post(payload))}
    return invoke_agent_runtime


def agent_post(payload: bytes) -> bytes:
    req = urllib.request.Request(f"http://127.0.0.1:{AGENT_PORT}/invocations", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:   # noqa: S310 (local test server)
        return r.read()


# ---------------------------------------------------------------- API calls through the real handler
def call(method: str, path: str, body: dict | None = None, ip: str = "203.0.113.10"):
    route, params = None, {}
    for pattern in handler.ROUTES:
        m, p = pattern.split(" ", 1)
        rx = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", p) + "$"
        hit = re.match(rx, path)
        if m == method and hit:
            route, params = pattern, hit.groupdict()
            break
    event = {"routeKey": route or f"{method} {path}", "pathParameters": params, "headers": {},
             "requestContext": {"http": {"sourceIp": ip}}, "body": json.dumps(body) if body is not None else None}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    RESULTS.append((bool(ok), label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}", flush=True)
    return bool(ok)


def wait_done(run_id: str, timeout: int = 900) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, data = call("GET", f"/runs/{run_id}")
        if data["run"]["status"] in ("completed", "failed"):
            return data
        time.sleep(0.5)
    raise TimeoutError(run_id)


def start_agent() -> None:
    from main import app
    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=AGENT_PORT), daemon=True).start()
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{AGENT_PORT}/ping", timeout=2)   # noqa: S310
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("agent server did not start")


# ---------------------------------------------------------------- the test
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bedrock", action="store_true", help="real Bedrock + calculator MCP server for the estimate")
    args = ap.parse_args()

    model_calls: list[str] = []
    invocations: list[dict] = []
    if not args.bedrock:
        install_fake_model(model_calls)
    common._table = FakeTable()
    handler._lambda = FakeLambda()
    worker._agentcore.invoke_agent_runtime = make_invoke(invocations)
    start_agent()
    print(f"agent server on 127.0.0.1:{AGENT_PORT} ({'real Bedrock + MCP' if args.bedrock else 'estimate step faked'})")

    # the agent's own actions
    r = json.loads(agent_post(json.dumps({"action": "ping"}).encode()))
    check(r.get("status") == "completed" and r.get("model_id") and r.get("service") == "ingram-cost-agent"
          and not model_calls, f"ping action: completed, model {r.get('model_id')}, 0 model calls")
    r = json.loads(agent_post(json.dumps({"action": "explode"}).encode()))
    check(r.get("status") == "failed" and r.get("stage") == "input", "unknown action -> status failed")
    r = json.loads(agent_post(json.dumps({"requirements": "x" * 40}).encode()))
    check(r.get("status") == "failed", "missing action -> status failed")
    r = json.loads(agent_post(json.dumps({"action": "estimate", "requirements": "tiny"}).encode()))
    check(r.get("status") == "failed" and r.get("stage") == "input" and not model_calls,
          "estimate with too-short requirements -> failed before any model call")

    # open API: no sign-in
    s, d = call("GET", "/session")
    check(s == 200 and d["runs_today"] == 0 and d["runs_per_day"] == 6, "GET /session works without sign-in (quota)")

    # validation
    check(call("POST", "/runs", {"requirements": "too short"})[0] == 400, "400 on too-short requirements")
    check(call("POST", "/runs", {"requirements": "x" * 20_001})[0] == 400, "400 on requirements over 20,000 characters")
    check(call("POST", "/runs", {"title": "no text"})[0] == 400, "400 on missing requirements")
    check(call("GET", "/runs/" + "0" * 32)[0] == 404, "404 on an unknown run")
    check(call("GET", "/runs/../etc")[0] == 404, "404 on a malformed run id")
    check(call("DELETE", "/runs")[0] == 404, "404 on an unknown route")

    # run 1
    text = ("Production in us-east-1: 2 EC2 t3.medium Linux instances 24x7 behind an Application Load Balancer, "
            "and 500 GB of S3 Standard storage.")
    t0 = time.time()
    s, d = call("POST", "/runs", {"title": "E2E run", "requirements": text})
    check(s == 202 and re.fullmatch(r"[0-9a-f]{32}", d.get("run_id", "")), "POST /runs -> 202 + run id")
    run1 = d["run_id"]
    data = wait_done(run1)
    run = data["run"]
    check(run["status"] == "completed", f"worker completed the run ({round(time.time() - t0, 1)} s): {run.get('error')}")
    check(run.get("calculator_url", "").startswith("https://calculator.aws/"), "GET /runs/{id} has calculator_url")
    check(run.get("services_count") == 3 and run.get("estimate_name") == "Cost Estimate",
          f"summary on META: {run.get('services_count')} services, name {run.get('estimate_name')!r}")
    res = data.get("result") or {}
    check(len(((res.get("estimate") or {}).get("environments") or {}).get("Production", {}).get("services", [])) == 3,
          "full estimate JSON stored on RUN#/RESULT")
    check(len(res.get("checklist") or []) == 3 and (res.get("coverage") or {}).get("missing") == [],
          "requested-services checklist and coverage are returned")
    check(len(data["log"]) >= 3, f"activity log has {len(data['log'])} lines")
    check(invocations and invocations[-1]["action"] == "estimate" and invocations[-1]["session"] == f"cost-run-{run1}",
          "worker invoked action 'estimate' with session cost-run-<id>")
    if not args.bedrock:
        check(model_calls == [text], "the model step ran exactly once")

    # duplicate delivery of the same worker event does nothing
    before = len(invocations)
    out = worker.handler({"run_id": run1}, None)
    check(out.get("status") == "skipped" and len(invocations) == before
          and call("GET", f"/runs/{run1}")[1]["run"]["status"] == "completed", "duplicate worker delivery is a no-op")

    # a failing estimate ends "failed" with the stage and error (nothing swallowed)
    expected_order = [run1]
    if not args.bedrock:
        s, d = call("POST", "/runs", {"requirements": f"{FAIL_MARKER}: one EC2 t3.micro in us-east-1"})
        expected_order.insert(0, d["run_id"])
        failed = wait_done(d["run_id"])["run"]
        check(failed["status"] == "failed" and failed.get("stage") == "estimate" and "simulated" in failed.get("error", ""),
              f"a failing estimate is stored as failed (stage {failed.get('stage')})")

    # history
    s, d = call("GET", "/runs")
    ids = [r["run_id"] for r in d.get("runs", [])]
    check(s == 200 and ids[:len(expected_order)] == expected_order, "GET /runs lists the runs, newest first")
    check(any(r["run_id"] == run1 and r.get("services_count") == 3 for r in d.get("runs", [])),
          "history row carries the summary")

    # stale run is marked failed
    common._table.items[(f"RUN#{run1}", "META")].update(
        status="running", updated_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).strftime(common.TIME_FORMAT))
    check(call("GET", f"/runs/{run1}")[1]["run"]["status"] == "failed", "a run stuck in 'running' is marked failed")

    # oversized results are trimmed below the DynamoDB item limit
    big = {"estimate": {**CANNED, "environments": {"Production": {"services": [
        {"name": f"svc{i}", "config": {"blob": "x" * 2000}} for i in range(300)]}}}, "checklist": []}
    fitted = worker.fit_result(big)
    check(fitted.get("truncated") and worker._size(fitted) <= worker.RESULT_LIMIT, "results over ~350 KB are trimmed with a note")

    # per-IP hourly cap (RUNS_PER_IP_HOUR=4), then the daily cap (RUNS_PER_DAY=6)
    used = 2 if not args.bedrock else 1
    for _ in range(4 - used):
        call("POST", "/runs", {"requirements": text})
    s, d = call("POST", "/runs", {"requirements": text})
    check(s == 429 and "an hour" in d.get("error", ""), f"429 per IP after 4 runs an hour ({d.get('error')})")
    check(call("POST", "/runs", {"requirements": text}, ip="198.51.100.7")[0] == 202, "another IP can still start a run")
    check(call("POST", "/runs", {"requirements": text}, ip="198.51.100.8")[0] == 202, "6th run of the day is accepted")
    s, d = call("POST", "/runs", {"requirements": text}, ip="198.51.100.9")
    check(s == 429 and "daily" in d.get("error", ""), f"429 once the daily limit is reached ({d.get('error')})")
    check(not any("203.0.113" in k[0] for k in common._table.items), "caller IPs are stored only as hashes")
    for t in handler._lambda.threads:
        t.join(timeout=900)

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
