#!/usr/bin/env python3
"""Invoke the Cost estimate agent, deployed (AgentCore, SigV4) or local (the container on :8080).

  invoke.py --arn <runtime-arn>                       ping (0 model calls, seconds): the deploy smoke test
  invoke.py --arn <runtime-arn> --case estimate       a small sample estimate (several Bedrock calls, minutes)
  invoke.py --local http://localhost:8080 [--case ...]
  invoke.py --arn ... --payload request.json          any request body (see agent/main.py)

Writes the reply to <out>/<case>/response.json. Exits non-zero unless the reply has status "completed".
Needs boto3 only for --arn.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DEPLOYMENT = Path(__file__).resolve().parents[1]
READ_TIMEOUT = 900  # AgentCore's synchronous request limit is 15 minutes
SAMPLE = ("Production in us-east-1: 2 EC2 t3.medium Linux instances running 24x7, an Application Load Balancer, "
          "Amazon RDS for PostgreSQL db.t3.medium Single-AZ with 100 GB gp3, and 500 GB of S3 Standard storage.")


def build_payload(args) -> dict:
    if args.payload:
        return json.loads(Path(args.payload).read_text(encoding="utf-8"))
    if args.case == "ping":
        return {"action": "ping"}
    return {"action": "estimate", "requirements": SAMPLE}


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--arn", help="AgentCore Runtime ARN")
    target.add_argument("--local", metavar="URL", help="local container base URL, e.g. http://localhost:8080")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--qualifier", default="DEFAULT", help="runtime endpoint name")
    ap.add_argument("--case", choices=["ping", "estimate"], default="ping")
    ap.add_argument("--payload", help="JSON request file (overrides --case)")
    ap.add_argument("--out", default=str(DEPLOYMENT / ".state" / "smoke"))
    args = ap.parse_args()

    payload = build_payload(args)
    t0 = time.time()
    result = (invoke_agentcore(args.arn, args.region, payload, args.qualifier) if args.arn
              else invoke_local(args.local, payload))
    seconds = time.time() - t0
    out = Path(args.out) / (args.case if not args.payload else "custom")
    out.mkdir(parents=True, exist_ok=True)
    (out / "response.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": result.get("status"), "stage": result.get("stage"), "error": result.get("error"),
        "seconds": round(seconds, 1), "service": result.get("service"), "model_id": result.get("model_id"),
        "calculator_url": result.get("calculator_url"), "services_count": result.get("services_count"),
        "saved_to": str(out),
    }, indent=2))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
