#!/usr/bin/env python3
"""Deploy the Discovery agent onto the AWS resources that already exist (see config.existing.env).

    uv run --no-project --python 3.12 --with boto3==1.43.114 python scripts/deploy_existing.py [--plan] [--only STEP ...]

Steps, in this order (each is idempotent; an unchanged step is skipped):
  code     build the agent zip (scripts/package_agent.sh) and upload it to s3://<bucket>/code/ (content-addressed key)
  runtime  point the AgentCore Runtime at the new zip (entry point main.py) and set its environment; wait for READY
  iam      the API Lambda role: add the history/ and guard/ access the S3 store needs (existing statements are kept)
  bucket   lifecycle rule: expire guard/ after 3 days (existing rules are kept)
  api      Lambda code (handler.py + pipeline.py + overview.py) + environment, the routes the UI needs (GET /session, GET /runs, the
           pipeline routes), CORS (x-access-key for the pipeline), stage throttling
  ui       Amplify Hosting app (the only resource created, and only if missing) + the static UI
  smoke    ping the runtime (0 model calls) and call GET /session through the API

Order matters: the new runtime still accepts the old Lambda's requests, so a failure after `runtime` leaves a working
system. `--plan` only reads AWS and prints what each step would change. No document or run is touched.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
STEPS = ["code", "runtime", "iam", "bucket", "api", "ui", "smoke"]
POLICY_NAME = "discovery-api-inline"
GUARD_RULE = "expire-guard"
UI_FILES = ["index.html", "styles.css", "app.js", "dom.js", "overview.js", "report.js", "pipeline.js", "config.js"]
AMPLIFY_HEADERS = """customHeaders:
  - pattern: '**'
    headers:
      - key: Strict-Transport-Security
        value: max-age=31536000; includeSubDomains
      - key: X-Content-Type-Options
        value: nosniff
      - key: X-Frame-Options
        value: SAMEORIGIN
      - key: Referrer-Policy
        value: strict-origin-when-cross-origin
  - pattern: 'config.js'
    headers:
      - key: Cache-Control
        value: no-cache
