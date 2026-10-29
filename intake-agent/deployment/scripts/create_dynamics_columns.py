"""Create the intake form columns (one ingr_ column per question) on the Dataverse `opportunity` table.

Real Dynamics 365 only (the mock already has them). Uses the Dataverse metadata Web API with the app user from
config.env; that app user needs the System Customizer (or System Administrator) role for this one-time setup.

    cd deployment
    python3 scripts/create_dynamics_columns.py                 # dry run: list what would be created
    python3 scripts/create_dynamics_columns.py --apply         # create the missing columns, then publish

Needs a solution publisher with the customization prefix "ingr" (Power Apps > Solutions > Publishers); pass its
solution's unique name with --solution so the columns are added to that solution. Existing columns are skipped,
so it is safe to run again after adding questions to shared/python/opportunity_form.py.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))

import opportunity_form as form  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="create the columns (default: dry run)")
parser.add_argument("--solution", default="", help="unique name of the solution to add the columns to")
args = parser.parse_args()

LANG = 1033
MAX_LEN = {"Text": 300, "Email": 300, "Phone": 50, "MultilineText": 8000}


def label(text: str) -> dict:
    return {"@odata.type": "Microsoft.Dynamics.CRM.Label",
            "LocalizedLabels": [{"@odata.type": "Microsoft.Dynamics.CRM.LocalizedLabel", "Label": text[:100],
                                 "LanguageCode": LANG}]}


def definition(c: dict) -> dict:
    """Dataverse attribute metadata for one column (opportunity_form.COLUMNS entry)."""
    base = {"SchemaName": c["column"], "DisplayName": label(c["label"].strip()), "Description": label(c["label"].strip()[:250]),
            "RequiredLevel": {"Value": "None", "CanBeChanged": True, "ManagedPropertyLogicalName": "canmodifyrequirementlevelsettings"}}
    kind = c["column_type"]
    if kind == "Choice":
        return {**base, "@odata.type": "Microsoft.Dynamics.CRM.PicklistAttributeMetadata",
                "OptionSet": {"@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata", "IsGlobal": False,
                              "OptionSetType": "Picklist",
                              "Options": [{"Value": form.CHOICE_BASE + i, "Label": label(o)} for i, o in enumerate(c["options"])]}}
    if kind == "WholeNumber":
        return {**base, "@odata.type": "Microsoft.Dynamics.CRM.IntegerAttributeMetadata", "Format": "None",
                "MinValue": 0, "MaxValue": 2147483647}
    if kind == "Decimal":
        return {**base, "@odata.type": "Microsoft.Dynamics.CRM.DecimalAttributeMetadata", "Precision": 2,
                "MinValue": 0, "MaxValue": 100000000000}
    if kind in ("DateOnly", "DateTime"):
        return {**base, "@odata.type": "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata",
                "Format": "DateOnly" if kind == "DateOnly" else "DateAndTime",
                "DateTimeBehavior": {"Value": "DateOnly" if kind == "DateOnly" else "UserLocal"}}
    if kind == "MultilineText":
        return {**base, "@odata.type": "Microsoft.Dynamics.CRM.MemoAttributeMetadata", "Format": "TextArea",
                "MaxLength": MAX_LEN[kind]}
    return {**base, "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
            "FormatName": {"Value": {"Email": "Email", "Phone": "Phone"}.get(kind, "Text")}, "MaxLength": MAX_LEN[kind]}


print(f"{len(form.COLUMNS)} intake form columns on the opportunity table:")
counts: dict[str, int] = {}
for c in form.COLUMNS:
    counts[c["column_type"]] = counts.get(c["column_type"], 0) + 1
print("  " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda x: -x[1])))
if not args.apply:
    for c in form.COLUMNS:
        print(f"  {c['column']:30} {c['column_type']:13} {c['label'].strip()[:70]}")
    print("\nDry run. Run with --apply to create the missing columns in the Dynamics environment in config.env.")
    sys.exit(0)

config = {}
for line in (ROOT / "config.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        config[k.strip()] = v.strip().strip('"')
if not config.get("DYNAMICS_URL", "").startswith("https://"):
    sys.exit("config.env: DYNAMICS_URL must be your real Dynamics environment (the mock already has the columns)")
os.environ.update(DYNAMICS_URL=config["DYNAMICS_URL"].rstrip("/"), DYNAMICS_CLIENT_ID=config["DYNAMICS_CLIENT_ID"],
                  DYNAMICS_TENANT_ID=config.get("DYNAMICS_TENANT_ID") or config["ENTRA_TENANT_ID"])

import dynamics  # noqa: E402

dynamics._client_secret = lambda: config["DYNAMICS_CLIENT_SECRET"]
existing, _ = dynamics._request("GET", "EntityDefinitions(LogicalName='opportunity')/Attributes?$select=LogicalName")
have = {a["LogicalName"] for a in existing.get("value", [])}
todo = [c for c in form.COLUMNS if c["column"] not in have]
print(f"{len(form.COLUMNS) - len(todo)} already exist, creating {len(todo)}")
headers = {"MSCRM.SolutionUniqueName": args.solution} if args.solution else {}
for c in todo:
    try:
        dynamics._request("POST", "EntityDefinitions(LogicalName='opportunity')/Attributes", definition(c),
                          extra_headers=headers)
        print(f"  + {c['column']}")
    except dynamics.DynamicsError as exc:
        print(f"  ! {c['column']}: {exc}")
dynamics._request("POST", "PublishXml", {"ParameterXml": "<importexportxml><entities><entity>opportunity</entity>"
                                                         "</entities></importexportxml>"})
print("Published the opportunity table. Add the columns to the Opportunity main form in Power Apps if sellers "
      "should see them there.")
