#!/usr/bin/env python3
"""Local end-to-end test: real API + worker Lambda code, real agent server (architecture_agent.agent on :8099),
real draw.io export; simulated DynamoDB, S3, Lambda async invoke and the AgentCore invoke hop.

    .venv/bin/python scripts/local_e2e.py              model steps faked from samples/ (no AWS, no cost)
    .venv/bin/python scripts/local_e2e.py --bedrock    model steps call Amazon Bedrock for real (needs credentials)
    .venv/bin/python scripts/local_e2e.py --serve      also serve the UI on :5173 and the API on :8787 afterwards

Needs DRAWIO_CLI (draw.io desktop) and node on PATH for the diagram step. Prints PASS/FAIL per check.
"""

from __future__ import annotations

import argparse
import base64
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
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
AGENT_PORT, API_PORT, UI_PORT = 8099, 8787, 5173

os.environ.update(RUNS_TABLE="local-runs", DATA_BUCKET="local-bucket", RUNS_PER_IP_HOUR="4",
                  WORKER_FUNCTION="local-worker", AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:local:runtime/x",
                  RUNS_PER_DAY="6", AWS_DEFAULT_REGION=os.environ.get("AWS_REGION", "us-east-1"),
                  DATA_DIR="/tmp/arch-e2e-data")
os.environ.setdefault("DRAWIO_CLI", "/Applications/draw.io.app/Contents/MacOS/draw.io")
sys.path[:0] = [str(ROOT / "api"), str(ROOT / "agent")]

from botocore.exceptions import ClientError  # noqa: E402

import common  # noqa: E402
import handler  # noqa: E402
import worker  # noqa: E402


