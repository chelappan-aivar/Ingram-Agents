"""AWS Funding Programs Decision Framework as code (AWS_Funding_Programs_Decision_Framework.pdf).

Deterministic: the same facts always give the same decision, and every decision lists the path that led to it.
The checks run in the framework's order: segment, then workload description, then AWS spend (ARR = MRR x 12).

Thresholds (USD ARR):   MAP above 500,000 · IW Migrate ceiling 264,000 · MAP Lite floor above 100,000
Boundaries: "above X" is strictly greater; a band "A to B" includes B. So exactly 500,000 is MAP Lite,
exactly 264,000 is the startup 100k-264k band, and exactly 100,000 is below the MAP Lite floor.

Every opportunity is mapped to a programme: the status is always "decided". When a fact is unknown the engine
applies the best-fit default and lists it under `assumptions` (with the question that would confirm it), so
the seller can check it. Best-fit defaults:
    segment unknown            -> non-startup (the other segment is shown under `alternatives` when it differs)
    workload unknown           -> Gen AI when a Gen AI stage/type is known, otherwise migration
    SAP, Oracle, other         -> the migration rules for the stated spend (no dedicated rules in the framework)
    no spend (migration / ISV) -> Promotional credits (nothing ARR-based can be worked out)
    startup 100k-264k, VMs?    -> VMs when migrating from on-premises or colocation, otherwise no VMs
    Gen AI stage / type?       -> proof of concept / fresh workload

Facts (None = unknown):
    segment           "startup" | "non_startup"
    workload          "migration" | "genai" | "isv_migration" | "sap_migration" | "oracle_migration" | "other"
    migration_source  "on_premises" | "other_cloud" | "colocation"
    vms               True | False
    genai_stage       "poc" | "production"
    genai_type        "fresh" | "modernization"
    mrr_usd, arr_usd  numbers
"""

from typing import Any

MAP_ARR = 500_000
IW_MIGRATE_ARR = 264_000
MAP_LITE_FLOOR_ARR = 100_000
FRAMEWORK = "AWS Funding Programs Decision Framework"

SEGMENTS = ("startup", "non_startup")
WORKLOADS = ("migration", "genai", "isv_migration", "sap_migration", "oracle_migration", "other")
SPECIAL = ("isv_migration", "sap_migration", "oracle_migration")
LABEL = {
    "startup": "Startup", "non_startup": "Non-startup", "migration": "Migration", "genai": "Gen AI project",
    "isv_migration": "ISV workload migration", "sap_migration": "SAP migration", "oracle_migration": "Oracle migration",
    "other": "Other workload", "on_premises": "on-premises", "other_cloud": "another cloud", "colocation": "colocation",
    "poc": "Proof of concept", "production": "Production-ready", "fresh": "Fresh workload",
    "modernization": "Modernization or optimization",
}
QUESTIONS = {
    "segment": "Is the end customer a startup, or an enterprise / other non-startup organisation?",
    "workload": "What is the workload: a migration to AWS, a Gen AI project, an ISV workload migration, or SAP / Oracle?",
    "spend": "What monthly AWS spend (MRR, USD) do you expect once the workload runs on AWS?",
    "vms": "Are virtual machines (VMs) part of the migration scope?",
    "genai_stage": "Is the Gen AI project a proof of concept, or is it production-ready?",
    "genai_type": "Is this a fresh Gen AI workload, or a modernization / optimization of an existing one?",
}


def _usd(n: float) -> str:
    return f"{round(n):,} USD"


def resolve_arr(mrr: float | None, arr: float | None) -> tuple[float | None, str, list[str]]:
    """-> (ARR, how it was worked out, notes). The framework converts MRR to ARR, so MRR wins when both are given."""
    notes = []
    if mrr is not None and mrr > 0:
        derived = mrr * 12
        if arr and abs(arr - derived) > 0.05 * max(arr, derived):
            notes.append(f"The stated ARR ({_usd(arr)}) differs from MRR x 12 ({_usd(derived)}); MRR x 12 is used, "
                         "as the framework requires. Confirm which figure is right.")
        return derived, f"MRR {_usd(mrr)} x 12 = ARR {_usd(derived)}", notes
    if arr is not None and arr > 0:
        return arr, f"ARR {_usd(arr)} (no MRR given; MRR = {_usd(arr / 12)})", notes
    return None, "", notes


def _outcome(rule: str, program: str, funding: str, components=None, notes=None) -> dict[str, Any]:
    return {"status": "decided", "rule": rule, "program": program, "funding": funding,
            "components": components or [], "notes": notes or []}


