#!/usr/bin/env python3
"""Allow extra browser origins on the pipeline HTTP API (additive; never removes an origin).

The API is an API Gateway HTTP API whose CORS is part of its definition, so one update_api call is enough (the REST
API of the SOW agent needed three places). The origins belong to the ingram-pipeline stack's UiOrigins parameter: a
later `sam deploy` resets them to that parameter, so pass the same origins there to make them permanent.

    python3 scripts/update_cors.py --stack ingram-pipeline --origins https://main.xxxx.amplifyapp.com,http://localhost:5173
"""
import argparse
import sys
from urllib.parse import urlparse

import boto3


def check_origins(raw: str) -> list[str]:
    """Validated origins from a comma-separated list, without trailing slashes. Raises ValueError on a bad one."""
    wanted = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    for origin in wanted:
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or '"' in origin:
            raise ValueError(f"not an origin: {origin}")
    return wanted


def merge(current: list[str], wanted: list[str]) -> list[str]:
    """Current origins first, then any new ones; a wildcard already allows everything and is kept alone."""
    if "*" in current:
        return current
    return current + [o for o in wanted if o not in current]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--origins", required=True, help="comma-separated origins to allow")
    ap.add_argument("--region")
    args = ap.parse_args()
    try:
        wanted = check_origins(args.origins)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    cfn = boto3.client("cloudformation", region_name=args.region)
    resources = cfn.describe_stack_resources(StackName=args.stack)["StackResources"]
    api_id = next((r["PhysicalResourceId"] for r in resources if r["ResourceType"] == "AWS::ApiGatewayV2::Api"), None)
    if api_id is None:
        print(f"stack {args.stack} has no HTTP API", file=sys.stderr)
        return 1

    gateway = boto3.client("apigatewayv2", region_name=args.region)
    cors = gateway.get_api(ApiId=api_id).get("CorsConfiguration") or {}
    current = list(cors.get("AllowOrigins") or [])
    merged = merge(current, wanted)
    if merged == current:
        print("API CORS: origins already allowed")
        return 0
    gateway.update_api(ApiId=api_id, CorsConfiguration={
        "AllowOrigins": merged,
        "AllowHeaders": cors.get("AllowHeaders") or ["content-type"],
        "AllowMethods": cors.get("AllowMethods") or ["GET", "POST", "OPTIONS"],
        "MaxAge": cors.get("MaxAge", 600),
    })
    print(f"API CORS: added {[o for o in merged if o not in current]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
