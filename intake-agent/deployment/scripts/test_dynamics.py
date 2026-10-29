"""Check the Dynamics 365 connection with the values in config.env, before deploying. Read-only.

    cd deployment && python3 scripts/test_dynamics.py

Signs in as the app (client credentials), lists the 5 newest opportunities and reads the first one with its
notes, using the same code as the API and the agent (shared/python/dynamics.py). Nothing is created or changed.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))

config = {}
for line in (ROOT / "config.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip('"')

url = config.get("DYNAMICS_URL", "").rstrip("/")
tenant = config.get("DYNAMICS_TENANT_ID") or config.get("ENTRA_TENANT_ID", "")
missing = [k for k, v in {"DYNAMICS_URL": url, "DYNAMICS_CLIENT_ID": config.get("DYNAMICS_CLIENT_ID"),
                          "DYNAMICS_CLIENT_SECRET": config.get("DYNAMICS_CLIENT_SECRET"),
                          "DYNAMICS_TENANT_ID or ENTRA_TENANT_ID": tenant}.items() if not v]
if missing:
    sys.exit(f"config.env: set {', '.join(missing)}")
os.environ.update(DYNAMICS_URL=url, DYNAMICS_TENANT_ID=tenant, DYNAMICS_CLIENT_ID=config["DYNAMICS_CLIENT_ID"])

import dynamics  # noqa: E402

dynamics._client_secret = lambda: config["DYNAMICS_CLIENT_SECRET"]   # local test: the secret from config.env


def step(name, fn):
    try:
        result = fn()
        print(f"  OK    {name}")
        return result
    except dynamics.DynamicsError as exc:
        print(f"  FAIL  {name}: {exc}")
        sys.exit(1)


print(f"Dynamics 365: {url}  (tenant {tenant}, app {config['DYNAMICS_CLIENT_ID']})")
step("sign in as the app (client credentials)", dynamics._access_token)
who, _ = step("connect to Dataverse (WhoAmI)", lambda: dynamics._request("GET", "WhoAmI"))
print(f"        application user id: {who.get('UserId')}")
opps = step("read opportunities", lambda: dynamics.search_opportunities("", 5, "all"))
for o in opps:
    print(f"        {o['created_on'][:10] if o['created_on'] else '':10}  {o['name'][:50]:50}  {o['customer'][:25]}")
if opps:
    o = step("read one opportunity with its notes", lambda: dynamics.get_opportunity(opps[0]["id"]))
    print(f"        '{o['name'][:60]}': {len(o['notes'])} notes, {len(o['answers'])} intake form answers")
else:
    print("        no opportunities yet (the connection works; create one in Dynamics or with the form)")
print("\nConnection works. Run ./scripts/deploy.sh to switch the Funding Desk from the demo store to Dynamics.")