def _pct(name: str, percent: float, arr: float | None) -> dict[str, Any]:
    return {"name": name, "basis": f"{percent:g}% of ARR", "percent": percent,
            "amount_usd": round(arr * percent / 100) if arr is not None else None}


def _band(arr: float) -> str:
    if arr > MAP_ARR:
        return "above_500k"
    if arr > IW_MIGRATE_ARR:
        return "264k_500k"
    if arr > MAP_LITE_FLOOR_ARR:
        return "100k_264k"
    return "below_100k"


def _promo_credits(rule: str, note: str) -> dict[str, Any]:
    return _outcome(rule, "Promotional credits", "Promotional credits only", notes=[note])


def _migration(segment: str, arr: float, vms: bool | None, source: str | None, assume: list) -> dict[str, Any]:
    band = _band(arr)
    map_lite = [_pct("MAP Lite Assess", 5, arr), _pct("MAP Lite Mobilize", 20, arr)]
    if band == "above_500k":
        return _outcome(f"{segment}.migration.above_500k", "MAP", "Per MAP terms",
                        notes=["Above 500,000 USD ARR, migrations go to MAP in both segments."])
    if segment == "non_startup":
        if band == "below_100k":
            return _promo_credits("non_startup.migration.below_100k",
                                  "Non-startup migrations at or below 100,000 USD ARR are below the MAP Lite floor; "
                                  "promotional credits are the best-fit programme.")
        notes = ["MAP Lite needs a migration from on-premises or any cloud."]
        if source is None:
            notes.append("The migration source is not stated; confirm it is on-premises or another cloud.")
        return _outcome("non_startup.migration.100k_500k", "MAP Lite", "25% of ARR (5% Assess + 20% Mobilize)",
                        map_lite, notes)
    if band == "264k_500k":
        return _outcome("startup.migration.264k_500k", "MAP Lite", "25% of ARR (5% Assess + 20% Mobilize)",
                        map_lite, ["For startups, MAP Lite is the better option above 264,000 USD ARR."])
    if band == "100k_264k":
        if vms is None:
            vms = source in ("on_premises", "colocation")
            assume.append(("vms", "Yes" if vms else "No"))
        if vms:
            return _outcome("startup.migration.100k_264k.vms", "MAP Lite", "5% Assess + 20% Mobilize",
                            map_lite, ["For startups, MAP Lite is the better option wherever VMs are involved."])
        return _outcome("startup.migration.100k_264k.no_vms", "IW Migrate",
                        "Single phase, up to 264,000 USD ARR, no assessment",
                        notes=["IW Migrate (MRR under 22,000 USD) is a single phase with no assessment."])
    return _outcome("startup.migration.below_100k", "IW Migrate", "Single phase, up to 264,000 USD ARR, no assessment",
                    notes=["IW Migrate (MRR under 22,000 USD) is a single phase with no assessment."])


def _genai(segment: str, arr: float | None, stage: str | None, kind: str | None, assume: list) -> dict[str, Any]:
    if arr is None:
        assume.append(("spend", "Not stated"))
    notes = [] if arr is not None else ["Funding is a percentage of ARR; the amount follows once the expected MRR is known."]
    if segment == "non_startup":
        if stage is None:
            stage = "poc"
            assume.append(("genai_stage", LABEL["poc"]))
        if stage == "poc":
            return _outcome("non_startup.genai.poc", "POC", "10% of ARR", [_pct("Gen AI POC", 10, arr)], notes)
        return _outcome("non_startup.genai.production", "Production funding", "15% of ARR",
                        [_pct("Gen AI production", 15, arr)], notes)
    if kind is None:
        kind = "fresh"
        assume.append(("genai_type", LABEL["fresh"]))
    if kind == "fresh":
        return _outcome("startup.genai.fresh", "IW Assess, then IW Build", "5,000 USD, then 25% of ARR",
                        [{"name": "IW Assess", "basis": "fixed", "percent": None, "amount_usd": 5000},
                         _pct("IW Build", 25, arr)], ["IW Build applies to fresh workloads."] + notes)
    return _outcome("startup.genai.modernization", "POC only", "POC funding",
                    notes=["Startup Gen AI modernization receives POC funding only. The framework gives no amount; "
                           "confirm it with the AWS partner team."])


# Workloads without dedicated ARR rules are funded like the closest covered workload.
MAPPED_TO_MIGRATION = {
    "sap_migration": "SAP migrations have no dedicated funding rules yet; the migration rules for the stated spend apply.",
    "oracle_migration": "Oracle migrations have no dedicated funding rules yet; the migration rules for the stated spend apply.",
    "other": "This workload is not a named programme track; it is mapped to the closest one, the migration rules.",
}


