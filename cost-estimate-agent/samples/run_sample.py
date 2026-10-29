#!/usr/bin/env python3
"""Send one sample to the deployed Cost Calculator API and save the agent's answer in output/<sample>.result.json.

    python3 run_sample.py 01-web-app-us-east-1.txt
    python3 run_sample.py all

Same calls as the web UI (deployment-ui/ui/app.js): POST /invoke-agent {user_input, session_id} (202), then
GET /invoke-agent?session_id=... every 5 s until status is "success" or "fail". Standard library only.
Each run costs Bedrock tokens and saves a public estimate on calculator.aws.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://w2ydw8uim4.execute-api.us-east-1.amazonaws.com/prod/invoke-agent"
POLL_S = 5
TIMEOUT_S = 20 * 60


def call(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def run(sample: Path) -> bool:
    session_id = f"sample-{sample.stem}-{uuid.uuid4().hex[:12]}"
    status, res = call("POST", API, {"user_input": sample.read_text(encoding="utf-8"), "session_id": session_id})
    if status != 202:
        print(f"{sample.name}: not started (HTTP {status}): {res}")
        return False
    started = time.time()
    while time.time() - started < TIMEOUT_S:
        time.sleep(POLL_S)
        status, res = call("GET", f"{API}?{urllib.parse.urlencode({'session_id': session_id})}")
        if status == 200 and res.get("status") in ("success", "fail"):
            break
    else:
        print(f"{sample.name}: still processing after {TIMEOUT_S // 60} minutes")
        return False
    seconds = round(time.time() - started)
    out = HERE / "output"
    out.mkdir(exist_ok=True)
    (out / f"{sample.stem}.result.json").write_text(json.dumps({
        "sample": sample.name, "session_id": session_id, "seconds": seconds,
        "status": res.get("status"), "error": res.get("error"), "data": res.get("data"),
        "created_at": res.get("created_at"), "updated_at": res.get("updated_at"),
    }, indent=2) + "\n", encoding="utf-8")
    data = res.get("data") or {}
    ok = res.get("status") == "success" and data.get("success") is not False
    envs = ((data.get("data") or {}).get("environments") or {})
    n = sum(len((e or {}).get("services") or []) for e in envs.values())
    print(f"{sample.name}: {'OK' if ok else 'FAILED'} in {seconds} s, {n} service(s) in {len(envs)} environment(s), "
          f"{data.get('calculator_url') or data.get('message') or res.get('error')}")
    return ok


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    names = sorted(p.name for p in HERE.iterdir() if p.suffix in (".txt", ".json", ".md")) if sys.argv[1] == "all" else [sys.argv[1]]
    results = [run(HERE / n) for n in names]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
