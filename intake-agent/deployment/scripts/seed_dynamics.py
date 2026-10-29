"""Load the dummy opportunities (scripts/dynamics_seed_data.py) into Dynamics 365 through the Dataverse Web API.

Works against the mock Dataverse (default: its URL and credentials are read from the deployed stack) or a real
Dynamics environment (--real: values from config.env). Uses the same client as the agent (shared/python/dynamics.py).
Idempotent: an opportunity whose name already exists is skipped. Every opportunity has its intake form answers in
the ingr_ columns (create them first in real Dynamics: scripts/create_dynamics_columns.py).

    cd deployment && python3 scripts/seed_dynamics.py            # mock Dynamics (after deploy.sh)
    python3 scripts/seed_dynamics.py --real                      # real Dynamics 365 (config.env DYNAMICS_*)
    python3 scripts/seed_dynamics.py --list                      # only list what is there
    python3 scripts/seed_dynamics.py --reset                     # mock only: delete everything, load again
    python3 scripts/seed_dynamics.py --prune                     # mock only: delete opportunities that are not in the seed list
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "shared" / "python"), str(ROOT / "scripts")]

parser = argparse.ArgumentParser()
parser.add_argument("--real", action="store_true", help="seed the real Dynamics in config.env instead of the mock")
parser.add_argument("--list", action="store_true", help="only list the opportunities")
parser.add_argument("--reset", action="store_true", help="mock only: delete all opportunities first, then load")
parser.add_argument("--prune", action="store_true",
                    help="mock only: delete every opportunity that is not in the seed list (and its saved funding decisions)")
args = parser.parse_args()

config = {}
for line in (ROOT / "config.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        config[k.strip()] = v.strip().strip('"')
region = config.get("AWS_REGION", "us-east-1")
stack = config.get("STACK_NAME", "ingram-intake")


def aws(*cmd: str) -> str:
    return subprocess.run(["aws", *cmd, "--region", region, "--output", "text"], check=True,
                          capture_output=True, text=True).stdout.strip()


if args.real:
    if not config.get("DYNAMICS_URL") or config["DYNAMICS_URL"] == "mock":
        sys.exit("config.env: set DYNAMICS_URL to your Dynamics environment for --real")
    url, client_id, secret = config["DYNAMICS_URL"].rstrip("/"), config["DYNAMICS_CLIENT_ID"], config["DYNAMICS_CLIENT_SECRET"]
    os.environ.update(DYNAMICS_URL=url, DYNAMICS_CLIENT_ID=client_id,
                      DYNAMICS_TENANT_ID=config.get("DYNAMICS_TENANT_ID") or config["ENTRA_TENANT_ID"])
else:
    out = lambda key: aws("cloudformation", "describe-stacks", "--stack-name", stack,  # noqa: E731
                          "--query", f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue")
    url = out("MockDynamicsUrl")
    if not url or url == "None":
        sys.exit("The stack has no mock Dynamics. Set DYNAMICS_URL=mock in config.env and run ./scripts/deploy.sh")
    client_id = out("MockDynamicsClientId")
    secret = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", out("MockDynamicsSecretArn"),
                            "--query", "SecretString"))["client_secret"]
    os.environ.update(DYNAMICS_URL=url, DYNAMICS_CLIENT_ID=client_id, DYNAMICS_MOCK="true",
                      DYNAMICS_TOKEN_URL=f"{url}/oauth2/v2.0/token")

import dynamics  # noqa: E402
from dynamics_seed_data import OPPORTUNITIES, load  # noqa: E402

dynamics._client_secret = lambda: secret
print(f"{'Mock' if not args.real else 'Real'} Dynamics 365: {url}")

try:
    existing = {o["name"]: o for o in dynamics.search_opportunities("", 100, "all")}
except dynamics.DynamicsError as exc:
    sys.exit(f"Could not read opportunities: {exc}")
if args.list:
    for o in existing.values():
        print(f"  {o['state']:5} {o['name'][:55]:55} {o['customer'][:22]:22} {o['estimated_value']}")
    sys.exit(0)

if args.reset:
    if args.real:
        sys.exit("--reset only works on the mock Dynamics (it deletes every opportunity)")
    for o in existing.values():
        dynamics.delete_opportunity(o["id"])
    print(f"  deleted {len(existing)} opportunities and their notes")
if args.prune:
    if args.real:
        sys.exit("--prune only works on the mock Dynamics (it deletes opportunities)")
    import boto3

    table = boto3.resource("dynamodb", region_name=region).Table(config.get("RUNS_TABLE", "ingram-intake-runs"))
    keep = {name for name, *_ in OPPORTUNITIES}
    extra = [o for o in existing.values() if o["name"] not in keep]
    for o in extra:
        dynamics.delete_opportunity(o["id"])
        table.delete_item(Key={"pk": f"FUNDLATEST#{o['id']}", "sk": "META"})   # also drops it from Live Assist's list
        for item in table.query(KeyConditionExpression="pk = :p",
                                ExpressionAttributeValues={":p": f"FUNDOPP#{o['id']}"})["Items"]:
            table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        print(f"  - {o['name']}")
    print(f"  deleted {len(extra)} opportunities that are not in the seed list")
created = load(dynamics, real=args.real)
print(f"{created} created, {len(OPPORTUNITIES) - created} already there. Open the Funding Desk to check them.")
