"""What earlier agents decided, as report lines: the Intake funding decision and the Live Assist calls.

The context is written next to the documents by the Discovery API's Live Assist import (``_context.json``). Fields that
were not supplied are left out; nothing is inferred.
"""

STATUS_LABEL = {"decided": "decided", "needs_info": "needs more information", "to_confirm": "to be confirmed",
                "not_covered": "no program covers it"}


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "/")


def context_lines(context: dict | None) -> list[str]:
    """A 'Context from earlier agents' section, or nothing when no context was supplied."""
    if not context:
        return []
    head, calls = context.get("header") or {}, context.get("calls") or []
    if not head and not calls:
        return []
    rows = []
    if head.get("customer"):
        rows.append(("Customer", head["customer"]))
    if head.get("seller"):
        rows.append(("Seller (Ingram)", head["seller"]))
    if head.get("opportunity_size_usd"):
        basis = f" ({head['opportunity_size_basis']})" if head.get("opportunity_size_basis") else ""
        rows.append(("Opportunity size", f"{round(head['opportunity_size_usd']):,} USD{basis}"))
    if head.get("crm_url"):
        rows.append(("CRM record", head["crm_url"]))
    if head.get("aws_program"):
        status = STATUS_LABEL.get(head.get("funding_status"), head.get("funding_status"))
        rows.append(("AWS program selected (Intake Agent)", head["aws_program"] + (f", {status}" if status else "")))
    if head.get("estimated_funding_usd"):
        rows.append(("Estimated AWS funding", f"{round(head['estimated_funding_usd']):,} USD"))
    if head.get("funding_rationale"):
        rows.append(("Why this program", head["funding_rationale"]))
    out = ["## Context from earlier agents", ""]
    if rows:
        out += ["| Field | Detail | Source |", "|---|---|---|"]
        out += [f"| {_cell(k)} | {_cell(v)} | Intake Agent record (00_intake_record.md) |" for k, v in rows]
        out.append("")
    if calls:
        out += ["**Live Assist calls**", ""]
        for c in calls:
            docs = f" _(sources: {', '.join(c['documents'])})_" if c.get("documents") else ""
            out.append(f"- {c.get('date', '')} {c['title']}: {c.get('summary', '')}{docs}".replace("  ", " "))
        out.append("")
    return out
