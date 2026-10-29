"""Engagement context Live Assist hands to the Discovery agent with its documents.

The documents (``discovery_link.export_documents``) are what the agent reads. This is what the Discovery page shows
around the report: who the customer and seller are, the AWS program the Intake Agent selected, who took part in the
calls, and one entry per ended call. Everything comes from data Live Assist already holds; a value that is not known
is omitted, never guessed.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

SUMMARY_CHARS = 320
MAX_PARTICIPANTS = 60


def _short(text: str, limit: int = SUMMARY_CHARS) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def crm_url(row: dict[str, Any], crm_base_url: str) -> str | None:
    """The CRM link: the stored one, else a Dynamics record link built from the configured base URL."""
    if row.get("crm_url") and str(row["crm_url"]).startswith("https://"):
        return str(row["crm_url"])
    if crm_base_url.startswith("https://"):
        query = urllib.parse.urlencode({"etn": "opportunity", "pagetype": "entityrecord",
                                        "id": row["intake_opportunity_id"]})
        return f"{crm_base_url.rstrip('/')}/main.aspx?{query}"
    return None


def _size(row: dict[str, Any]) -> tuple[float | None, str | None]:
    if row.get("estimated_value_usd"):
        return row["estimated_value_usd"], "CRM estimated value"
    arr = (row.get("decision") or {}).get("arr_usd")
    if isinstance(arr, (int, float)) and arr > 0:
        return float(arr), "Expected AWS ARR (Intake)"
    return None, None


def header(row: dict[str, Any], crm_base_url: str = "") -> dict[str, Any]:
    decision = row.get("decision") or {}
    size, basis = _size(row)
    out = {
        "customer": row.get("customer") or None,
        "opportunity": row.get("name"),
        "seller": row.get("seller"),
        "opportunity_size_usd": size,
        "opportunity_size_basis": basis,
        "crm_url": crm_url(row, crm_base_url),
        "aws_program": decision.get("program"),
        "funding_status": decision.get("status"),
        "estimated_funding_usd": decision.get("total_usd"),
        "funding_rationale": decision.get("rationale"),
        "intake_checked_at": row.get("checked_at"),
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def participants(row: dict[str, Any], views: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Named people grouped by side: Ingram (the seller), partner (the Business Analyst in the call), customer."""
    people: dict[str, dict[str, str]] = {}
    seller = row.get("seller")
    if seller:
        people[seller.lower()] = {"name": seller, "side": "ingram", "role": "Seller"}
    for view in views:
        for turn in view.get("turns") or []:
            name = str(turn.get("speaker_name") or "").strip()
            if not name or name.lower() in people:
                continue
            people[name.lower()] = {"name": name, "side": "partner" if turn.get("speaker") == "ba" else "customer"}
    return list(people.values())[:MAX_PARTICIPANTS]


def call_entries(views: list[dict[str, Any]], document_names: dict[str, list[str]]) -> list[dict[str, Any]]:
    """One entry per ended call, oldest first: date, title, short summary and the documents that carry it."""
    calls = []
    for view in sorted(views, key=lambda v: v.get("created_at") or ""):
        minutes = (view.get("minutes") or {}).get("minutes") or {}
        summary = minutes.get("summary")
        if not summary:
            first = next((t.get("text") for t in view.get("turns") or [] if t.get("text")), "")
            summary = first
        entry = {"session_id": view["session_id"], "title": view.get("title") or "Call",
                 "date": (view.get("created_at") or "")[:10], "summary": _short(summary),
                 "turn_count": len(view.get("turns") or []), "documents": document_names.get(view["session_id"], [])}
        calls.append({k: v for k, v in entry.items() if v not in (None, "", [])})
    return calls


def build_context(row: dict[str, Any], views: list[dict[str, Any]], docs: list[dict[str, str]],
                  crm_base_url: str = "") -> dict[str, Any]:
    names: dict[str, list[str]] = {}
    for d in docs:
        if d.get("session_id"):
            names.setdefault(d["session_id"], []).append(d["name"])
    return {"header": header(row, crm_base_url), "participants": participants(row, views),
            "calls": call_entries(views, names)}

