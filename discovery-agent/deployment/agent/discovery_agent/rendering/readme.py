from .context import context_lines

HYPOTHESIS = "hypothesis, not confirmed"

HEARD_SECTIONS = [("Goals", "goals"), ("Pain points", "pain_points"), ("Current state", "current_state"),
                  ("Constraints", "constraints"), ("Stakeholders", "stakeholders")]

MAX_QUOTES = 2  # quotes shown per item; the JSON keeps all of them
MAX_QUOTE_CHARS = 240


def source_labels(report: dict) -> dict[str, str]:
    return {s["id"]: s.get("label") or s["id"] for s in report["meta"]["sources"]}


def _trim(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def pick_evidence(evidence: list[dict], n: int = MAX_QUOTES) -> tuple[list[dict], int]:
    """Choose a few readable quotes, preferring one per source document, trimmed to a readable length.

    Returns (quotes to show, number of quotes left out).
    """
    by_source: dict[str, list[dict]] = {}
    for e in evidence:
        by_source.setdefault(e["source"], []).append(e)
    # one quote per source, closest to a readable mid length
    picked = [min(es, key=lambda e: abs(len(e["quote"]) - 140)) for es in by_source.values()][:n]
    for e in evidence:
        if len(picked) >= n:
            break
        if e not in picked:
            picked.append(e)
    shown = [dict(e, quote=_trim(e["quote"], MAX_QUOTE_CHARS)) for e in picked]
    return shown, len(evidence) - len(shown)


def ev_line(e: dict, labels: dict[str, str]) -> str:
    who = f"{e['speaker']}, " if e.get("speaker") else ""
    label = labels.get(e["source"], e["source"])
    short = label.split(" \u00b7 ")[0]  # 'Sep 22 \u00b7 Title' -> 'Sep 22'; full names are listed once in the header table
    return f'"{e["quote"]}" ({who}{short})'


STATUS_LABEL = {"not_started": "Not started", "planned_by_customer": "Planned by the customers",
                "in_progress_by_customer": "In progress at the customers"}


def specifics_lines(u: dict) -> list[str]:
    """Scoping details of a use case in a fixed order; fields the sources do not state are omitted."""
    out: list[str] = []
    if u.get("systems_involved"):
        out.append(f"- **Systems involved:** {', '.join(u['systems_involved'])}")
    if u.get("current_process"):
        out.append("- **Current process:**")
        out += [f"  - Step {i}: {step}" for i, step in enumerate(u["current_process"], 1)]
    if u.get("data_sources_and_apis"):
        out += ["- **Data sources and APIs:**"] + [f"  - {x}" for x in u["data_sources_and_apis"]]
    if u.get("owners"):
        owners = [o["name"] + (f" ({o['role']})" if o.get("role") else "") for o in u["owners"]]
        out.append(f"- **Owners:** {'; '.join(owners)}")
    if u.get("dependencies_and_blockers"):
        out += ["- **Dependencies and blockers:**"] + [f"  - {x}" for x in u["dependencies_and_blockers"]]
    if u.get("stated_metrics"):
        out += ["- **Customer-stated metrics:**"] + [f"  - {x}" for x in u["stated_metrics"]]
    if u.get("status") in STATUS_LABEL:
        out.append(f"- **Status:** {STATUS_LABEL[u['status']]}")
    return out


def heard_text(it: dict) -> str:
    who = f" ({it['name']}{', ' + it['role'] if it.get('role') else ''})" if it.get("name") else ""
    return f"{it['text']}{who}"


def trimmed_row(meta: dict) -> list[str]:
    """One header-table row when small talk was removed from some sources before analysis."""
    parts = [f"{s.get('label') or s['id']} (kept {round(s['kept_fraction'] * 100)}%)".replace("|", "/")
             for s in meta["sources"] if s.get("kept_fraction", 1) < 1]
    return [f"| Trimmed before analysis | Small talk removed from: {'; '.join(parts)} |"] if parts else []


def render_markdown(report: dict, narrative: dict | None = None, context: dict | None = None) -> str:
    m = report["meta"]
    labels = source_labels(report)

    def evidence(items: list[dict], indent: str = "  - ") -> list[str]:
        shown, more = pick_evidence(items)
        lines = [f"{indent}{ev_line(e, labels)}" for e in shown]
        if more:
            lines.append(f"{indent}_(+{more} more in the JSON report)_")
        return lines

    out = [
        f"# Discovery Report: {m['engagement'].replace('_', ' ')}",
        "",
        "| Field | Detail |",
        "|---|---|",
        f"| Engagement | {m['engagement'].replace('_', ' ')} |",
        f"| Sources analysed ({len(labels)}) | {'; '.join(labels.values()).replace('|', '/')} |",
        f"| Generated | {m['generated_at'][:10]} |",
        *trimmed_row(m),
        *( [f"| Reviewer instruction | {m['reviewer_instructions'].replace(chr(10), ' ').replace('|', '/')} |"]
           if m.get("reviewer_instructions") else [] ),
        f"| Status | {m['review_status']} |",
        "",
        f"> Items marked **{HYPOTHESIS}** are inferences from the sources, not statements by the customers. "
        "Every item cites a verbatim quote and its source.",
        "",
    ]
    out += context_lines(context)
    if narrative:
        out += ["## Executive summary", ""] + [p + "\n" for p in narrative["executive_summary"]]

    out += ["## 1. Discovery summary", "", "| # | Use case | Category | Confidence |", "|---|---|---|---|"]
    ucs = sorted(report["use_cases"], key=lambda x: x["rank"])
    out += [f"| {u['rank']} | {u['title'].replace('|', '/')} | {u['category'].capitalize()} | {u['confidence']} |" for u in ucs] + [""]

    out += ["## 2. Top hidden opportunities", ""]
    cats = {u["id"]: u["category"] for u in ucs}
    if not report["top_hidden_opportunities"]:
        out += ["_No hidden opportunities with enough evidence._", ""]
    for i, t in enumerate(report["top_hidden_opportunities"], 1):
        why = narrative["opportunity_narratives"][t["use_case_id"]] if narrative else t["why_it_matters"]
        out += [f"{i}. **{t['title']}** (`{t['use_case_id']}`, {cats[t['use_case_id']].capitalize()}) — _{HYPOTHESIS}_",
                f"   {why}"]
    out.append("")

    out += ["## 3. Use cases", ""]
    for u in ucs:
        hidden = u["category"] != "EXPLICIT"
        out += [f"### {u['rank']}. {u['title']} (`{u['id']}`)", ""]
        if narrative:
            out += [narrative["use_case_summaries"][u["id"]], ""]
        out += [f"- **Category:** {u['category'].capitalize()}" + (f" — _{HYPOTHESIS}_" if hidden else ""),
                f"- **Confidence:** {u['confidence']}",
                f"- **Problem:** {u['problem']}"] + specifics_lines(u) + [
                "- **Evidence:**"] + evidence(u["evidence"], "  - ")
        if hidden:
            out.append(f"- **Why it is hidden:** {u['why_hidden']}")
        out += [f"- **Value for the customer:** {u['customer_value']}",
                f"- **Value for us:** {u['company_value']}",
                f"- **Ask on the next call:** {u['validation_question']}", ""]

    out += ["## 4. What we heard", ""]
    for title, key in HEARD_SECTIONS:
        out += [f"### {title}", ""]
        items = report["what_we_heard"][key]
        if not items:
            out += ["_Nothing found in the sources._", ""]
            continue
        for it in items:
            label = "" if it["tag"] == "stated" else f" _{HYPOTHESIS}_"
            out.append(f"- **[{it['tag']}]** {heard_text(it)}{label}")
            out += evidence(it["evidence"])
        out.append("")

    questions = report["gaps_and_open_questions"]
    resolved = [q for q in questions if q.get("status") in ("resolved", "needs_confirmation")]
    still_open = [q for q in questions if q.get("status") not in ("resolved", "needs_confirmation")]

    if resolved:
        out += ["## 5. Resolved during review", "",
                "_Questions the reviewer addressed. Items marked **needs confirmation** are the agent's "
                "best evidence-based stand on a conflict the reviewer skipped._", ""]
        for q in resolved:
            if q.get("resolved_by") == "agent_assumed":
                tag = "agent's stand — needs confirmation"
            else:
                tag = "answered by reviewer"
            out += [f"- **{q['question']}** _({tag})_",
                    f"  - Resolution: {q.get('resolution', '').strip() or '(not recorded)'}",
                    f"  - Why it was raised: {q['reason']}"]
            out += evidence(q.get("evidence", []))
        out += [""]

    heading_n = "6" if resolved else "5"
    out += [f"## {heading_n}. Open questions", ""]
    if not still_open:
        out += ["_None — all questions were addressed during review._", ""]
    for q in still_open:
        out += [f"- **{q['question']}**", f"  - Why: {q['reason']}"]
        out += evidence(q.get("evidence", []))
    return "\n".join(out) + "\n"