# ---------------------------------------------------------------- fakes
def _err(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeTable:
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
            item = self.items.setdefault((Key["pk"], Key["sk"]), dict(Key))
            add = re.search(r"ADD (\w+) (:\w+)", UpdateExpression)
            if ConditionExpression and add:   # the daily counter: attribute_not_exists(n) OR n < :cap
                if add.group(1) in item and not item[add.group(1)] < values[":cap"]:
                    raise _err("ConditionalCheckFailedException", "UpdateItem")
            if add:
                item[add.group(1)] = item.get(add.group(1), 0) + values[add.group(2)]
            set_part = UpdateExpression.split("SET", 1)[1] if "SET" in UpdateExpression else ""
            for name, value in re.findall(r"(#?\w+)\s*=\s*(:\w+)", set_part):
                item[names.get(name, name)] = copy.deepcopy(values[value])

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None, ExclusiveStartKey=None):
        attr, value = KeyConditionExpression._values[0].name, KeyConditionExpression._values[1]
        with self.lock:
            rows = [copy.deepcopy(i) for i in self.items.values() if i.get(attr) == value]
        rows.sort(key=lambda i: i.get("created_at" if IndexName else "sk") or "", reverse=not ScanIndexForward)
        return {"Items": rows[:Limit] if Limit else rows}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _err("NoSuchKey", "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


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


SAMPLE_DESIGN = json.loads((ROOT / "samples" / "sample_design.json").read_text())


def fake_model_reply(payload: dict) -> dict | None:
    """Canned replies for the three model-calling actions (no Bedrock)."""
    from adr_generator.contracts import Design
    from adr_generator.render.adr_md import render_adr
    from adr_generator.render.spec_json import build_spec
    action = payload.get("action")
    if action in ("generate_adr", "revise_adr"):
        design = copy.deepcopy(payload.get("design") or SAMPLE_DESIGN)
        if action == "revise_adr":
            design["title"] = design["title"].replace(" (revised)", "") + " (revised)"
        d = Design.model_validate(design)
        return {"status": "completed", "run_id": "fake", "design": d.model_dump(mode="json"),
                "adr_md": render_adr(d, source_name="srs.md", run_date="2026-10-20"),
                "spec_json": build_spec(d, source_name="srs.md", generated_at="2026-10-20T00:00:00Z"),
                "counts": {"requirements": len(d.requirements), "decisions": len(d.decisions),
                           "components": len(d.components), "relationships": len(d.relationships),
                           "assumptions": len(d.assumptions), "assumed_components": 0},
                "tokens": {"input": 12000, "output": 18000}, "seconds": 0.1, "model_id": "fake",
                "fallback_used": False, "stop_reason": "tool_use"}
    if action == "estimate_usage":
        return {"status": "completed", "run_id": payload.get("run_id"),
                "usage_json": {"services": {"compute": [{"service": "AWS Fargate", "task_count": 4, "vcpu_per_task": 1}],
                                            "database": [{"service": "Amazon RDS for PostgreSQL", "storage_gb": 100}]}},
                "usage_assumptions_md": "# Usage assumptions\n- 150 agents, business hours.\n",
                "counts": {"entries": 2, "drivers": 1, "warnings": 0}, "tokens": {"input": 9000, "output": 3000},
                "seconds": 0.1, "model_id": "fake", "fallback_used": False}
    return None


def make_invoke(use_bedrock: bool):
    def invoke_agent_runtime(agentRuntimeArn, qualifier, runtimeSessionId, contentType, accept, payload):
        assert len(runtimeSessionId) >= 33, "runtimeSessionId must be >= 33 characters"
        body = json.loads(payload)
        reply = None if use_bedrock else fake_model_reply(body)
        if reply is None:
            req = urllib.request.Request(f"http://127.0.0.1:{AGENT_PORT}/invocations", data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=900) as r:
                return {"response": io.BytesIO(r.read())}
        return {"response": io.BytesIO(json.dumps(reply).encode())}
    return invoke_agent_runtime


# ---------------------------------------------------------------- API calls through the real handler
def call(method: str, path: str, body: dict | None = None, ip: str = "203.0.113.10", query: dict | None = None):
    route, params = None, {}
    for pattern in handler.ROUTES:
        m, p = pattern.split(" ", 1)
        rx = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", p) + "$"
        hit = re.match(rx, path)
        if m == method and hit:
            route, params = pattern, hit.groupdict()
            break
    event = {"routeKey": route or f"{method} {path}", "pathParameters": params, "headers": {},
             "requestContext": {"http": {"sourceIp": ip}},
             "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    RESULTS.append((bool(ok), label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}", flush=True)
    return bool(ok)


def wait_done(run_id: str, timeout: int = 900) -> dict:
    t0, seen = time.time(), set()
    while time.time() - t0 < timeout:
        _, data = call("GET", f"/runs/{run_id}")
        stage = data["run"].get("stage")
        if stage not in seen:
            seen.add(stage)
            print(f"      stage={stage} progress={data['run'].get('progress')}", flush=True)
        if data["run"]["status"] in ("completed", "partial", "failed"):
            return data
        time.sleep(1)
    raise TimeoutError(run_id)


# ---------------------------------------------------------------- servers
def start_agent() -> None:
    from architecture_agent.agent import app
    threading.Thread(target=lambda: app.run(port=AGENT_PORT), daemon=True).start()
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{AGENT_PORT}/ping", timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("agent server did not start")


class ApiProxy(BaseHTTPRequestHandler):
    """Turns HTTP requests into API Gateway v2 events for handler.handler (for the --serve UI preview)."""
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", f"http://localhost:{UI_PORT}")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self, method):
        url = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("content-length") or 0)) if method == "POST" else None
        status, data = call(method, url.path, json.loads(body) if body else None,
                            query={k: v[0] for k, v in parse_qs(url.query).items()} or None)
        raw = json.dumps(data).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- the test
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bedrock", action="store_true", help="real Bedrock for the ADR and usage steps")
    ap.add_argument("--serve", action="store_true", help="keep serving the UI (:5173) and API (:8787) afterwards")
    args = ap.parse_args()

    common._table, common._s3 = FakeTable(), FakeS3()
    handler._lambda = FakeLambda()
    worker._agentcore.invoke_agent_runtime = make_invoke(args.bedrock)
    start_agent()
    print(f"agent server on :{AGENT_PORT} ({'real Bedrock' if args.bedrock else 'model steps faked'})")

    # open API: no sign-in
    s, d = call("GET", "/session")
    check(s == 200 and d["runs_today"] == 0 and d["runs_per_day"] == 6, "GET /session works without sign-in (quota)")

    # validation
    check(call("POST", "/runs", {"srs_text": "too short"})[0] == 400, "400 on too-short SRS text")
    check(call("POST", "/runs", {"file": {"name": "x.exe", "content_base64": "QUJD"}})[0] == 400, "400 on a bad file type")
    check(call("POST", "/runs", {"file": {"name": "x.md", "content_base64": "%%%"}})[0] == 400, "400 on bad base64")
    check(call("GET", "/runs/" + "0" * 32)[0] == 404, "404 on an unknown run")
    check(call("GET", "/runs/../etc")[0] == 404, "404 on a malformed run id")

    # run 1: pasted text
    srs = (ROOT / "samples" / "sample_srs.md").read_text()
    t0 = time.time()
    s, d = call("POST", "/runs", {"title": "E2E text run", "srs_text": srs})
    check(s == 202 and re.fullmatch(r"[0-9a-f]{32}", d.get("run_id", "")), "POST /runs (text) -> 202 + run id")
    run1 = d["run_id"]
    check(call("POST", f"/runs/{run1}/revise", {"instruction": "Make it Multi-AZ"})[0] == 409,
          "409 when revising a run that is still working")
    data = wait_done(run1)
    v1 = data["versions"][0]
    check(data["run"]["status"] == "completed", f"run 1 completed ({round(time.time() - t0, 1)} s): {data['run'].get('error')}")
    check({"adr.md", "spec.json", "design.json", "diagram.png", "diagram.svg", "diagram.drawio", "usage.json"}
          <= set(v1.get("files") or {}), "version 1 has ADR, spec, design, diagram (png/svg/drawio) and usage files")
    check((v1.get("diagram") or {}).get("report_status") in ("pass", "pass_with_warnings", "warn"),
          f"diagram report status: {(v1.get('diagram') or {}).get('report_status')}")
    check(len(data["log"]) >= 5, f"activity log has {len(data['log'])} lines")

    s, f = call("GET", f"/runs/{run1}/files/diagram.png", query={"version": "1"})
    check(s == 200 and base64.b64decode(f["data"])[:8] == b"\x89PNG\r\n\x1a\n", "diagram.png comes back as a PNG")
    s, f = call("GET", f"/runs/{run1}/files/adr.md")
    check(s == 200 and f["data"].startswith("# Architecture Decision Record"), "adr.md comes back as text")
    check(call("GET", f"/runs/{run1}/files/secret.txt")[0] == 404, "404 on a file name outside the allow-list")
    check(call("GET", f"/runs/{run1}/files/adr.md", query={"version": "7"})[0] == 404, "404 on a version that does not exist")

    # revise -> version 2
    s, d = call("POST", f"/runs/{run1}/revise", {"instruction": "Use Aurora PostgreSQL Serverless v2 for the case database."})
    check(s == 202 and d.get("version") == 2, "POST /revise -> 202, version 2")
    data = wait_done(run1)
    check(data["run"]["status"] == "completed" and len(data["versions"]) == 2
          and data["versions"][1].get("base_version") == 1, "version 2 completed from version 1")
    check(data["run"].get("latest_complete_version") == 2, "latest_complete_version moved to 2")

    # run 2: uploaded file (extract_srs on the real agent)
    s, d = call("POST", "/runs", {"file": {"name": "sample_srs.md", "content_base64": base64.b64encode(srs.encode()).decode()}})
    check(s == 202, "POST /runs (file) -> 202")
    run2 = d["run_id"]
    data = wait_done(run2)
    check(data["run"]["status"] == "completed" and data["run"].get("input_chars", 0) > 1000,
          f"file run completed, {data['run'].get('input_chars')} characters extracted")
    check(call("GET", f"/runs/{run2}/files/srs.txt")[1].get("data", "").strip()[:20] == srs.strip()[:20],
          "extracted SRS text is stored")

    # history
    s, d = call("GET", "/runs")
    ids = [r["run_id"] for r in d.get("runs", [])]
    check(s == 200 and ids[:2] == [run2, run1], "GET /runs lists both runs, newest first")

    # stale run is marked failed
    meta = common._table.items[(f"RUN#{run2}", "META")]
    meta.update(status="running", updated_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    check(call("GET", f"/runs/{run2}")[1]["run"]["status"] == "failed", "a run stuck in 'running' is marked failed")

    # per-IP hourly cap (RUNS_PER_IP_HOUR=4; this IP used 3), then the daily cap (RUNS_PER_DAY=6)
    check(call("POST", "/runs", {"srs_text": srs})[0] == 202, "4th run from one IP in the hour is accepted")
    s, d = call("POST", "/runs", {"srs_text": srs})
    check(s == 429 and "an hour" in d.get("error", ""), f"429 per IP after 4 runs an hour ({d.get('error')})")
    check(call("POST", "/runs", {"srs_text": srs}, ip="198.51.100.7")[0] == 202, "another IP can still start a run")
    check(call("POST", "/runs", {"srs_text": srs}, ip="198.51.100.8")[0] == 202, "6th run of the day is accepted")
    s, d = call("POST", "/runs", {"srs_text": srs}, ip="198.51.100.9")
    check(s == 429 and "daily" in d.get("error", ""), f"429 once the daily limit is reached ({d.get('error')})")
    check(not any(k[0].startswith("IP#203.0.113") for k in common._table.items), "caller IPs are stored only as hashes")
    for t in handler._lambda.threads:
        t.join(timeout=900)

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")

    if args.serve:
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", API_PORT), ApiProxy).serve_forever, daemon=True).start()
        ui = lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=str(ROOT / "ui"), **k)  # noqa: E731
        print(f"UI on http://localhost:{UI_PORT}; API on :{API_PORT}. Ctrl-C to stop.")
        try:
            ThreadingHTTPServer(("127.0.0.1", UI_PORT), ui).serve_forever()
        except KeyboardInterrupt:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
