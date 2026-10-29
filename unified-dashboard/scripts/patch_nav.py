#!/usr/bin/env python3
"""Add (or remove) the Pipeline Runs nav script tag in every HTML page of the live portal bucket.

The portal is a compiled Next.js export, so its sidebar cannot be edited at source. This inserts
one <script> tag before </body>. It is idempotent and keeps each object's content type, cache
control and encryption settings. Use --remove to undo.

    python3 scripts/patch_nav.py            # patch
    python3 scripts/patch_nav.py --remove   # undo
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
TAG = '<script src="/pipeline/assets/nav.js" defer></script>'


def aws(*args: str) -> str:
    return subprocess.run(["aws", *args], check=True, capture_output=True, text=True).stdout


def html_keys() -> list[str]:
    out = aws("s3api", "list-objects-v2", "--bucket", BUCKET, "--query", "Contents[?ends_with(Key, `.html`)].Key", "--output", "json")
    return [k for k in json.loads(out or "[]") if not k.startswith("pipeline/")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for key in html_keys():
            head = json.loads(aws("s3api", "head-object", "--bucket", BUCKET, "--key", key))
            path = Path(tmp) / "page.html"
            aws("s3", "cp", f"s3://{BUCKET}/{key}", str(path), "--only-show-errors")
            html = path.read_text(encoding="utf-8")
            if args.remove:
                new = html.replace(TAG, "")
            elif TAG in html:
                continue
            elif "</body>" in html:
                new = html.replace("</body>", f"{TAG}</body>", 1)
            else:
                print(f"skip {key}: no </body>")
                continue
            if new == html:
                continue
            changed += 1
            print(("would patch " if args.dry_run else "patched ") + key)
            if args.dry_run:
                continue
            path.write_text(new, encoding="utf-8")
            cmd = ["s3api", "put-object", "--bucket", BUCKET, "--key", key, "--body", str(path),
                   "--content-type", head.get("ContentType", "text/html"), "--cache-control", head.get("CacheControl", "no-cache"),
                   "--server-side-encryption", head.get("ServerSideEncryption", "AES256")]
            aws(*cmd)
    print(f"{changed} page(s) {'would change' if args.dry_run else 'changed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
