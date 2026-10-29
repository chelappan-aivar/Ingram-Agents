#!/usr/bin/env python3
"""Allow extra browser origins on the existing SowGenAgent API (additive; never removes an origin).

The backend answers CORS in two places, both updated here:
  1. the invoker Lambda (env CORS_ALLOWED_ORIGINS) for the POST responses, and
  2. the MOCK OPTIONS integrations of API Gateway (a VTL template that echoes allow-listed origins),
     followed by a new deployment of the stage.

Note: these resources belong to the CDK stack SowGenAgent; a later `cdk deploy` of that stack resets them.
Add the origins to the CDK config too to make them permanent.

    python3 scripts/update_cors.py --stack SowGenAgent --origins https://main.xxxx.amplifyapp.com,http://localhost:5173
"""
import argparse
import re
import sys
from urllib.parse import urlparse

import boto3

ORIGIN_RE = re.compile(r'\$origin == "([^"]+)"')
STATIC_ORIGIN = "method.response.header.Access-Control-Allow-Origin"


def vtl(origins: list[str]) -> str:
    """The same template the CDK stack generates: echo the request origin when it is allow-listed."""
    cond = " || ".join(f'$origin == "{o}"' for o in origins)
    return (
        '#set($origin = $input.params().header.get("Origin"))\n'
        '#if($origin == "")\n'
        '  #set($origin = $input.params().header.get("origin"))\n'
        "#end\n"
        f"#if({cond})\n"
        "  #set($context.responseOverride.header.Access-Control-Allow-Origin = $origin)\n"
        "#end"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", required=True)
    ap.add_argument("--origins", required=True, help="comma-separated origins to allow")
    ap.add_argument("--region")
    args = ap.parse_args()
    wanted = [o.strip().rstrip("/") for o in args.origins.split(",") if o.strip()]
    for o in wanted:
        u = urlparse(o)
        if u.scheme not in ("http", "https") or not u.netloc or u.path or '"' in o:
            print(f"not an origin: {o}", file=sys.stderr)
            return 2

    cfn = boto3.client("cloudformation", region_name=args.region)
    resources = cfn.describe_stack_resources(StackName=args.stack)["StackResources"]
    api_id = next(r["PhysicalResourceId"] for r in resources if r["ResourceType"] == "AWS::ApiGateway::RestApi")
    stage = next(r["PhysicalResourceId"] for r in resources if r["ResourceType"] == "AWS::ApiGateway::Stage")
    invoker = next(r["PhysicalResourceId"] for r in resources
                   if r["ResourceType"] == "AWS::Lambda::Function" and r["LogicalResourceId"].startswith("ApiInvoker"))

    # ---- 1. invoker Lambda
    lam = boto3.client("lambda", region_name=args.region)
    env = lam.get_function_configuration(FunctionName=invoker).get("Environment", {}).get("Variables", {})
    current = [o for o in env.get("CORS_ALLOWED_ORIGINS", "").split(",") if o]
    merged = current + [o for o in wanted if o not in current]
    if merged != current:
        env["CORS_ALLOWED_ORIGINS"] = ",".join(merged)
        lam.update_function_configuration(FunctionName=invoker, Environment={"Variables": env})
        lam.get_waiter("function_updated_v2").wait(FunctionName=invoker)
        print(f"invoker Lambda: added {[o for o in merged if o not in current]}")
    else:
        print("invoker Lambda: origins already allowed")

    # ---- 2. API Gateway OPTIONS (preflight)
    apigw = boto3.client("apigateway", region_name=args.region)
    changed = 0
    for page in apigw.get_paginator("get_resources").paginate(restApiId=api_id):
        for res in page["items"]:
            if "OPTIONS" not in (res.get("resourceMethods") or {}):
                continue
            method = apigw.get_method(restApiId=api_id, resourceId=res["id"], httpMethod="OPTIONS")
            for code, ir in (method.get("methodIntegration", {}).get("integrationResponses") or {}).items():
                templates = ir.get("responseTemplates") or {}
                tpl = templates.get("application/json", "")
                static = (ir.get("responseParameters") or {}).get(STATIC_ORIGIN, "").strip("'")
                if not tpl and not static:
                    continue
                listed = ORIGIN_RE.findall(tpl)
                new_list = listed + [o for o in wanted if o not in listed and o != static]
                if new_list == listed:
                    continue
                apigw.update_integration_response(
                    restApiId=api_id, resourceId=res["id"], httpMethod="OPTIONS", statusCode=code,
                    patchOperations=[{"op": "replace" if tpl else "add",
                                      "path": "/responseTemplates/application~1json", "value": vtl(new_list)}],
                )
                changed += 1
    if changed:
        apigw.create_deployment(restApiId=api_id, stageName=stage,
                                description="Add UI origins to CORS preflight (deployment-ui/scripts/update_cors.py)")
        print(f"API Gateway {api_id}: preflight updated on {changed} route(s), stage '{stage}' redeployed")
    else:
        print(f"API Gateway {api_id}: preflight origins already allowed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
