"""Re-map stored funding decisions that still carry the retired statuses (needs_info, to_confirm, not_covered).

Every opportunity now maps to a programme (status "decided"), see agent/funding/rules.py. Decisions saved before that
change are recomputed from the facts they were made with (`result.facts`, no model call), so the table never holds a
retired status. Touches three kinds of items in the runs table:

    FUNDLATEST#<opp> / META      latest decision for everyone: `result.decision` and the compact `latest`
    FUNDCHK#<check> / META       one finished check: `result.decision`
    FUNDOPP#<opp> / <owner>      a user's list entry: the compact `latest`

Dynamics notes already written stay as they are. Dry run by default; nothing is written without --apply.

    cd deployment && python3 scripts/migrate_funding_statuses.py            # show what would change
    python3 scripts/migrate_funding_statuses.py --apply                     # write the new decisions
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from funding.rules import decide  # noqa: E402

RETIRED = {"needs_info", "to_confirm", "not_covered"}

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
parser.add_argument("--table", default="ingram-intake-runs")
parser.add_argument("--region", default="us-east-1")
args = parser.parse_args()

table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)


def plain(value):
    if isinstance(value, Decimal):
        return float(value) if value % 1 else int(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def dynamo(value):
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def scan(prefix: str):
    kwargs = {"FilterExpression": Attr("pk").begins_with(prefix)}
    while True:
        page = table.scan(**kwargs)
        yield from page["Items"]
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def needs_migration(decision: dict) -> bool:
    return decision.get("status") in RETIRED or "assumptions" not in decision


def recompute(result: dict) -> dict:
    facts = {f["fact"]: f.get("value") for f in result.get("facts", []) if f.get("used")}
    return decide(plain(facts))


def compact(old: dict, decision: dict) -> dict:
    return {**old, "status": decision["status"], "program": decision["program"], "funding": decision["funding"],
            "total_usd": decision["total_usd"], "arr_usd": decision["arr_usd"],
            "assumed": [a["fact"] for a in decision["assumptions"]]}


changed = {"FUNDLATEST#": 0, "FUNDCHK#": 0, "FUNDOPP#": 0}
new_by_opp: dict[str, dict] = {}

for prefix in ("FUNDLATEST#", "FUNDCHK#"):
    for item in scan(prefix):
        result = plain(item.get("result") or {})
        old = result.get("decision") or {}
        if not old or not needs_migration(old):
            continue
        new = recompute(result)
        name = (result.get("opportunity") or {}).get("name", item["pk"])
        print(f"{prefix:12} {name[:46]:46} {old.get('status')}/{old.get('program')} -> {new['status']}/{new['program']}"
              f" ({new['total_usd']})")
        changed[prefix] += 1
        if prefix == "FUNDLATEST#":
            new_by_opp[item["pk"].split("#", 1)[1]] = new
        if not args.apply:
            continue
        if prefix == "FUNDLATEST#":
            table.update_item(Key={"pk": item["pk"], "sk": item["sk"]},
                              UpdateExpression="SET #r.#d = :d, latest = :l",
                              ExpressionAttributeNames={"#r": "result", "#d": "decision"},
                              ExpressionAttributeValues={":d": dynamo(new), ":l": dynamo(compact(plain(item.get("latest") or {}), new))})
        else:
            table.update_item(Key={"pk": item["pk"], "sk": item["sk"]}, UpdateExpression="SET #r.#d = :d",
                              ExpressionAttributeNames={"#r": "result", "#d": "decision"},
                              ExpressionAttributeValues={":d": dynamo(new)})

for item in scan("FUNDOPP#"):
    latest = plain(item.get("latest") or {})
    new = new_by_opp.get(item["pk"].split("#", 1)[1])
    if not new or latest.get("status") not in RETIRED:
        continue
    changed["FUNDOPP#"] += 1
    if args.apply:
        table.update_item(Key={"pk": item["pk"], "sk": item["sk"]}, UpdateExpression="SET latest = :l",
                          ExpressionAttributeValues={":l": dynamo(compact(latest, new))})

print(("Updated " if args.apply else "Would update ") + ", ".join(f"{n} {k}" for k, n in changed.items()))
if not args.apply:
    print("Dry run: nothing was written. Re-run with --apply to write.")
