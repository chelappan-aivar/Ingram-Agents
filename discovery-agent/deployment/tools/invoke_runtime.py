"""Call the deployed AgentCore Runtime directly (no API, no cost guards). Used by scripts/deploy.sh for the smoke test.

    python tools/invoke_runtime.py --arn <runtime ARN> --region us-east-1 ping
    python tools/invoke_runtime.py --arn <runtime ARN> --region us-east-1 start "Acme Corp" [--run-id ID] [--clarify] [--resume]

`ping` makes 0 model calls. `start` runs the full pipeline on input/<folder>/ (expensive: Sonnet + Opus); follow it with
tools/watch_run.py. Needs boto3 and credentials allowed to call bedrock-agentcore:InvokeAgentRuntime.
"""
import argparse
import json
import secrets
import sys
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arn", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("action", choices=["ping", "start"])
    ap.add_argument("engagement", nargs="?")
    ap.add_argument("--run-id")
    ap.add_argument("--clarify", action="store_true")
    ap.add_argument("--resume", action="store_true", help="reuse the checkpoints of an existing run id")
    args = ap.parse_args()

    if args.action == "ping":
        payload = {"action": "ping"}
    else:
        if not args.engagement:
            ap.error("start needs the engagement name")
        folder = args.engagement.replace(" ", "_")
        run_id = args.run_id or f"{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%SZ}-{secrets.token_hex(3)}"
        payload = {"action": "start", "engagement": args.engagement, "run_id": run_id,
                   "input_prefix": f"input/{folder}/", "output_prefix": f"output/{folder}/",
                   "options": {"clarify": args.clarify, "resume": args.resume}}
    session_id = f"discovery-cli-{uuid.uuid4().hex}"  # >= 33 characters
    client = boto3.client("bedrock-agentcore", region_name=args.region,
                          config=Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}))
    resp = client.invoke_agent_runtime(agentRuntimeArn=args.arn, runtimeSessionId=session_id, qualifier="DEFAULT",
                                       contentType="application/json", accept="application/json",
                                       payload=json.dumps(payload).encode())
    answer = json.loads(resp["response"].read().decode() or "{}")
    print(json.dumps(answer, indent=2))
    if args.action == "start" and answer.get("status") == "accepted":
        print(f"follow it: python tools/watch_run.py --bucket <DataBucketName> --region {args.region} "
              f"'{args.engagement}' {payload['run_id']}", file=sys.stderr)
    return 0 if answer.get("status") in ("ok", "accepted", "already_started") else 1


if __name__ == "__main__":
    sys.exit(main())
