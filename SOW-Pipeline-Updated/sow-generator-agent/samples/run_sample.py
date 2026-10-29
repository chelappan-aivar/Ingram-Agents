#!/usr/bin/env python3
"""Send one sample folder to the deployed SOW generator API and save what comes back in <folder>/output/.

    python3 run_sample.py 01-aiml-fabrikam-insurance              # generate -> check (rules) -> .docx
    python3 run_sample.py 02-devops-tailspin-toys --ai-review     # also run the AI reviewer (one more model call)
    python3 run_sample.py 03-quick-test-northwind-traders --customer "Northwind Traders" --template aiml/ai-ml

Same calls as the web UI (deployment-ui/ui/app.js). Uses only the standard library.
Writes: output/sow.md, output/check.json, output/<Customer>_SOW.docx, output/run.json (timings, token usage).
A generation costs Bedrock tokens (about 2 to 5 minutes, 50k output tokens maximum).
"""
import argparse
import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_API = "https://jt1cq55g59.execute-api.us-east-1.amazonaws.com/demo/api/v1/sow"
TRANSCRIPT_EXT = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
SKIP = {"project-details.txt", "upstream.json", "sample.json"}


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def post(api: str, path: str, body: dict, timeout: int = 900):
    req = urllib.request.Request(f"{api}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-User-Id": "samples-cli"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:500]
        sys.exit(f"POST {path} failed: HTTP {err.code} {detail}")


def sse(resp):
    """Yield the JSON events of a server-sent event stream."""
    data = []
    for raw in resp:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            try:
                yield json.loads("\n".join(data))
            except json.JSONDecodeError:
                pass
            data = []
    if data:
        yield json.loads("\n".join(data))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="sample folder, e.g. 01-aiml-fabrikam-insurance")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--customer", help="customer name (default: upstream.json intake.customer_name)")
    ap.add_argument("--template", help="template id (default: upstream.json meta.template_id)")
    ap.add_argument("--opportunity", help="opportunity id (default: upstream.json meta.opportunity_id)")
    ap.add_argument("--no-upstream", action="store_true", help="do not send upstream.json")
    ap.add_argument("--ai-review", action="store_true", help="run the AI reviewer in the draft check")
    args = ap.parse_args()

    folder = (HERE / args.folder).resolve()
    if not folder.is_dir():
        sys.exit(f"no such sample folder: {folder}")
    upstream_file = folder / "upstream.json"
    upstream = json.loads(upstream_file.read_text()) if upstream_file.exists() and not args.no_upstream else None
    meta = json.loads((folder / "sample.json").read_text()) if (folder / "sample.json").exists() else {}
    customer = args.customer or meta.get("customer_name") or (upstream or {}).get("intake", {}).get("customer_name")
    template = args.template or meta.get("template_id") or (upstream or {}).get("meta", {}).get("template_id")
    opportunity = args.opportunity or meta.get("opportunity_id") or (upstream or {}).get("meta", {}).get("opportunity_id")
    if not customer or not template:
        sys.exit("--customer and --template are required when the folder has no upstream.json")
    details = (folder / "project-details.txt").read_text() if (folder / "project-details.txt").exists() else ""
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.name not in SKIP)
    transcripts = [p for p in files if p.suffix.lower() in TRANSCRIPT_EXT]
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    out = folder / "output"
    out.mkdir(exist_ok=True)

    print(f"{customer} | {template} | {len(transcripts)} transcript(s), {len(images)} image(s), "
          f"upstream: {'yes' if upstream else 'no'}")
    session_id = json.load(post(args.api, "/sessions", {}, timeout=60))["session_id"]
    image_urls = [data_url(p) for p in images]
    body = {
        "session_id": session_id, "customer_name": customer, "template_id": template, "opportunity_id": opportunity,
        "project_details": details, "upstream": upstream,
        "transcript_files": [{"name": p.name, "content_b64": data_url(p)} for p in transcripts],
        "architecture_image_b64s": image_urls, "arch_image_filenames": [p.name for p in images],
    }
    started = time.time()
    parts, usage, error, done = [], {}, None, False
    for evt in sse(post(args.api, "/generations/stream", body)):
        kind = evt.get("type")
        if kind == "chunk":
            parts.append(evt.get("text") or "")
            if len(parts) % 200 == 0:
                print(f"  ... {sum(map(len, parts)):,} characters ({time.time() - started:.0f} s)")
        elif kind == "usage":
            usage = evt.get("usage") or {}
        elif kind == "done":
            done = True
        elif kind == "error":
            error = evt.get("message")
    markdown = "".join(parts).strip()
    gen_seconds = round(time.time() - started, 1)
    if error or not done or not markdown:
        sys.exit(f"generation failed: {error or 'stream closed early'}")
    (out / "sow.md").write_text(markdown + "\n", encoding="utf-8")
    print(f"draft: {len(markdown):,} characters in {gen_seconds} s -> output/sow.md")

    check = json.load(post(args.api, "/checks", {"sow_markdown": markdown, "upstream": upstream, "verify": args.ai_review}))
    (out / "check.json").write_text(json.dumps(check, indent=2) + "\n", encoding="utf-8")
    s = check.get("summary") or {}
    print(f"check: {json.dumps(s)}, {len(check.get('issues') or [])} issue(s) -> output/check.json")
    for r in check.get("rules") or []:
        print(f"  {r['status']:>15}  {r['name']}")

    link = json.load(post(args.api, "/exports/docx", {
        "session_id": session_id, "customer_name": customer, "opportunity_id": opportunity, "sow_markdown": markdown,
        "persist_output": True,
        "architecture_images": [{"filename": p.name, "content_b64": u} for p, u in zip(images, image_urls)],
    }))
    with urllib.request.urlopen(link["download_url"], timeout=120) as resp:
        docx_name = "".join(c if c.isalnum() else "_" for c in customer).strip("_") + "_SOW.docx"
        (out / docx_name).write_bytes(resp.read())
    print(f"docx: output/{docx_name}")

    (out / "run.json").write_text(json.dumps({
        "session_id": session_id, "customer_name": customer, "template_id": template, "opportunity_id": opportunity,
        "inputs": {"transcripts": [p.name for p in transcripts], "images": [p.name for p in images],
                   "upstream": bool(upstream), "project_details_chars": len(details)},
        "generation_seconds": gen_seconds, "draft_chars": len(markdown), "usage": usage,
        "check_summary": s, "ai_review": check.get("ai_review"), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
