#!/usr/bin/env python3
"""Local end-to-end test: real API Lambda code (api/handler.py), real agent server (agent/main.py on :8099) with the
real orchestrator tools, checkpoints, status.json and renderers; simulated S3 (in memory, plus a tiny HTTP endpoint on
:8098 for the browser-style presigned PUT/GET), DynamoDB (in memory) and the InvokeAgentRuntime hop.

    python scripts/local_e2e.py              model stages stubbed through the Stages injection (no AWS, no cost)
    python scripts/local_e2e.py --bedrock    model stages call Amazon Bedrock for real (needs credentials; costs money)
    python scripts/local_e2e.py --serve      afterwards serve the UI on :5173 and the API on :8787 (Ctrl-C to stop)

Run it with a Python 3.12 venv that has agent/requirements.txt installed. Prints PASS/FAIL per check; exit 1 on failure.
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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
AGENT_PORT, S3_PORT, API_PORT, UI_PORT = 8099, 8098, 8787, 5173
REGION = "us-east-1"
BUCKET = f"ingram-discovery-data-111122223333-{REGION}"

ARGS = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ARGS.add_argument("--bedrock", action="store_true", help="real Bedrock for the model stages (costs money)")
ARGS.add_argument("--serve", action="store_true", help="keep serving the UI (:5173) and API (:8787) afterwards")
OPTS = ARGS.parse_args()

if not OPTS.bedrock:
    os.environ.pop("AWS_PROFILE", None)   # like the runtime: no profile, S3 storage
os.environ.update(RUNS_TABLE="local-runs", DATA_BUCKET=BUCKET, KMS_KEY_ID="", AWS_REGION=REGION,
                  AWS_DEFAULT_REGION=REGION, RUNS_PER_DAY="5", RUNS_PER_IP_HOUR="3",
                  AGENT_RUNTIME_ARN=f"arn:aws:bedrock-agentcore:{REGION}:111122223333:runtime/ingram_discovery_agent-x")
sys.path[:0] = [str(ROOT / "api"), str(ROOT / "agent")]

from botocore.exceptions import ClientError  # noqa: E402

import handler  # noqa: E402
import main  # noqa: E402
from discovery_agent.data.storage import S3Storage  # noqa: E402
from discovery_agent.runtime.state import STEPS  # noqa: E402
from discovery_agent.tools import run_step  # noqa: E402
from tests.helpers import TEXT_A, TEXT_B, StubStages  # noqa: E402


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
            if ConditionExpression and add:   # attribute_not_exists(n) OR n < :cap
                if add.group(1) in item and not item[add.group(1)] < values[":cap"]:
                    raise _err("ConditionalCheckFailedException", "UpdateItem")
            if add:
                item[add.group(1)] = item.get(add.group(1), 0) + values[add.group(2)]
            set_part = UpdateExpression.split("SET", 1)[1] if "SET" in UpdateExpression else ""
            for name, value in re.findall(r"(#?\w+)\s*=\s*(:\w+)", set_part):
                item[names.get(name, name)] = copy.deepcopy(values[value])

    def query(self, KeyConditionExpression, IndexName=None, ScanIndexForward=True, Limit=None):
        attr, value = KeyConditionExpression._values[0].name, KeyConditionExpression._values[1]
        with self.lock:
            rows = [copy.deepcopy(i) for i in self.items.values() if i.get(attr) == value]
        rows.sort(key=lambda i: i.get("created_at" if IndexName else "sk") or "", reverse=not ScanIndexForward)
        return {"Items": rows[:Limit] if Limit else rows}


class FakeS3:
    """In-memory S3 shared by the agent (S3Storage) and the API handler. Presigned URLs point at the HTTP endpoint
    below, which enforces what real S3 enforces for these URLs: the signed size and the SSE headers."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.sse: dict[str, str | None] = {}   # key -> ServerSideEncryption used on the write
        self.lock = threading.Lock()

    def put_object(self, Bucket, Key, Body, **extra):
        assert Bucket == BUCKET, Bucket
        with self.lock:
            self.objects[Key] = bytes(Body)
            self.sse[Key] = extra.get("ServerSideEncryption")

    def get_object(self, Bucket, Key):
        with self.lock:
            if Key not in self.objects:
                raise _err("NoSuchKey", "GetObject")
            return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        with self.lock:
            if Key not in self.objects:
                raise _err("404", "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        with self.lock:
            keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"KeyCount": len(keys), "Contents": [{"Key": k} for k in keys]}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                yield outer.list_objects_v2(Bucket, Prefix)
        return Pages()

    def generate_presigned_url(self, op, Params, ExpiresIn):
        q = {"op": op}
        if op == "put_object":
            q.update(size=str(Params["ContentLength"]), sse=Params.get("ServerSideEncryption", ""))
        return f"http://127.0.0.1:{S3_PORT}/{quote(Params['Key'])}?" + "&".join(f"{k}={quote(v)}" for k, v in q.items())


