"""Local test: fetch the last N hours of Outlook mail and print/save it.

    uv run fetch-mail                      # inbox, last 24h
    uv run fetch-mail --hours 48 --all-folders --out output/mail.json
    uv run fetch-mail --check              # only verify sign-in + permissions
    uv run fetch-mail --delegated          # sign in as yourself (device code), read /me
"""

import argparse
import base64
import json
import sys
from pathlib import Path

from intake.config import load_config
from intake.graph_client import DelegatedGraphClient, GraphClient, GraphError
from intake.mail_fetcher import fetch_recent_messages

MAIL_ROLES = {"Mail.Read", "Mail.ReadWrite", "Mail.ReadBasic.All", "Mail.ReadBasic"}


def _token_claims(token: str) -> dict:
    # Read our own token's claims; no signature check needed for this diagnostic.
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch recent Outlook mail via Microsoft Graph")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--all-folders", action="store_true", help="Search all folders, not just Inbox")
    parser.add_argument("--out", type=Path, help="Write full JSON results to this file")
    parser.add_argument("--check", action="store_true", help="Only check sign-in and permissions")
    parser.add_argument("--delegated", action="store_true",
                        help="Sign in as yourself (device code) and read your own mailbox via /me")
    args = parser.parse_args()

    config = load_config()
    client = (DelegatedGraphClient(config, Path(".token_cache.json")) if args.delegated
              else GraphClient(config))

    try:
        claims = _token_claims(client.token())
    except GraphError as exc:
        print(f"SIGN-IN FAILED: {exc}", file=sys.stderr)
        return 1

    if args.delegated:
        scopes = set(claims.get("scp", "").split())
        print(f"Signed in as {claims.get('upn') or claims.get('preferred_username')}. Scopes: {sorted(scopes)}")
        if "Mail.Read" not in scopes:
            print("WARNING: token has no Mail.Read scope; Graph will return 403.", file=sys.stderr)
    else:
        roles = claims.get("roles", [])
        print(f"Sign-in OK. App permissions (roles): {roles or 'NONE'}")
        if not MAIL_ROLES & set(roles):
            print("WARNING: no Mail application permission granted; Graph will return 403.", file=sys.stderr)
    if args.check:
        return 0

    try:
        messages = fetch_recent_messages(
            client, config.mailbox_user, hours=args.hours,
            folder=None if args.all_folders else "inbox",
        )
    except GraphError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Mailbox: {'me' if args.delegated else config.mailbox_user} | last {args.hours}h | {len(messages)} message(s)\n")
    for m in messages:
        print(f"[{m['received_at']}] {m['from']}")
        print(f"    Subject: {m['subject']}")
        print(f"    Preview: {m['preview'][:120]!r}\n")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(messages, indent=2, ensure_ascii=False))
        print(f"Saved full results to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
