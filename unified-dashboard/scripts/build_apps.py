#!/usr/bin/env python3
"""Stage the latest standalone UI of each of the six agents for the portal.

Output (in site-apps/):
  apps/<id>/...           the agent's own UI, pointed at its live API
  shells/<slug>.html      portal page (sidebar + header) that hosts the app in a same-origin frame

The sources are the UIs that are deployed on Amplify today:
  intake       intake-agent/deployment/ui
  live-assist  ingram-ai-live-assist-feature-develop-updated/src/console   (via the portal's authenticated proxy)
  discovery    discovery-agent/deployment/ui
  sow          SOW-Pipeline-Updated/sow-generator-agent/deployment/ui       (SOW pipeline split out: standalone)
  architecture SOW-Pipeline-Updated/architecture-agent/deployment/ui        (standalone)
  cost         SOW-Pipeline-Updated/cost-estimate-agent/deployment/ui       (standalone)

Nothing is edited in the source folders; the staged copies get a live config.js, a connect-src that
allows only that agent's API, and a theme default that follows the portal.
"""
from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
OUT = HERE / "site-apps"
S3_ORIGIN = "https://*.s3.us-east-1.amazonaws.com"

APPS = [
    {"id": "intake", "slug": "intake-agent", "title": "Intake Agent", "src": "intake-agent/deployment/ui",
     "config": ("window.INTAKE_CONFIG", "https://xknyierfsd.execute-api.us-east-1.amazonaws.com"), "connect": []},
    {"id": "live-assist", "slug": "live-assist-agent", "title": "Live Assist Agent", "src": "ingram-ai-live-assist-feature-develop-updated/src/console",
     "config": None, "connect": []},
    {"id": "discovery", "slug": "discovery-agent", "title": "Discovery Agent", "src": "discovery-agent/deployment/ui",
     "config": ("window.DISCOVERY_CONFIG", "https://eaxi52af2c.execute-api.us-east-1.amazonaws.com"), "connect": [S3_ORIGIN]},
    {"id": "sow", "slug": "catalyst-sow-generator-agent", "title": "SOW Generator Agent", "src": "SOW-Pipeline-Updated/sow-generator-agent/deployment/ui",
     "config": ("window.SOW_CONFIG", "https://m8yzw40uva.execute-api.us-east-1.amazonaws.com"), "connect": []},
    {"id": "architecture", "slug": "architecture-agent", "title": "Architecture Agent", "src": "SOW-Pipeline-Updated/architecture-agent/deployment/ui",
     "config": ("window.ARCH_CONFIG", "https://6z8b7f8w27.execute-api.us-east-1.amazonaws.com"), "connect": []},
    {"id": "cost", "slug": "cost-calculator-agent", "title": "Cost Estimate Agent", "src": "SOW-Pipeline-Updated/cost-estimate-agent/deployment/ui",
     "config": ("window.COST_CONFIG", "https://g2fvp3xlbk.execute-api.us-east-1.amazonaws.com"), "connect": []},
]