S3 = FakeS3()


class S3Endpoint(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", f"http://localhost:{UI_PORT}")
        self.send_header("Access-Control-Allow-Methods", "PUT,GET")
        self.send_header("Access-Control-Allow-Headers", "content-type,x-amz-server-side-encryption,"
                                                         "x-amz-server-side-encryption-aws-kms-key-id")

    def _reply(self, status: int, body: bytes = b""):
        self.send_response(status)
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._reply(204)

    def do_PUT(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        size = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(size)
        if q.get("op") != "put_object" or str(size) != q.get("size") \
                or self.headers.get("x-amz-server-side-encryption") != q.get("sse"):
            self._reply(403, b"SignatureDoesNotMatch")
            return
        S3.put_object(BUCKET, unquote(url.path.lstrip("/")), data, ServerSideEncryption=q.get("sse"))
        self._reply(200)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            body = S3.get_object(BUCKET, unquote(url.path.lstrip("/")))["Body"].read()
        except ClientError:
            self._reply(404)
            return
        self._reply(200, body)

    def log_message(self, *args):
        pass


def invoke_agent_runtime(agentRuntimeArn, runtimeSessionId, qualifier, contentType, accept, payload):
    """The AgentCore hop: POST the payload to the local agent server, like InvokeAgentRuntime does."""
    assert len(runtimeSessionId) >= 33, "runtimeSessionId must be >= 33 characters"
    SESSIONS.append(runtimeSessionId)
    req = urllib.request.Request(f"http://127.0.0.1:{AGENT_PORT}/invocations", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return {"response": io.BytesIO(r.read())}


SESSIONS: list[str] = []


def scripted_orchestrator(task, invocation_state):
    """Stands in for the Strands orchestrator model: calls the seven tools in order (as the prompt tells it to)."""
    for step in STEPS:
        run_step(invocation_state["run"], step)
    return "done"


def install_agent_seams() -> None:
    storage = S3Storage(BUCKET, client=S3)
    real = main.handle_invocation
    main.default_storage = lambda: storage
    if OPTS.bedrock:
        main.handle_invocation = lambda payload, **kw: real(payload, storage=storage)
    else:
        main.handle_invocation = lambda payload, **kw: real(
            payload, storage=storage, agent=scripted_orchestrator, stages=StubStages([]))


# ---------------------------------------------------------------- API calls through the real handler
def call(method: str, path: str, body: dict | None = None, ip: str = "203.0.113.10", query: dict | None = None):
    route, params = None, {}
    for pattern in handler.ROUTES:
        m, p = pattern.split(" ", 1)
        hit = re.match("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", p) + "$", path)
        if m == method and hit:
            route, params = pattern, hit.groupdict()
            break
    event = {"routeKey": route or f"{method} {path}", "pathParameters": params, "headers": {},
             "requestContext": {"http": {"method": method, "path": path, "sourceIp": ip}},
             "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}
    resp = handler.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"])


def http(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    RESULTS.append((bool(ok), label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}", flush=True)
    return bool(ok)


def wait_state(engagement: str, run_id: str, states: set[str], timeout: int = 1800) -> dict:
    t0, seen = time.time(), None
    while time.time() - t0 < timeout:
        s, doc = call("GET", f"/runs/{run_id}", query={"engagement": engagement})
        line = (doc.get("state"), len(doc.get("completed_steps") or []))
        if line != seen:
            seen = line
            print(f"      state={line[0]} steps={line[1]}/7", flush=True)
        if doc.get("state") in states or doc.get("state") == "failed":
            return doc
        time.sleep(0.5)
    raise TimeoutError(run_id)


def upload(engagement: str, docs: dict[str, bytes], ip: str = "203.0.113.10") -> bool:
    s, d = call("POST", f"/engagements/{engagement}/upload-urls",
                {"files": [{"name": n, "size": len(b)} for n, b in docs.items()]}, ip=ip)
    if s != 200:
        return False
    return all(http("PUT", d["urls"][n], b, d["required_headers"])[0] == 200 for n, b in docs.items())


def join_runs(timeout: float = 120) -> None:
    for t in threading.enumerate():
        if t.name.startswith("run-"):
            t.join(timeout)


# ---------------------------------------------------------------- servers
def start_servers() -> None:
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", S3_PORT), S3Endpoint).serve_forever, daemon=True).start()
    threading.Thread(target=lambda: main.app.run(port=AGENT_PORT, host="127.0.0.1"), daemon=True).start()
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
        status, data = call(method, unquote(url.path), json.loads(body) if body else None,
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


class UiFiles(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "ui"), **k)

    def do_GET(self):
        if self.path.split("?")[0] == "/config.js":   # point the UI at the local API
            raw = f'window.DISCOVERY_CONFIG = {{ apiUrl: "http://127.0.0.1:{API_PORT}" }};\n'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- the test
def main_test() -> int:
    handler._table, handler._s3_client = FakeTable(), S3
    handler._agentcore_client = mock.Mock(invoke_agent_runtime=invoke_agent_runtime)
    install_agent_seams()
    start_servers()
    print(f"agent server on :{AGENT_PORT} ({'real Bedrock' if OPTS.bedrock else 'model stages stubbed'})")

    # runtime: ping (0 model calls) and the HTTP health check
    s, _ = http("GET", f"http://127.0.0.1:{AGENT_PORT}/ping")
    check(s == 200, "runtime GET /ping answers 200")
    s, raw = http("POST", f"http://127.0.0.1:{AGENT_PORT}/invocations", json.dumps({"action": "ping"}).encode(),
                  {"Content-Type": "application/json"})
    check(s == 200 and json.loads(raw).get("status") == "ok", "runtime action ping -> status ok (deploy smoke test)")
    s, raw = http("POST", f"http://127.0.0.1:{AGENT_PORT}/invocations", json.dumps({"action": "nope"}).encode(),
                  {"Content-Type": "application/json"})
    check(json.loads(raw).get("status") == "error", "runtime rejects an unknown action with status error")

    # clients: regional virtual-host S3 URLs (CSP + bucket CORS origin), signed size + SSE, 25 s invoke timeout
    with mock.patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIDLOCALTEST", "AWS_SECRET_ACCESS_KEY": "x",
                                      "AWS_SESSION_TOKEN": ""}):
        os.environ.pop("AWS_PROFILE", None)   # restored by patch.dict
        url = handler.make_s3_client().generate_presigned_url(
            "put_object", Params={"Bucket": BUCKET, "Key": "input/A/a.txt", "ContentLength": 3,
                                  "ServerSideEncryption": "aws:kms"}, ExpiresIn=60)
        signed = (re.search(r"X-Amz-SignedHeaders=([^&]+)", url) or [None, ""])[1]
        check(url.startswith(f"https://{BUCKET}.s3.{REGION}.amazonaws.com/"),
              "presigned URLs use the bucket's regional virtual host (matches CSP connect-src DataOrigin)")
        check("content-length" in signed and "x-amz-server-side-encryption" in signed,
              f"presigned PUT signs the size and the SSE header ({signed})")
        check(handler.make_agentcore_client().meta.config.read_timeout == 25, "InvokeAgentRuntime read timeout is 25 s")

    s, d = call("GET", "/session")
    check(s == 200 and d["runs_today"] == 0 and d["runs_per_day"] == 5, "GET /session works without sign-in (quota)")

    # upload-urls validation
    one = lambda name, size=10: {"files": [{"name": name, "size": size}]}  # noqa: E731
    check(call("POST", "/engagements/Acme Corp/upload-urls", one("x.exe"))[0] == 400, "400 on an unsupported file type")
    check(call("POST", "/engagements/Acme Corp/upload-urls", one("../x.txt"))[0] == 400, "400 on a path in the file name")
    check(call("POST", "/engagements/Acme Corp/upload-urls", one("big.pdf", 26 * 1024 * 1024))[0] == 400,
          "400 on a file over 25 MB")
    check(call("POST", "/engagements/Acme Corp/upload-urls", {"files": ["a.txt"]})[0] == 400, "400 without file sizes")
    check(call("POST", "/engagements/Acme Corp/upload-urls",
               {"files": [{"name": f"{i}.txt", "size": 1} for i in range(201)]})[0] == 400, "400 on more than 200 files")
    check(call("POST", "/engagements/bad%2Fname/upload-urls", one("a.txt"))[0] == 400, "400 on a bad engagement name")
    s, d = call("POST", "/engagements/Acme Corp/upload-urls", one("01_call_a.txt", 5))
    check(http("PUT", d["urls"]["01_call_a.txt"], b"123456", d["required_headers"])[0] == 403,
          "a PUT with a different size than signed is rejected")
    check(call("POST", "/runs", {"engagement": "Nobody Here"})[0] == 400, "400 when no documents were uploaded")

    # upload + normal run
    docs = {"01_call_a.txt": TEXT_A.encode(), "02_call_b.txt": TEXT_B.encode()}
    check(upload("Acme Corp", docs), "documents uploaded with the presigned PUT URLs and SSE headers")
    check(S3.sse.get("input/Acme_Corp/01_call_a.txt") == "aws:kms", "uploaded documents are stored with SSE-KMS")
    t0 = time.time()
    s, d = call("POST", "/runs", {"engagement": "Acme Corp", "instructions": "Focus on finance operations."})
    check(s == 202 and d.get("run_id"), f"POST /runs -> 202 + run id ({d.get('run_id') or d})")
    run1 = d["run_id"]
    check(len(SESSIONS[-1]) >= 33, f"runtimeSessionId has {len(SESSIONS[-1])} characters (>= 33)")
    doc = wait_state("Acme Corp", run1, {"succeeded"})
    check(doc.get("state") == "succeeded" and len(doc.get("completed_steps") or []) == 7,
          f"run 1 succeeded with 7/7 steps in {round(time.time() - t0, 1)} s (error: {doc.get('error')})")
    check(doc.get("instructions") == "Focus on finance operations.", "status.json records the reviewer instructions")
    s, raw = http("POST", f"http://127.0.0.1:{AGENT_PORT}/invocations", json.dumps({
        "engagement": "Acme Corp", "run_id": run1, "input_prefix": "input/Acme_Corp/",
        "output_prefix": "output/Acme_Corp/"}).encode(), {"Content-Type": "application/json"})
    check(json.loads(raw).get("status") == "already_started", "a repeated start of a finished run answers already_started")
    s, d = call("GET", f"/runs/{run1}/results", query={"engagement": "Acme Corp"})
    check(s == 200 and {"discovery.json", "README.md", "discovery_report.docx"} <= set(d.get("urls") or {}),
          f"GET /results lists the report links: {sorted(d.get('urls') or {})}")
    s, body = http("GET", d["urls"]["discovery_report.docx"])
    check(s == 200 and body[:2] == b"PK", "discovery_report.docx downloads as a Word file")
    report = json.loads(S3.objects[f"output/Acme_Corp/{run1}/discovery.json"])
    check(len(report.get("use_cases") or []) >= 1, f"discovery.json has {len(report.get('use_cases') or [])} use cases")
    outputs = [k for k in S3.objects if k.startswith(f"output/Acme_Corp/{run1}/") or k.startswith("work/Acme_Corp/")]
    check(outputs and all(S3.sse.get(k) == "aws:kms" for k in outputs), "every agent write uses SSE-KMS")
    s, d = call("GET", "/runs")
    hist = {r["run_id"]: r for r in d.get("runs", [])}
    check(s == 200 and hist.get(run1, {}).get("status") == "succeeded" and hist[run1]["engagement"] == "Acme Corp",
          "GET /runs lists the run with its cached status")

    # clarify flow
    s, d = call("POST", "/runs", {"engagement": "Acme Corp", "clarify": True})
    check(s == 202, "POST /runs with clarify -> 202")
    run2 = d["run_id"]
    doc = wait_state("Acme Corp", run2, {"awaiting_clarification"})
    check(doc.get("state") == "awaiting_clarification", "clarify run pauses at awaiting_clarification after merge")
    check(call("GET", f"/runs/{run2}/results", query={"engagement": "Acme Corp"})[0] == 409, "no results while paused")
    s, d = call("GET", f"/runs/{run2}/questions", query={"engagement": "Acme Corp"})
    questions = d.get("questions") or []
    check(s == 200 and questions and questions[0].get("id"), f"GET /questions returns {len(questions)} question(s)")
    answers = {q["id"]: "Finance owns the approvals." for q in questions}
    s, d = call("POST", f"/runs/{run2}/answers", {"engagement": "Acme Corp", "answers": answers})
    check(s == 202 and d.get("resumed"), "POST /answers -> 202, run resumed (phase 2)")
    check(call("POST", f"/runs/{run2}/answers", {"engagement": "Acme Corp", "answers": answers})[0] == 409,
          "a second set of answers is rejected (409)")
    doc = wait_state("Acme Corp", run2, {"succeeded"})
    check(doc.get("state") == "succeeded", f"clarify run succeeded after the answers (error: {doc.get('error')})")
    report = json.loads(S3.objects.get(f"output/Acme_Corp/{run2}/discovery.json", b"{}"))
    check(bool((report.get("meta") or {}).get("clarifications")), "the report records the clarification round")
    check(call("GET", f"/runs/{run2}/questions", query={"engagement": "Acme Corp"})[0] == 409,
          "questions are gone once the run has finished (409)")

    # unknown runs
    check(call("GET", "/runs/2026-01-01T00-00-00Z-000000", query={"engagement": "Acme Corp"})[0] == 404,
          "404 on an unknown run")
    check(call("GET", "/runs/..", query={"engagement": "Acme Corp"})[0] == 404, "404 on a malformed run id")

    # cost guards: RUNS_PER_IP_HOUR=3 (this IP used 2), RUNS_PER_DAY=5
    check(call("POST", "/runs", {"engagement": "Acme Corp"})[0] == 202, "3rd run from one IP in the hour is accepted")
    s, d = call("POST", "/runs", {"engagement": "Acme Corp"})
    check(s == 429 and "an hour" in d.get("error", ""), f"429 per IP after 3 runs an hour ({d.get('error')})")
    check(call("POST", "/runs", {"engagement": "Acme Corp"}, ip="198.51.100.7")[0] == 202, "another IP can still start a run")
    check(call("POST", "/runs", {"engagement": "Acme Corp"}, ip="198.51.100.8")[0] == 202, "5th run of the day is accepted")
    s, d = call("POST", "/runs", {"engagement": "Acme Corp"}, ip="198.51.100.9")
    check(s == 429 and "daily" in d.get("error", ""), f"429 once the daily limit is reached ({d.get('error')})")
    check(not any("203.0.113" in pk or "198.51.100" in pk for pk, _ in handler._table.items),
          "caller IPs are stored only as hashes")
    check(call("GET", "/session")[1]["runs_today"] == 5, "GET /session shows 5 / 5 runs today")
    join_runs()

    failed = [label for ok, label in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")

    if OPTS.serve:
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", API_PORT), ApiProxy).serve_forever, daemon=True).start()
        print(f"UI on http://localhost:{UI_PORT}; API on :{API_PORT}; fake S3 on :{S3_PORT}. Ctrl-C to stop.")
        try:
            ThreadingHTTPServer(("127.0.0.1", UI_PORT), UiFiles).serve_forever()
        except KeyboardInterrupt:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_test())
