#!/usr/bin/env python3
"""Mirror the working Amplify UIs byte for byte into site-apps/apps/<id>/ so CloudFront serves exactly the same files.

    python3 scripts/mirror_amplify.py            # all six
    python3 scripts/mirror_amplify.py intake

Starting at index.html, every referenced .js/.css/.json/.svg/.png/.ico/.csv file is fetched from the Amplify site
(same host, relative paths only). Amplify answers unknown paths with index.html, so an HTML reply for a non-HTML
path is treated as "not there". Nothing is rewritten.
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "site-apps" / "apps"
SITES = {
    "intake": "https://main.d18ure1ae72p94.amplifyapp.com/",       # Funding Desk (Intake)
    "live-assist": "https://main.dltxotqkxfpha.amplifyapp.com/",
    "discovery": "https://main.d2n5894mve8es1.amplifyapp.com/",
    "sow": "https://main.d2xiqlwjyzxr12.amplifyapp.com/",          # SOW pipeline UI
    "architecture": "https://main.d7able6y8wyu0.amplifyapp.com/",
    "cost": "https://main.d1shfynv0ayer3.amplifyapp.com/",
}
REF = re.compile(r"""["'(]\s*((?:\.{0,2}/)?[\w./@%-]+\.(?:js|mjs|css|json|svg|png|jpg|jpeg|gif|webp|ico|csv|woff2?|html))["')?#]""")
ATTR = re.compile(r"""(?:src|href)=["']([^"'#?]+)""")


def get(url: str) -> tuple[bytes, str] | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "mirror"}), timeout=30) as r:
            return r.read(), r.headers.get("content-type", "")
    except urllib.error.HTTPError:
        return None


def mirror(app: str, base: str) -> int:
    dest, seen, queue, saved = OUT / app, set(), ["index.html"], 0
    for old in dest.rglob("*") if dest.exists() else []:
        if old.is_file():
            old.unlink()
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        got = get(urllib.parse.urljoin(base, rel))
        if got is None:
            continue
        body, ctype = got
        is_html = "text/html" in ctype
        if is_html and rel != "index.html" and not rel.endswith(".html"):
            continue                              # SPA fallback for a path that does not exist
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        saved += 1
        if rel.endswith((".html", ".js", ".mjs", ".css")):
            text = body.decode("utf-8", "replace")
            for ref in (*ATTR.findall(text), *REF.findall(text)):
                if re.match(r"^(https?:)?//|^data:|^mailto:", ref):
                    continue
                nxt = urllib.parse.urlparse(urllib.parse.urljoin("http://x/" + rel, ref)).path.lstrip("/")
                if nxt and nxt not in seen and not nxt.startswith(".."):
                    queue.append(nxt)
    return saved


if __name__ == "__main__":
    for app in sys.argv[1:] or SITES:
        print(f"{app}: {mirror(app, SITES[app])} files")
