"""End-to-end check of a deployed environment through its API (stdlib only).

    ILA_ACCESS_KEY=... python deploy/smoke.py https://<api-id>.execute-api.<region>.amazonaws.com

Starts a manual call, sends a line, waits for an agent run, pauses and resumes,
ends the call and reads the handoff and the calls list. Exits 1 on failure.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

try:  # python.org builds on macOS ship without CA certificates; certifi has them.
    import certifi

    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

RUN_TIMEOUT_SECONDS = 120
LINE = ("We run everything on-prem today, about 40 servers and two SQL Server databases, "
        "and we want to move to AWS in eu-west-1 before the end of March.")


class Client:
    def __init__(self, base: str, key: str) -> None:
        self.base, self.key = base.rstrip("/"), key

    def __call__(self, method: str, path: str, body: dict | None = None, expect: int = 200) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-Access-Key": self.key, "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
                status, payload = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        out = json.loads(payload) if payload else {}
        if status != expect:
            raise SystemExit(f"FAIL {method} {path}: expected {expect}, got {status} {out}")
        return out


def step(text: str) -> None:
    print(f"    {text}")


def main() -> None:
    if len(sys.argv) != 2 or not os.environ.get("ILA_ACCESS_KEY"):
        raise SystemExit(__doc__)
    api = Client(sys.argv[1], os.environ["ILA_ACCESS_KEY"])

    api("GET", "/health")
    options = api("GET", "/options")
    step(f"options: {len(options['topics'])} topics")

    call = api("POST", "/calls", {"title": "Smoke test", "assist_config": {"scope": "guided", "max_suggestions": 1}},
               expect=201)
    sid = call["session_id"]
    step(f"call started: {sid}")

    seq = api("POST", f"/calls/{sid}/turns", {"speaker": "cx", "text": LINE}, expect=202)["seq"]
    step(f"line sent: seq {seq}")

    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while True:
        view = api("GET", f"/calls/{sid}")
        runs = view["runs"]
        if runs and not view["processing"]["busy"]:
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL no agent run within {RUN_TIMEOUT_SECONDS} s: {view['processing']}")
        time.sleep(2)
    run = runs[-1]
    if run["status"] != "ok":
        raise SystemExit(f"FAIL agent run errored: {run.get('error')}")
    step(f"agent run ok in {run['duration_ms'] / 1000:.1f} s, captured {run['captured_fields']}, "
         f"{len(run['suggestions'])} suggestion(s)")

    api("PATCH", f"/calls/{sid}", {"status": "paused"})
    api("PATCH", f"/calls/{sid}", {"status": "live"})
    step("paused and resumed")

    api("POST", f"/calls/{sid}/end", {})
    handoff = api("GET", f"/calls/{sid}/handoff")
    step(f"ended; handoff has {len(handoff['captured'])} captured field(s)")

    listed = {c["session_id"] for c in api("GET", "/calls?limit=20")["calls"]}
    if sid not in listed:
        raise SystemExit("FAIL call missing from GET /calls")
    print("    PASS")


if __name__ == "__main__":
    main()
