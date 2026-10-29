"""The engagement overview shown above and below the report: header, what has been done, stakeholders, call timeline.

Inputs are the context Live Assist sent with the documents (``_context.json``: header, participants, calls) and, once
the run has finished, ``discovery.json``. Nothing here calls a model or AWS: every line is derived from those two
documents, so each statement can be traced to its source.
"""

from __future__ import annotations

import re
from typing import Any

SIDES = ("ingram", "partner", "customer")
MAX_ITEMS = 100
MAX_TEXT = 400
HEADER_FIELDS = ("customer", "opportunity", "seller", "opportunity_size_usd", "opportunity_size_basis", "crm_url",
                 "aws_program", "funding_status", "estimated_funding_usd", "funding_rationale", "intake_checked_at")


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_context(raw: Any) -> dict[str, Any]:
    """Keep only the known fields of a context document received from Live Assist (untrusted input)."""
    if not isinstance(raw, dict):
        return {}
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    out_header: dict[str, Any] = {}
    for key in HEADER_FIELDS:
        value = header.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        if key.endswith("_usd"):
            if isinstance(value, (int, float)) and value > 0:
                out_header[key] = float(value)
        elif key == "crm_url":
            if isinstance(value, str) and value.startswith("https://") and len(value) <= 500:
                out_header[key] = value
        elif isinstance(value, str):
            out_header[key] = _text(value)
    people = []
    for p in raw.get("participants") or []:
        if isinstance(p, dict) and p.get("side") in SIDES and _text(p.get("name")):
            people.append({k: _text(p[k], 120) for k in ("name", "side", "role") if p.get(k)})
    calls = []
    for c in raw.get("calls") or []:
        if isinstance(c, dict) and _text(c.get("title")):
            calls.append({"title": _text(c["title"], 160), "date": _text(c.get("date"), 10),
                          "summary": _text(c.get("summary")),
                          "documents": [_text(d, 200) for d in c.get("documents") or [] if isinstance(d, str)][:10]})
    return {"header": out_header, "participants": people[:MAX_ITEMS], "calls": calls[:MAX_ITEMS]}


def _key(name: str) -> str:
    """'Halder, Tirthankar' and 'Tirthankar Halder' give the same key."""
    return " ".join(sorted(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()))


def stakeholders(context: dict[str, Any], report: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """People per side. Roles and sources come from the report's stakeholder findings when the name matches.

    Someone the documents mention but who is not in the call roster cannot be placed on a side from the data, so they
    are listed as 'unplaced' instead of being guessed.
    """
    by_key: dict[str, dict[str, Any]] = {}
    out: dict[str, list[dict[str, Any]]] = {side: [] for side in (*SIDES, "unplaced")}
    for p in context.get("participants") or []:
        entry = {"name": p["name"], "role": p.get("role"), "sources": []}
        by_key[_key(p["name"])] = entry
        out[p["side"]].append(entry)
    for item in ((report or {}).get("what_we_heard") or {}).get("stakeholders") or []:
        name = item.get("name")
        if not name:
            continue
        entry = by_key.get(_key(name))
        if entry is None:
            entry = {"name": name, "role": None, "sources": []}
            by_key[_key(name)] = entry
            out["unplaced"].append(entry)
        entry["role"] = entry["role"] or item.get("role")
        entry["sources"] += [e["source"] for e in item.get("evidence") or [] if e.get("source")]
    for entries in out.values():
        for entry in entries:
            entry["sources"] = sorted(set(entry["sources"]))
            if not entry["role"]:
                entry.pop("role")
    return out


def timeline(context: dict[str, Any], report: dict[str, Any] | None) -> list[dict[str, str]]:
    """One entry per call with its date and a short summary. Without Live Assist context, one per analysed source."""
    if context.get("calls"):
        return [{k: c[k] for k in ("date", "title", "summary") if c.get(k)} for c in context["calls"]]
    sources = ((report or {}).get("meta") or {}).get("sources") or []
    return [{"title": s.get("label") or s["id"]} for s in sources if s.get("kind") in ("transcript", "email", "chat")]


def done(context: dict[str, Any], report: dict[str, Any] | None, state: str | None) -> list[str]:
    """What has been done so far, in the order it happened."""
    head, calls = context.get("header") or {}, context.get("calls") or []
    lines = []
    if head.get("aws_program") or head.get("funding_status"):
        program = head.get("aws_program") or "no program"
        when = f" on {head['intake_checked_at'][:10]}" if head.get("intake_checked_at") else ""
        funding = f"; estimated funding {round(head['estimated_funding_usd']):,} USD" if head.get("estimated_funding_usd") else ""
        lines.append(f"Intake checked AWS funding{when}: {program} selected ({head.get('funding_status') or 'status unknown'}){funding}.")
    if calls:
        dates = sorted(c["date"] for c in calls if c.get("date"))
        span = f", {dates[0]} to {dates[-1]}" if len(dates) > 1 else (f", on {dates[0]}" if dates else "")
        lines.append(f"{len(calls)} Live Assist call{'s' if len(calls) != 1 else ''} held{span}.")
    if report:
        meta = report.get("meta") or {}
        found = report.get("use_cases") or []
        hidden = [u for u in found if u.get("category") != "EXPLICIT"]
        open_q = [q for q in report.get("gaps_and_open_questions") or [] if q.get("status") not in ("resolved", "needs_confirmation")]
        lines.append(f"Discovery analysed {len(meta.get('sources') or [])} documents and found {len(found)} use cases "
                     f"({len(hidden)} hidden) and {len(open_q)} open questions.")
    elif state in ("starting", "running", "awaiting_clarification"):
        lines.append("Discovery is analysing the documents.")
    return lines


def build(context: dict[str, Any], report: dict[str, Any] | None, state: str | None) -> dict[str, Any]:
    return {"header": context.get("header") or {}, "summary": done(context, report, state),
            "stakeholders": stakeholders(context, report), "timeline": timeline(context, report),
            "has_context": bool(context.get("header") or context.get("calls"))}


MAX_SUMMARY_ITEMS = 8


def summarise_report(report: dict[str, Any], narrative: dict[str, Any] | None) -> dict[str, Any]:
    """The finished report in the size the Fetch panel shows: counts, top findings and the executive summary."""
    use_cases = sorted(report.get("use_cases") or [], key=lambda u: u.get("rank") or 99)
    questions = [q for q in report.get("gaps_and_open_questions") or [] if q.get("status") not in ("resolved", "needs_confirmation")]
    pains = [p["text"] for p in (report.get("what_we_heard") or {}).get("pain_points") or [] if p.get("text")]
    return {
        "generated_at": (report.get("meta") or {}).get("generated_at"),
        "executive_summary": [str(p) for p in (narrative or {}).get("executive_summary") or [] if p][:4],
        "use_case_count": len(use_cases),
        "use_cases": [{"title": u.get("title"), "category": u.get("category"), "confidence": u.get("confidence")}
                      for u in use_cases[:MAX_SUMMARY_ITEMS]],
        "hidden_opportunities": [{"title": h.get("title"), "why_it_matters": h.get("why_it_matters")}
                                 for h in (report.get("top_hidden_opportunities") or [])[:MAX_SUMMARY_ITEMS]],
        "pain_points": pains[:MAX_SUMMARY_ITEMS],
        "open_questions": [q["question"] for q in questions[:MAX_SUMMARY_ITEMS] if q.get("question")],
        "open_question_count": len(questions),
    }
