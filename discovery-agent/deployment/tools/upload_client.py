"""End-to-end client for the discovery API. Tests the presigned-URL flow without a browser.

It calls the routes in order: get upload URLs -> PUT each file straight to S3 -> start the run -> poll status ->
fetch result URLs. Uses only the standard library (urllib), so there is nothing to install. Every run counts against
the API's cost guards (RUNS_PER_IP_HOUR, RUNS_PER_DAY).

Usage:
    python tools/upload_client.py <API_BASE_URL> <ENGAGEMENT> <FILE> [<FILE> ...] [--token <JWT>] [--no-wait]

Example (ApiUrl from the stack outputs):
    python tools/upload_client.py https://<api-id>.execute-api.<region>.amazonaws.com "Acme Corp" \
        samples/Acme/01_call.txt samples/Acme/02_email.eml

If the API sits behind a Cognito JWT authorizer, pass --token <JWT>; it is sent as `Authorization: Bearer <JWT>`
on the API calls (never on the S3 PUTs — those are already signed).
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _api(method: str, url: str, token: str | None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise SystemExit(f"API {method} {url} -> {e.code}: {detail}")


def _put_file(url: str, path: Path, headers: dict) -> int:
    """Upload one file to its presigned PUT URL. Returns the HTTP status (200 on success)."""
    req = urllib.request.Request(url, data=path.read_bytes(), method="PUT")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise SystemExit(f"PUT {path.name} -> {e.code}: {e.read().decode()}")


def main(argv: list[str]) -> int:
    token = None
    if "--token" in argv:
        i = argv.index("--token")
        token = argv[i + 1]
        del argv[i:i + 2]
    wait = "--no-wait" not in argv
    argv = [a for a in argv if a != "--no-wait"]

    if len(argv) < 3:
        sys.exit(__doc__)
    base, engagement, files = argv[0].rstrip("/"), argv[1], [Path(f) for f in argv[2:]]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        sys.exit(f"no such file(s): {', '.join(missing)}")

    # 1) ask for presigned PUT URLs
    name_in_path = urllib.parse.quote(engagement, safe="")
    out = _api("POST", f"{base}/engagements/{name_in_path}/upload-urls", token,
               {"files": [{"name": f.name, "size": f.stat().st_size} for f in files]})
    urls, headers = out["urls"], out.get("required_headers", {})
    print(f"got {len(urls)} upload URL(s); input_prefix={out['input_prefix']}")

    # 2) PUT each file directly to S3
    for f in files:
        status = _put_file(urls[f.name], f, headers)
        print(f"  uploaded {f.name} ({f.stat().st_size} bytes) -> HTTP {status}")

    # 3) start the run
    run = _api("POST", f"{base}/runs", token, {"engagement": engagement})
    run_id = run["run_id"]
    print(f"run started: run_id={run_id} status_key={run['status_key']}")

    if not wait:
        print(f"poll:   GET {base}/runs/{run_id}?engagement={name_in_path}")
        return 0

    # 4) poll status.json until the run finishes
    status_url = f"{base}/runs/{urllib.parse.quote(run_id, safe='')}?engagement={name_in_path}"
    last = None
    while True:
        try:
            doc = _api("GET", status_url, token)
        except SystemExit:
            doc = {"state": "pending"}  # 404 until the runtime writes the first status.json
        line = f"{doc.get('state'):9} {len(doc.get('completed_steps', []))}/7"
        if line != last:
            print(" ", time.strftime("%H:%M:%S"), line)
            last = line
        if doc.get("state") in ("succeeded", "failed"):
            break
        if doc.get("state") == "awaiting_clarification":
            print("the run waits for answers (clarify mode): answer in the web UI or POST /runs/{id}/answers")
            return 0
        time.sleep(10)

    if doc.get("state") == "failed":
        print("run failed:", doc.get("error"))
        return 1

    # 5) fetch presigned GET URLs for the outputs
    results = _api("GET", f"{base}/runs/{urllib.parse.quote(run_id, safe='')}/results?engagement={name_in_path}", token)
    print("results (download within", results["expires_in"], "s):")
    for name, url in results["urls"].items():
        print(f"  {name}: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
