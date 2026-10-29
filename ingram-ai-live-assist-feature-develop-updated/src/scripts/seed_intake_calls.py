"""Add mock Live Assist calls to Intake opportunities, through the deployed Live Assist API.

    python scripts/seed_intake_calls.py --api-url https://<id>.execute-api.us-east-1.amazonaws.com [--dry-run]
        [--secret lassist-dev/access-key] [--only Relecloud --only "Woodgrove Bank"]

For each customer in mock_intake_calls.py it picks the customer's newest Intake opportunity, links it to a Live Assist
account and opportunity, plays each mock transcript as a call (source "transcript"), waits for the call to end and its
minutes to be written, and adds the call to the opportunity's call history. The Discovery agent then finds the calls
through the export, with no change on its side. Calls whose title already exists on the opportunity are skipped, so
the script can be run again. The access key is read from Secrets Manager and never printed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_intake_calls import BA, MOCK_CALLS  # noqa: E402

POLL_SECONDS = 4
CALL_TIMEOUT_SECONDS = 420
PLAYBACK_SPEED = 100


class Api:
    def __init__(self, base_url: str, key: str) -> None:
        self.base, self.key = base_url.rstrip("/"), key

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Access-Key": self.key, "X-Console": "1", "Accept": "application/json",
                     **({"Content-Type": "application/json"} if body is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from None
        return payload.get("data", payload) if isinstance(payload, dict) else payload


def access_key(secret_id: str) -> str:
    import boto3
    return boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"].strip()


def newest_opportunity(api: Api, customer: str) -> dict | None:
    rows = [o for o in api.request("GET", "/intake/opportunities").get("opportunities", []) if o.get("customer") == customer]
    return max(rows, key=lambda o: o.get("checked_at") or "", default=None)


def wait_until_done(api: Api, session_id: str) -> dict:
    """Poll until the call has ended, nothing is processing and the minutes are final."""
    deadline = time.time() + CALL_TIMEOUT_SECONDS
    while time.time() < deadline:
        view = api.request("GET", f"/calls/{session_id}")
        minutes = (view.get("minutes") or {}).get("status")
        if view.get("status") == "ended" and not (view.get("processing") or {}).get("busy") and minutes in ("ok", "error"):
            return view
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"call {session_id} did not finish within {CALL_TIMEOUT_SECONDS} s")


def seed_opportunity(api: Api, opp: dict, calls: list[dict[str, str]], dry_run: bool) -> int:
    oid, added = opp["intake_opportunity_id"], 0
    have = {c.get("title") for c in api.request("GET", f"/intake/opportunities/{oid}/calls").get("calls", [])}
    link = None
    for call in calls:
        if call["title"] in have:
            print(f"  skip   {call['title']} (already there)")
            continue
        if dry_run:
            print(f"  would add {call['title']} ({len(call['text'].splitlines())} lines)")
            continue
        link = link or api.request("POST", f"/intake/opportunities/{oid}/link")
        started = api.request("POST", "/calls", {
            "title": call["title"], "account_id": link["account_id"], "opportunity_id": link["opportunity_id"],
            "source": {"type": "transcript", "text": call["text"], "ba_names": BA, "speed": PLAYBACK_SPEED}})
        session_id = started["session_id"]
        view = wait_until_done(api, session_id)
        api.request("POST", f"/intake/opportunities/{oid}/calls", {"session_id": session_id})
        print(f"  added  {call['title']} -> {session_id} ({len(view.get('turns') or [])} turns, minutes {view['minutes']['status']})")
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--secret", default="lassist-dev/access-key", help="Secrets Manager id of the access key")
    parser.add_argument("--only", action="append", help="customer to seed (repeatable); default all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    api = Api(args.api_url, access_key(args.secret))
    total = 0
    for customer, calls in MOCK_CALLS.items():
        if args.only and customer not in args.only:
            continue
        opp = newest_opportunity(api, customer)
        if opp is None:
            print(f"{customer}: no Intake opportunity found, skipped")
            continue
        print(f"{customer}: {opp['name']} ({opp['intake_opportunity_id']})")
        total += seed_opportunity(api, opp, calls, args.dry_run)
    print(f"done: {total} call(s) added" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