def _single(segment: str, workload: str, arr: float | None, f: dict[str, Any], assume: list) -> dict[str, Any]:
    """Decision for one fully specified scenario."""
    if workload == "isv_migration":
        return _promo_credits("isv_migration", "ISV workload migrations have no segment split and no ARR-based funding.")
    if workload == "genai":
        return _genai(segment, arr, f.get("genai_stage"), f.get("genai_type"), assume)
    if arr is None:
        assume.append(("spend", "Not stated"))
        return _promo_credits(f"{segment}.{workload}.no_spend",
                              "No AWS spend is stated, so nothing ARR-based can be worked out; promotional credits "
                              "are the best-fit programme until the expected MRR is known.")
    o = _migration(segment, arr, f.get("vms"), f.get("migration_source"), assume)
    if workload in MAPPED_TO_MIGRATION:
        o["notes"] = [MAPPED_TO_MIGRATION[workload]] + o["notes"]
        o["rule"] = f"{workload}.{o['rule'].split('.', 1)[1]}"
    return o


def _key(o: dict[str, Any]) -> tuple:
    return (o["program"], o["funding"], tuple((c["name"], c["amount_usd"]) for c in o["components"]))


def _assumptions(assume: list) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for fact, value in assume:
        seen.setdefault(fact, value)
    return [{"fact": fact, "assumed": value, "question": QUESTIONS[fact]} for fact, value in seen.items()]


def decide(facts: dict[str, Any]) -> dict[str, Any]:
    """facts -> decision {status (always "decided"), program, funding, components, total_usd, arr_usd, path,
    assumptions, alternatives, ...}."""
    segment, workload = facts.get("segment"), facts.get("workload")
    arr, arr_how, notes = resolve_arr(facts.get("mrr_usd"), facts.get("arr_usd"))
    assume: list[tuple[str, str]] = []
    effective = workload
    if workload is None:
        effective = "genai" if (facts.get("genai_stage") or facts.get("genai_type")) else "migration"
        assume.append(("workload", LABEL[effective]))
    seg = segment or "non_startup"
    if segment is None and effective != "isv_migration":
        assume.append(("segment", LABEL[seg]))
    path = [
        {"step": "Segment", "value": LABEL[segment] if segment else ("Not applicable" if effective == "isv_migration"
                                                                   else f"{LABEL[seg]} (assumed)"), "known": segment is not None},
        {"step": "Workload", "value": LABEL[workload] if workload else f"{LABEL[effective]} (assumed)",
         "known": workload is not None},
        {"step": "AWS spend", "value": arr_how or "Not stated", "known": arr is not None},
    ]

    o = _single(seg, effective, arr, facts, assume)
    alternatives = []
    if segment is None and effective != "isv_migration":
        other = _single("startup", effective, arr, facts, [])
        if _key(other) != _key(o):
            alternatives.append({"when": LABEL["startup"], "status": "decided", "program": other["program"],
                                 "funding": other["funding"], "components": other["components"], "notes": other["notes"]})
            notes.append("The segment is not known; if the customer is a startup the funding differs (see alternatives).")
    return _result(o, path, arr, notes + o["notes"], _assumptions(assume), alternatives,
                   _rationale(o, workload or effective, seg if effective != "isv_migration" else None, arr))


def _rationale(o: dict[str, Any], workload: str, segment: str | None, arr: float | None) -> str:
    head = LABEL[workload]
    if segment and workload in ("migration", "genai"):
        head = f"{LABEL[segment]}, {head[0].lower() + head[1:] if workload == 'migration' else head}"
    if arr is not None and workload not in ("isv_migration",):
        head += f", {_usd(arr)} ARR"
    return f"{head}: {o['program']} ({o['funding']})."


def _result(o: dict[str, Any], path, arr, notes, assumptions, alternatives, rationale) -> dict[str, Any]:
    components = o["components"]
    known = [c["amount_usd"] for c in components if c["amount_usd"] is not None]
    total = sum(known) if components and len(known) == len(components) else None
    return {
        "framework": FRAMEWORK, "status": "decided", "rule": o["rule"], "program": o["program"], "funding": o["funding"],
        "components": components, "total_usd": total, "arr_usd": round(arr) if arr is not None else None,
        "mrr_usd": round(arr / 12) if arr is not None else None, "rationale": rationale, "path": path,
        "alternatives": alternatives, "notes": list(dict.fromkeys(notes)),
        "assumptions": assumptions,
        "missing": [],   # kept for consumers of the old shape (Live Assist); nothing blocks a decision any more
    }
