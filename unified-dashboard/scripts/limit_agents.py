#!/usr/bin/env python3
"""Show only the six pipeline agents in the portal (sidebar, Agents page and every other list).

The agent registry is compiled into one JS chunk that is served with `immutable, max-age=1y`, so an
in-place edit would never reach browsers that already cached it. This script therefore:

  1. patches the registry (keeps six ids, filters the manifest list),
  2. uploads the patched chunk under a NEW file name,
  3. repoints every page / payload that references the old chunk name to the new one.

The old chunk is left in place, so a stale cached page keeps working. Agents that are hidden still
exist as direct URLs (their pages and manifests are untouched); only the lists stop showing them.
Idempotent: if the portal already references the new chunk it does nothing.

    python3 scripts/limit_agents.py            # apply
    python3 scripts/limit_agents.py --dry-run
    python3 scripts/limit_agents.py --undo     # point pages back at the original chunk
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BUCKET = os.environ.get("SITE_BUCKET", "ingram-agents-dev-app-edgesitebucket3cccc238-ihhyzfnzepgv")
DISTRIBUTION = os.environ.get("DISTRIBUTION_ID", "ENNIIXWOU2B8O")
OLD_NAME = "09rl8ksyocm8k.js"
NEW_NAME = "09rl8ksyocm8k-six.js"
CHUNK_KEY = "_next/static/chunks/"

KEEP = ["intake-agent", "live-assist-agent", "discovery-agent", "catalyst-sow-generator-agent", "architecture-agent", "cost-calculator-agent"]

OLD_SET = (
    'ex=new Set(["discovery-agent","architecture-agent","diagram-generator-agent","catalyst-sow-generator-agent",'
    '"cost-calculator-agent","intake-agent","live-assist-agent","solution-match-agent","funding-agent",'
    '"offering-builder-agent","playbook-runbook-agent"])'
)
NEW_SET = "ex=new Set(" + json.dumps(KEEP, separators=(",", ":")) + ")"
OLD_FN = "function eR(){return eS.map(e=>e.manifest)}"
NEW_FN = "function eR(){return eS.map(e=>e.manifest).filter(e=>ex.has(e.id))}"


def aws(*args: str) -> str:
    return subprocess.run(["aws", *args], check=True, capture_output=True, text=True).stdout


def put(key: str, path: Path, head: dict) -> None:
    aws("s3api", "put-object", "--bucket", BUCKET, "--key", key, "--body", str(path),
        "--content-type", head.get("ContentType", "application/octet-stream"),
        "--cache-control", head.get("CacheControl", "no-cache"),
        "--server-side-encryption", head.get("ServerSideEncryption", "AES256"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()
    src, dst = (NEW_NAME, OLD_NAME) if args.undo else (OLD_NAME, NEW_NAME)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        aws("s3", "sync", f"s3://{BUCKET}/", str(root), "--exclude", "pipeline/*", "--only-show-errors")

        if not args.undo:
            old_chunk = root / CHUNK_KEY / OLD_NAME
            text = old_chunk.read_text(encoding="utf-8")
            if OLD_SET not in text or OLD_FN not in text:
                print("The registry chunk does not look the way this script expects; refusing to patch.", file=sys.stderr)
                return 1
            patched = text.replace(OLD_SET, NEW_SET, 1).replace(OLD_FN, NEW_FN, 1)
            new_chunk = root / CHUNK_KEY / NEW_NAME
            new_chunk.write_text(patched, encoding="utf-8")
            print(f"patched registry -> {NEW_NAME}")
            if not args.dry_run:
                put(CHUNK_KEY + NEW_NAME, new_chunk, json.loads(aws("s3api", "head-object", "--bucket", BUCKET, "--key", CHUNK_KEY + OLD_NAME)))

        changed = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name in (OLD_NAME, NEW_NAME):
                continue
            key = path.relative_to(root).as_posix()
            if path.suffix not in {".html", ".txt", ".js", ".json"}:
                continue
            data = path.read_text(encoding="utf-8", errors="surrogateescape")
            # Match the whole file name so "x09rl8ksyocm8k.js" style substrings are never touched.
            if f"/{src}" not in data and f'"{src}"' not in data:
                continue
            new = data.replace(f"/{src}", f"/{dst}").replace(f'"{src}"', f'"{dst}"')
            if new == data:
                continue
            changed += 1
            print(("would repoint " if args.dry_run else "repointed ") + key)
            if args.dry_run:
                continue
            head = json.loads(aws("s3api", "head-object", "--bucket", BUCKET, "--key", key))
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            put(key, path, head)

    print(f"{changed} object(s) {'would change' if args.dry_run else 'changed'}")
    if changed and not args.dry_run:
        inv = aws("cloudfront", "create-invalidation", "--distribution-id", DISTRIBUTION, "--paths", "/*", "--query", "Invalidation.Id", "--output", "text").strip()
        print("invalidation", inv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
