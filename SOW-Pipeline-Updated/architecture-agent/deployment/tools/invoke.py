#!/usr/bin/env python3
"""Invoke the Architecture Agent, deployed (AgentCore, SigV4) or local (the container on :8080), and save the outputs.

  invoke.py --arn <runtime-arn> --case diagram      draw the sample ADR design (no model call, seconds)
  invoke.py --arn <runtime-arn> --case full         SRS -> ADR, usage and diagram (2 Bedrock calls, ~5 min)
  invoke.py --local http://localhost:8080 --case diagram
  invoke.py --arn ... --payload request.json        any request body (see architecture_agent/agent.py)

Writes the reply to <out>/response.json and any diagram to <out>/diagram.png|svg|drawio. Exits non-zero unless
the reply has status "completed". Needs boto3 only for --arn.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DEPLOYMENT = Path(__file__).resolve().parents[1]
SAMPLE_DESIGN = DEPLOYMENT / "samples" / "sample_design.json"
SAMPLE_SRS = DEPLOYMENT / "samples" / "sample_srs.md"
READ_TIMEOUT = 900  # AgentCore's synchronous request limit is 15 minutes


def build_payload(args) -> dict:
    if args.payload:
        return json.loads(Path(args.payload).read_text(encoding="utf-8"))
    if args.case == "diagram":
        return {"action": "generate_diagram", "design": json.loads(SAMPLE_DESIGN.read_text(encoding="utf-8"))}
    if args.case == "full":
        return {"srs_text": SAMPLE_SRS.read_text(encoding="utf-8"), "srs_filename": SAMPLE_SRS.name}
    raise SystemExit(f"unknown case {args.case!r}")


def invoke_agentcore(arn: str, region: str, payload: dict, qualifier: str) -> dict:
    import boto3
    from botocore.config import Config

    client = boto3.client("bedrock-agentcore", region_name=region,
                          config=Config(read_timeout=READ_TIMEOUT, connect_timeout=30, retries={"max_attempts": 1}))
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        qualifier=qualifier,
        runtimeSessionId=f"smoke-{uuid.uuid4().hex}",   # >= 33 characters; one fresh session per call
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    return json.loads(resp["response"].read())


def invoke_local(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/invocations", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as r:   # noqa: S310 (operator-supplied local URL)
        return json.loads(r.read())


def save(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "response.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    d = result.get("diagram") or {}
    for key, name in (("png_base64", "diagram.png"), ("svg_base64", "diagram.svg")):
        if d.get(key):
            (out / name).write_bytes(base64.b64decode(d[key]))
    if d.get("drawio_xml"):
        (out / "diagram.drawio").write_text(d["drawio_xml"], encoding="utf-8")
    if (result.get("adr") or {}).get("adr_md"):
        (out / "adr.md").write_text(result["adr"]["adr_md"], encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--arn", help="AgentCore Runtime ARN")
    target.add_argument("--local", metavar="URL", help="local container base URL, e.g. http://localhost:8080")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--qualifier", default="DEFAULT", help="runtime endpoint name")
    ap.add_argument("--case", choices=["diagram", "full"], default="diagram")
    ap.add_argument("--payload", help="JSON request file (overrides --case)")
    ap.add_argument("--out", default=str(DEPLOYMENT / ".state" / "smoke"))
    args = ap.parse_args()

    payload = build_payload(args)
    t0 = time.time()
    result = (invoke_agentcore(args.arn, args.region, payload, args.qualifier) if args.arn
              else invoke_local(args.local, payload))
    seconds = time.time() - t0
    out = Path(args.out) / (args.case if not args.payload else "custom")
    save(result, out)

    report = (result.get("diagram") or {}).get("report") or {}
    meta = result.get("diagram_metadata") or {}
    print(json.dumps({
        "status": result.get("status"), "stage": result.get("stage"), "error": result.get("error"),
        "seconds": round(seconds, 1), "diagram_report": result.get("report_status"),
        "diagram_model_calls": report.get("model_calls"), "diagram_tokens": meta.get("tokens"),
        "components": (report.get("summary") or {}).get("components"), "saved_to": str(out),
    }, indent=2))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