"""


def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in (ROOT / "config.existing.env").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip()
    for key in cfg:
        cfg[key] = os.environ.get(key, cfg[key])
    return cfg


class Deploy:
    def __init__(self, cfg: dict[str, str], plan: bool) -> None:
        self.cfg, self.plan = cfg, plan
        self.region = cfg["AWS_REGION"]
        self.s3 = boto3.client("s3", region_name=self.region)
        self.iam = boto3.client("iam")
        self.lam = boto3.client("lambda", region_name=self.region)
        self.secrets = boto3.client("secretsmanager", region_name=self.region)
        self.apigw = boto3.client("apigatewayv2", region_name=self.region)
        self.amplify = boto3.client("amplify", region_name=self.region)
        self.control = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.bucket = cfg["DATA_BUCKET"]
        account = boto3.client("sts").get_caller_identity()["Account"]
        if cfg.get("EXPECTED_ACCOUNT") and account != cfg["EXPECTED_ACCOUNT"]:
            sys.exit(f"Wrong AWS account {account}; config.existing.env expects {cfg['EXPECTED_ACCOUNT']}.")
        self.account = account
        self.code_key = ""
        self.runtime = self.control.get_agent_runtime(agentRuntimeId=cfg["RUNTIME_ID"])
        self.kms_arn = self.s3.get_bucket_encryption(Bucket=self.bucket)["ServerSideEncryptionConfiguration"][
            "Rules"][0]["ApplyServerSideEncryptionByDefault"]["KMSMasterKeyID"]
        self.api_url = f"https://{cfg['API_ID']}.execute-api.{self.region}.amazonaws.com"

    def say(self, step: str, text: str) -> None:
        print(f"[{step}] {'(plan) ' if self.plan else ''}{text}", flush=True)

    # ------------------------------------------------------------------ code
    def code_hash(self) -> str:
        agent = ROOT / "agent"
        files = [agent / "requirements.txt", agent / "main.py"]
        for folder in (agent / "discovery_agent", agent / "schema"):
            files += sorted(p for p in folder.rglob("*")
                            if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store")
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(agent)).encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def step_code(self) -> None:
        self.code_key = f"code/discovery_agent_{self.code_hash()}.zip"
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self.code_key)
            return self.say("code", f"s3://{self.bucket}/{self.code_key} already uploaded (code unchanged)")
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                raise
        if self.plan:
            return self.say("code", f"would build and upload s3://{self.bucket}/{self.code_key}")
        zip_path = subprocess.run([str(ROOT / "scripts" / "package_agent.sh")], check=True, text=True,
                                  stdout=subprocess.PIPE).stdout.strip().splitlines()[-1]
        self.s3.upload_file(zip_path, self.bucket, self.code_key)
        self.say("code", f"uploaded s3://{self.bucket}/{self.code_key}")

    # ------------------------------------------------------------------ runtime
    def runtime_env(self) -> dict[str, str]:
        return {"AWS_REGION": self.region, "DATA_BUCKET": self.bucket, "KMS_KEY_ID": self.kms_arn}

    def step_runtime(self) -> None:
        current = self.runtime
        code = current["agentRuntimeArtifact"]["codeConfiguration"]
        want_env = self.runtime_env()
        same = (code["code"]["s3"]["prefix"] == self.code_key and code["entryPoint"] == ["main.py"]
                and current.get("environmentVariables") == want_env)
        if same:
            return self.say("runtime", f"already on {self.code_key} (version {current['agentRuntimeVersion']})")
        self.say("runtime", f"{code['code']['s3']['prefix']} (entry {code['entryPoint'][0]}) -> {self.code_key} (entry main.py)")
        if self.plan:
            return
        args = {
            "agentRuntimeId": self.cfg["RUNTIME_ID"],
            "agentRuntimeArtifact": {"codeConfiguration": {
                "code": {"s3": {"bucket": self.bucket, "prefix": self.code_key}},
                "runtime": code["runtime"], "entryPoint": ["main.py"]}},
            "environmentVariables": want_env,
        }
        for key in ("roleArn", "networkConfiguration", "lifecycleConfiguration", "protocolConfiguration",
                    "authorizerConfiguration", "requestHeaderConfiguration", "metadataConfiguration", "description"):
            if key in current:
                args[key] = current[key]
        self.control.update_agent_runtime(**args)
        deadline = time.time() + 600
        while time.time() < deadline:
            state = self.control.get_agent_runtime(agentRuntimeId=self.cfg["RUNTIME_ID"])
            if state["status"] == "READY" and state["agentRuntimeVersion"] != current["agentRuntimeVersion"]:
                self.runtime = state
                return self.say("runtime", f"READY, version {state['agentRuntimeVersion']}")
            if state["status"].endswith("FAILED"):
                sys.exit(f"Runtime update failed: {state.get('failureReason')}")
            time.sleep(5)
        sys.exit("Runtime did not become READY within 10 minutes.")

    # ------------------------------------------------------------------ iam
    def live_assist_key_statements(self) -> list[dict]:
        """Read access to the one secret that holds the Live Assist access key (and its KMS key, if it has one)."""
        name = self.cfg.get("LIVE_ASSIST_KEY_SECRET")
        if not name:
            return []
        secret = self.secrets.describe_secret(SecretId=name)
        out = [{"Sid": "ReadLiveAssistAccessKey", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
                "Resource": secret["ARN"]}]
        if secret.get("KmsKeyId"):
            out.append({"Sid": "DecryptLiveAssistAccessKey", "Effect": "Allow", "Action": "kms:Decrypt",
                        "Resource": secret["KmsKeyId"]})
        return out

    def api_policy(self) -> dict:
        b, arn = self.bucket, self.runtime["agentRuntimeArn"]
        acct_log = f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/{self.cfg['LAMBDA_NAME']}:*"
        return {"Version": "2012-10-17", "Statement": [
            {"Sid": "InvokeTheRuntime", "Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
             "Resource": [arn, f"{arn}/runtime-endpoint/*"]},
            {"Sid": "SignUploadsToInput", "Effect": "Allow", "Action": "s3:PutObject",
             "Resource": f"arn:aws:s3:::{b}/input/*"},
            {"Sid": "ReadRunContextFromInput", "Effect": "Allow", "Action": "s3:GetObject",
             "Resource": f"arn:aws:s3:::{b}/input/*/_context.json"},
            {"Sid": "ReadOutputsForStatusAndResults", "Effect": "Allow", "Action": "s3:GetObject",
             "Resource": f"arn:aws:s3:::{b}/output/*"},
            {"Sid": "ClarificationFilesInWork", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": f"arn:aws:s3:::{b}/work/*"},
            {"Sid": "RunHistoryAndCostGuards", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": [f"arn:aws:s3:::{b}/history/*", f"arn:aws:s3:::{b}/guard/*"]},
            {"Sid": "ListPrefixesSoMissingKeysReturnNoSuchKey", "Effect": "Allow", "Action": "s3:ListBucket",
             "Resource": f"arn:aws:s3:::{b}",
             "Condition": {"StringLike": {"s3:prefix": ["input/*", "output/*", "history/*", "guard/*"]}}},
            {"Sid": "KmsForPresignedKmsUploadsAndReads", "Effect": "Allow",
             "Action": ["kms:GenerateDataKey", "kms:Decrypt"], "Resource": self.kms_arn},
            {"Sid": "Logs", "Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], "Resource": acct_log},
            *self.live_assist_key_statements(),
        ]}

    def step_iam(self) -> None:
        role, want = self.cfg["API_ROLE_NAME"], self.api_policy()
        have = self.iam.get_role_policy(RoleName=role, PolicyName=POLICY_NAME)["PolicyDocument"]
        if have == want:
            return self.say("iam", f"{role}/{POLICY_NAME} already up to date")
        sids = lambda doc: {s["Sid"] for s in doc["Statement"]}  # noqa: E731
        self.say("iam", f"{role}/{POLICY_NAME}: add {sorted(sids(want) - sids(have))}, update the rest")
        if not self.plan:
            self.iam.put_role_policy(RoleName=role, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(want))
            time.sleep(8)  # IAM changes take a moment to reach the Lambda

    # ------------------------------------------------------------------ bucket
    def step_bucket(self) -> None:
        config = self.s3.get_bucket_lifecycle_configuration(Bucket=self.bucket)
        rules = config["Rules"]
        if any(r["ID"] == GUARD_RULE for r in rules):
            return self.say("bucket", f"lifecycle rule {GUARD_RULE} already present")
        self.say("bucket", f"add lifecycle rule {GUARD_RULE} (guard/ expires after 3 days); keep {[r['ID'] for r in rules]}")
        if not self.plan:
            rules.append({"ID": GUARD_RULE, "Status": "Enabled", "Filter": {"Prefix": "guard/"},
                          "Expiration": {"Days": 3}, "NoncurrentVersionExpiration": {"NoncurrentDays": 1}})
            self.s3.put_bucket_lifecycle_configuration(
                Bucket=self.bucket, LifecycleConfiguration={"Rules": rules},
                **({"TransitionDefaultMinimumObjectSize": config["TransitionDefaultMinimumObjectSize"]}
                   if "TransitionDefaultMinimumObjectSize" in config else {}))

    # ------------------------------------------------------------------ api (Lambda + routes + throttling)
    def lambda_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("handler.py", "pipeline.py", "overview.py"):
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))  # fixed time: same code, same hash
                info.external_attr = 0o644 << 16
                z.writestr(info, (ROOT / "api" / name).read_bytes())
        return buf.getvalue()

    def step_api(self) -> None:
        name = self.cfg["LAMBDA_NAME"]
        conf = self.lam.get_function_configuration(FunctionName=name)
        blob = self.lambda_zip()
        sha = base64.b64encode(hashlib.sha256(blob).digest()).decode()
        env = {"DATA_BUCKET": self.bucket, "AGENT_RUNTIME_ARN": self.runtime["agentRuntimeArn"],
               "KMS_KEY_ID": (conf.get("Environment", {}).get("Variables", {}).get("KMS_KEY_ID")
                              or self.kms_arn.rsplit("/", 1)[-1]),
               "RUNS_PER_DAY": self.cfg["RUNS_PER_DAY"], "RUNS_PER_IP_HOUR": self.cfg["RUNS_PER_IP_HOUR"]}
        if self.cfg.get("LIVE_ASSIST_API_URL"):   # pipeline: Intake + Live Assist opportunities (api/pipeline.py)
            env["LIVE_ASSIST_API_URL"] = self.cfg["LIVE_ASSIST_API_URL"]
        if self.cfg.get("LIVE_ASSIST_KEY_SECRET"):   # the access key is read from here; users no longer type it
            env["LIVE_ASSIST_KEY_SECRET"] = self.cfg["LIVE_ASSIST_KEY_SECRET"]
        if conf["CodeSha256"] != sha:
            self.say("api", f"Lambda {name}: update code ({conf['CodeSize']} -> {len(blob)} bytes)")
            if not self.plan:
                self.lam.update_function_code(FunctionName=name, ZipFile=blob)
                self.lam.get_waiter("function_updated").wait(FunctionName=name)
        else:
            self.say("api", f"Lambda {name}: code unchanged")
        if conf.get("Environment", {}).get("Variables") != env:
            self.say("api", f"Lambda {name}: environment {sorted(conf.get('Environment', {}).get('Variables', {}))} -> {sorted(env)}")
            if not self.plan:
                self.lam.update_function_configuration(FunctionName=name, Environment={"Variables": env})
                self.lam.get_waiter("function_updated").wait(FunctionName=name)
        else:
            self.say("api", f"Lambda {name}: environment unchanged")

        api = self.cfg["API_ID"]
        integration = self.apigw.get_integrations(ApiId=api)["Items"][0]["IntegrationId"]
        routes = {r["RouteKey"] for r in self.apigw.get_routes(ApiId=api)["Items"]}
        for key in ("GET /session", "GET /runs", "GET /pipeline/opportunities", "POST /pipeline/opportunities/{id}/import",
                    "GET /runs/{run_id}/overview", "GET /runs/{run_id}/report", "GET /pipeline/overview"):
            if key not in routes:
                self.say("api", f"add route {key}")
                if not self.plan:
                    self.apigw.create_route(ApiId=api, RouteKey=key, Target=f"integrations/{integration}")
        # the pipeline sends the user's Live Assist key in X-Access-Key; the browser must be allowed to send it
        cors = self.apigw.get_api(ApiId=api).get("CorsConfiguration") or {}
        headers = sorted({h.lower() for h in cors.get("AllowHeaders") or []} | {"content-type", "x-access-key"})
        if sorted(h.lower() for h in cors.get("AllowHeaders") or []) != headers:
            self.say("api", f"CORS allow headers -> {headers}")
            if not self.plan:
                self.apigw.update_api(ApiId=api, CorsConfiguration={**cors, "AllowHeaders": headers})
        # the throttling the template applies: runs and answers cost money, uploads cost storage
        stage = self.apigw.get_stage(ApiId=api, StageName="$default")
        costly = {"POST /runs": 3, "POST /runs/{run_id}/answers": 3, "POST /engagements/{name}/upload-urls": 5,
                  "POST /pipeline/opportunities/{id}/import": 3}
        rates = {"POST /runs": 1, "POST /runs/{run_id}/answers": 1, "POST /engagements/{name}/upload-urls": 2,
                 "POST /pipeline/opportunities/{id}/import": 1}
        want_routes = {k: {"ThrottlingBurstLimit": costly[k], "ThrottlingRateLimit": rates[k]} for k in costly}
        want_default = {"ThrottlingBurstLimit": 20, "ThrottlingRateLimit": 10, "DetailedMetricsEnabled": False}
        if stage.get("RouteSettings") != want_routes or stage.get("DefaultRouteSettings") != want_default:
            self.say("api", f"stage throttling: default 10/s, {sorted(want_routes)}")
            if not self.plan:
                self.apigw.update_stage(ApiId=api, StageName="$default", RouteSettings=want_routes,
                                        DefaultRouteSettings=want_default)
        else:
            self.say("api", "stage throttling unchanged")

    # ------------------------------------------------------------------ ui (Amplify)
    def find_app(self) -> dict | None:
        for page in self.amplify.get_paginator("list_apps").paginate():
            for app in page["apps"]:
                if app["name"] == self.cfg["AMPLIFY_APP_NAME"]:
                    return app
        return None

    def ui_zip(self) -> bytes:
        """The UI with its API URL and a Content-Security-Policy pinned to this API and the data bucket."""
        data_origin = f"https://{self.bucket}.s3.{self.region}.amazonaws.com"
        html = (ROOT / "ui" / "index.html").read_text()
        connect = f"connect-src 'self' {self.api_url} {data_origin};"
        html, n = re.subn(r"connect-src 'self' [^;]*;", connect, html)
        if n != 1:
            sys.exit("Could not set connect-src in ui/index.html")
        config = ('// Generated by scripts/deploy_existing.py. Contains no secrets.\n'
                  f'window.DISCOVERY_CONFIG = {{\n  apiUrl: "{self.api_url}",\n}};\n')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in UI_FILES:
                body = html if name == "index.html" else config if name == "config.js" else (ROOT / "ui" / name).read_text()
                z.writestr(name, body)
        return buf.getvalue()

    def step_ui(self) -> None:
        app = self.find_app()
        if app is None:
            self.say("ui", f"create Amplify app {self.cfg['AMPLIFY_APP_NAME']} (branch main)")
            if self.plan:
                return
            app = self.amplify.create_app(name=self.cfg["AMPLIFY_APP_NAME"], platform="WEB", customHeaders=AMPLIFY_HEADERS,
                                          description="Ingram Discovery agent web UI (static; uploaded by scripts/deploy_existing.py)",
                                          tags={"Project": "ingram-discovery"})["app"]
        app_id = app["appId"]
        branches = [b["branchName"] for b in self.amplify.list_branches(appId=app_id)["branches"]]
        if "main" not in branches:
            self.say("ui", "create branch main")
            if not self.plan:
                self.amplify.create_branch(appId=app_id, branchName="main", stage="PRODUCTION", enableAutoBuild=False)
        url = f"https://main.{app['defaultDomain']}"
        self.say("ui", f"deploy ui/ to {url}")
        if self.plan:
            return
        job = self.amplify.create_deployment(appId=app_id, branchName="main")
        req = urllib.request.Request(job["zipUploadUrl"], data=self.ui_zip(), method="PUT",
                                     headers={"Content-Type": "application/zip"})
        urllib.request.urlopen(req, timeout=120).read()
        self.amplify.start_deployment(appId=app_id, branchName="main", jobId=job["jobId"])
        for _ in range(60):
            status = self.amplify.get_job(appId=app_id, branchName="main", jobId=job["jobId"])["job"]["summary"]["status"]
            if status == "SUCCEED":
                return self.say("ui", f"published: {url}")
            if status in ("FAILED", "CANCELLED"):
                sys.exit(f"Amplify deployment {status}")
            time.sleep(5)
        sys.exit("Amplify deployment timed out")

    # ------------------------------------------------------------------ smoke
    def step_smoke(self) -> None:
        if self.plan:
            return self.say("smoke", "would ping the runtime and call GET /session")
        client = boto3.client("bedrock-agentcore", region_name=self.region,
                              config=Config(read_timeout=120, retries={"max_attempts": 2, "mode": "standard"}))
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime["agentRuntimeArn"], runtimeSessionId=f"discovery-smoke-{uuid.uuid4().hex}",
            qualifier="DEFAULT", contentType="application/json", accept="application/json",
            payload=json.dumps({"action": "ping"}).encode())
        answer = json.loads(resp["response"].read().decode() or "{}")
        if answer.get("status") != "ok" or answer.get("storage") != "s3":
            sys.exit(f"Runtime ping failed: {answer}")
        self.say("smoke", f"runtime ping ok (models: {answer.get('models')})")
        with urllib.request.urlopen(f"{self.api_url}/session", timeout=30) as r:
            session = json.loads(r.read())
        if not session.get("ok"):
            sys.exit(f"GET /session failed: {session}")
        self.say("smoke", f"GET /session ok: {session}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plan", action="store_true", help="read AWS and print the changes; change nothing")
    ap.add_argument("--only", nargs="+", choices=STEPS, help="run only these steps (default: all, in order)")
    args = ap.parse_args()
    d = Deploy(load_config(), args.plan)
    wanted = [s for s in STEPS if not args.only or s in args.only]
    if "runtime" in wanted and "code" not in wanted:  # the runtime step needs the key of the current code
        d.code_key = f"code/discovery_agent_{d.code_hash()}.zip"
    for step in wanted:
        getattr(d, f"step_{step}")()
    return 0


if __name__ == "__main__":
    sys.exit(main())