NAV = [(a["slug"], a["title"].replace(" Agent", "")) for a in APPS]
CSP_META = re.compile(r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(")', re.S)
THEME_DEFAULT = re.compile(r'(getItem\("\w+Theme"\)\s*\|\|\s*)"dark"')


def must_replace(text: str, old: str, new: str, what: str) -> str:
    if old not in text:
        sys.exit(f"Live Assist console changed upstream: cannot patch {what}. Update scripts/build_apps.py.")
    return text.replace(old, new, 1)


def patch_live_assist(dest: Path) -> None:
    """Use the portal's authenticated Live Assist proxy: no access key in the browser, CSRF token on writes."""
    (dest / "config.js").write_text('window.LIVE_ASSIST_CONFIG = { apiBase: "/api/v1/live-assist" };\n', encoding="utf-8")
    p = dest / "js" / "api.js"
    t = p.read_text(encoding="utf-8")
    t = must_replace(t, 'export const usesAccessKey = Boolean(apiBase);',
                     'const viaPortal = apiBase.startsWith("/api/v1/");\n'
                     'export const usesAccessKey = Boolean(apiBase) && !viaPortal;\n\n'
                     '// Portal proxy: the cookie session authenticates, and writes need the portal\'s CSRF token.\n'
                     'let csrfToken = null;\n'
                     'async function portalCsrf() {\n'
                     '  if (!csrfToken) {\n'
                     '    const r = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });\n'
                     '    csrfToken = (await r.json()).csrf_token;\n'
                     '  }\n'
                     '  return csrfToken;\n'
                     '}', "usesAccessKey")
    t = must_replace(t, 'function authHeaders() {\n  if (!apiBase) return { "X-Console": "1" };',
                     'function authHeaders() {\n  if (viaPortal) return {};\n  if (!apiBase) return { "X-Console": "1" };', "authHeaders")
    t = must_replace(t, 'function unauthorized() {\n  return new ApiError(401, "unauthorized", "The access key',
                     'function unauthorized() {\n  if (viaPortal) return new ApiError(401, "unauthorized", "Your portal session has expired. Reload the page and sign in again.");\n'
                     '  return new ApiError(401, "unauthorized", "The access key', "unauthorized")
    t = must_replace(t, '  let response;\n  try {\n    response = await fetch(endpoint(path), {\n      method,\n      headers: { ...authHeaders(),',
                     '  let response;\n  const csrf = viaPortal && method !== "GET" ? { "X-CSRF-Token": await portalCsrf() } : {};\n  try {\n    response = await fetch(endpoint(path), {\n      method,\n      credentials: "same-origin",\n      headers: { ...authHeaders(), ...csrf,',
                     "request()")
    t = must_replace(t, '  if (apiBase && response.status === 401) throw unauthorized();\n  if (response.status === 304)',
                     '  if (viaPortal && response.status === 403) csrfToken = null; // stale CSRF token: fetch a new one next time\n'
                     '  if (apiBase && response.status === 401) throw unauthorized();\n  if (response.status === 304)', "401 handling")
    p.write_text(t, encoding="utf-8")


def stage_app(app: dict) -> None:
    src, dest = ROOT / app["src"], OUT / "apps" / app["id"]
    if not src.is_dir():
        sys.exit(f"missing source folder {src}")
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.test.js", "tests"))
    if app["config"]:
        name, url = app["config"]
        (dest / "config.js").write_text(f'{name} = {{\n  apiUrl: "{url}",\n}};\n', encoding="utf-8")
        hosts = [url, *app["connect"]]
        index = dest / "index.html"
        text = index.read_text(encoding="utf-8")

        def fix(m: re.Match) -> str:
            csp = re.sub(r"connect-src[^;]*", "connect-src 'self' " + " ".join(hosts), m.group(2))
            return m.group(1) + csp + m.group(3)

        text, n = CSP_META.subn(fix, text, count=1)
        if not n:
            sys.exit(f"{app['id']}: no CSP meta tag found to update")
        index.write_text(text, encoding="utf-8")
        for js in dest.glob("*.js"):  # follow the portal's light/dark choice by default
            body = js.read_text(encoding="utf-8")
            new = THEME_DEFAULT.sub(r'\1"system"', body)
            if new != body:
                js.write_text(new, encoding="utf-8")
    else:
        patch_live_assist(dest)


def shell(app: dict) -> str:
    links = "\n".join(
        f'            <li><a href="/agents/{s}/"{" aria-current=\"page\" class=\"current\"" if s == app["slug"] else ""}>{html.escape(t)}</a></li>' for s, t in NAV)
    title = html.escape(app["title"])
    return f"""<!doctype html>
<html lang="en" data-theme="light">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title} · Ingram Micro Agents Dashboard</title>
    <link rel="icon" href="/favicon.ico" sizes="48x48" />
    <link rel="stylesheet" href="/pipeline/assets/pipeline.css" />
    <script src="/pipeline/assets/theme.js"></script>
  </head>
  <body>
    <a class="skip" href="#main">Skip to content</a>
    <div class="shell">
      <aside class="sidebar" aria-label="Navigation">
        <a class="brand" href="/agents/" aria-label="Ingram Micro agents">INGRAM <span>MICRO</span></a>
        <nav>
          <a class="nav-item" href="/agents/">Agents</a>
          <a class="nav-item" href="/pipeline/">Pipeline Runs</a>
          <p class="nav-group">Connected agents</p>
          <ol class="nav-flow">
{links}
          </ol>
        </nav>
      </aside>
      <div class="main-col">
        <header class="topbar">
          <p class="crumbs">Ingram Micro Agents Dashboard <span aria-hidden="true">/</span> <a href="/agents/">Agents</a> <span aria-hidden="true">/</span> <strong>{title}</strong></p>
        </header>
        <main id="main" class="content content-frame">
          <iframe class="app-frame" title="{title}" src="/apps/{app['id']}/" allow="microphone; clipboard-write"></iframe>
        </main>
      </div>
    </div>
  </body>
</html>
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "shells").mkdir(parents=True)
    for app in APPS:
        stage_app(app)
        (OUT / "shells" / f"{app['slug']}.html").write_text(shell(app), encoding="utf-8")
        print(f"staged {app['id']:<12} -> /apps/{app['id']}/  and  /agents/{app['slug']}/")


if __name__ == "__main__":
    main()
