"""Follow a run by reading its status.json from the data bucket until it finishes.

    python tools/watch_run.py --bucket <DataBucketName> --region us-east-1 "<Engagement>" <run_id>

Your own AWS identity needs s3:GetObject on output/* (and kms:Decrypt when the bucket uses a customer-managed key).
"""
import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError


def main() -> int:
    ap = argparse.ArgumentParser(description="Follow a discovery run until it finishes.")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", required=True)
    ap.add_argument("engagement")
    ap.add_argument("run_id")
    args = ap.parse_args()

    key = f"output/{args.engagement.replace(' ', '_')}/{args.run_id}/status.json"
    s3 = boto3.client("s3", region_name=args.region)
    last = None
    while True:
        try:
            status = json.loads(s3.get_object(Bucket=args.bucket, Key=key)["Body"].read())
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise
            status = None
        if status:
            done = status.get("completed_steps") or []
            line = f"{status['state']:22} done: {len(done)}/7  {done[-1:] or ''}"
            if line != last:
                print(time.strftime("%H:%M:%S"), line, flush=True)
                last = line
            if status["state"] in ("succeeded", "failed", "awaiting_clarification"):
                for step, info in (status.get("steps") or {}).items():
                    print(f"  {step:24} {info.get('status')} {info.get('seconds', '')}s")
                if status.get("error"):
                    print("error:", status["error"])
                else:
                    print("outputs:", *status.get("output_keys", []), sep="\n  ")
                return 0 if status["state"] != "failed" else 1
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
