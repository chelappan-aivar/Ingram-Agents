"""Deterministic clean-up and assembly of the final report. No model calls.

The two analysis passes return findings with evidence. Instead of asking a model to retry or to re-write the whole
report, code:
  1. removes anything the documents do not support (repair),
  2. combines both passes, ranks, numbers and labels the findings (assemble).
Everything kept is copied verbatim from the passes, so quotes can never be altered here.
"""
import copy
import re
from collections import Counter

from ..data.documents import Document
from .transcript import normalize
from .validate import numbers_in, quote_is_verbatim, validate_report

# Deterministic backstop for low-value open questions the model sometimes emits despite the prompt: identity /
# attribution / "who said what" questions are undecidable from the documents and not useful to a solutions architect.
_IDENTITY_Q = re.compile(
    r"same person|same individual|different people|different person|who said|who stated|who quoted|"
    r"whose (?:spreadsheet|document|file|sheet)|which (?:person|attendee|speaker)|refer to the same",
    re.I,
)

MAX_USE_CASES = 24
MAX_HEARD_PER_SECTION = 8
MAX_OPEN_QUESTIONS = 12
SECTIONS = ("goals", "pain_points", "current_state", "constraints", "stakeholders")
LIST_FIELDS = ("current_process", "data_sources_and_apis", "dependencies_and_blockers", "stated_metrics")
USE_CASE_TEXT = ("title", "problem", "customer_value", "company_value", "validation_question")
USE_CASE_FIELDS = {
    "title", "problem", "evidence", "why_hidden", "customer_value", "company_value", "confidence",
    "validation_question", "systems_involved", "current_process", "data_sources_and_apis", "owners",
    "dependencies_and_blockers", "stated_metrics", "status", "priority",
}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
STATUSES = {"not_started", "planned_by_customer", "in_progress_by_customer", "unknown"}


class _Context:
    def __init__(self, docs: list[Document]):
        self.docs = {d.id: d for d in docs}
        self.bodies = {i: normalize(d.text) for i, d in self.docs.items()}
        self.all_text = " ".join(self.bodies.values())
        self.stats: Counter = Counter()  # repair counts by kind; safe to log (no content)

    def note(self, kind: str, n: int = 1) -> None:
        if n:
            self.stats[kind] += n


def _clean_evidence(evidence, ctx: _Context) -> list[dict]:
    out = []
    for e in evidence or []:
        if not isinstance(e, dict) or e.get("source") not in ctx.docs or not isinstance(e.get("quote"), str):
            ctx.note("evidence_dropped_malformed", 1)
            continue
        if not quote_is_verbatim(e["quote"], ctx.bodies[e["source"]]):
            ctx.note("evidence_dropped_not_verbatim", 1)
            continue
        entry = {"source": e["source"], "quote": e["quote"]}
        speaker, known = e.get("speaker"), ctx.docs[e["source"]].speakers
        if isinstance(speaker, str) and speaker and (not known or speaker in known):
            entry["speaker"] = speaker
        elif speaker:
            ctx.note("speaker_removed", 1)
        out.append(entry)
    return out


def _name_grounded(name: str, ctx: _Context) -> bool:
    """Every part of a person's name must occur in the documents ('Halder, Tirthankar' matches 'Tirthankar Halder')."""
    parts = [p for p in normalize(name).split() if len(p) > 1]
    return bool(parts) and all(p in ctx.all_text for p in parts)


def _ground_text(text, allowed: set[str], ctx: _Context) -> str | None:
    """Drop sentences that contain a number the cited quotes do not contain; None when nothing is left."""
    if not isinstance(text, str) or not text.strip():
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if not (numbers_in(s) - allowed)]
    if len(kept) < len(sentences):
        ctx.note("sentences_dropped_ungrounded_number", len(sentences) - len(kept))
    return " ".join(kept) or None


def _repair_use_case(item, ctx: _Context) -> dict | None:
    if not isinstance(item, dict):
        ctx.note("use_case_dropped_malformed", 1)
        return None
    uc = {k: copy.deepcopy(v) for k, v in item.items() if k in USE_CASE_FIELDS}
    uc["evidence"] = _clean_evidence(uc.get("evidence"), ctx)
    if not uc["evidence"]:
        ctx.note("use_case_dropped_no_evidence", 1)
        return None
    allowed = numbers_in(" ".join(e["quote"] for e in uc["evidence"]))

    for field in USE_CASE_TEXT + ("why_hidden",):
        if field in uc:
            uc[field] = _ground_text(uc[field], allowed, ctx)
    if any(uc.get(f) is None for f in ("title", "problem", "customer_value", "validation_question")):
        ctx.note("use_case_dropped_text_unsupported", 1)
        return None

    for field in ("systems_involved", *LIST_FIELDS):
        values = [v for v in uc.get(field, []) if isinstance(v, str) and v.strip()]
        if field == "systems_involved":
            kept = [v for v in values if normalize(v) and normalize(v) in ctx.all_text]
            ctx.note("systems_dropped_not_in_documents", len(values) - len(kept))
        else:
            kept = [v for v in values if not (numbers_in(v) - allowed)]
            ctx.note("entries_dropped_ungrounded_number", len(values) - len(kept))
        uc[field] = kept

    owners = []
    for o in uc.get("owners", []) or []:
        if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not _name_grounded(o["name"], ctx):
            ctx.note("owners_dropped_name_not_in_documents", 1)
            continue
        owner = {"name": o["name"]}
        role = o.get("role")
        if isinstance(role, str) and role.strip():
            if normalize(role) in ctx.all_text:
                owner["role"] = role
            else:
                ctx.note("owner_roles_dropped_not_in_documents")  # an unverifiable role is not asserted
        owners.append(owner)
    uc["owners"] = owners

    if uc.get("status") not in STATUSES:
        uc.pop("status", None)
    uc["confidence"] = uc.get("confidence") if uc.get("confidence") in CONFIDENCE_ORDER else "low"
    uc["priority"] = uc.get("priority") if uc.get("priority") in (1, 2, 3) else 2
    return uc


def _repair_heard(item, section: str, tag: str, ctx: _Context) -> dict | None:
    if not isinstance(item, dict):
        return None
    evidence = _clean_evidence(item.get("evidence"), ctx)
    if not evidence:
        ctx.note("heard_item_dropped_no_evidence", 1)
        return None
    text = _ground_text(item.get("text"), numbers_in(" ".join(e["quote"] for e in evidence)), ctx)
    if text is None:
        ctx.note("heard_item_dropped_text_unsupported", 1)
        return None
    out = {"text": text, "tag": tag, "evidence": evidence}
    if section == "stakeholders":
        name = item.get("name")
        if not isinstance(name, str) or not _name_grounded(name, ctx):
            ctx.note("heard_item_dropped_name_not_in_documents", 1)
            return None
        out["name"] = name
        if isinstance(item.get("role"), str) and normalize(item["role"]) in ctx.all_text:
            out["role"] = item["role"]
    return out


def _repair_questions(questions, ctx: _Context) -> list[dict]:
    out = []
    for q in questions or []:
        if not isinstance(q, dict) or not isinstance(q.get("question"), str) or not isinstance(q.get("reason"), str):
            ctx.note("question_dropped_malformed", 1)
            continue
        if _IDENTITY_Q.search(q["question"]):  # undecidable identity/attribution noise; never surface
            ctx.note("question_dropped_identity_noise", 1)
            continue
        evidence = _clean_evidence(q.get("evidence"), ctx)
        allowed = numbers_in(" ".join(e["quote"] for e in evidence))
        question = _ground_text(q["question"], allowed, ctx)
        if question is None:
            ctx.note("question_dropped_text_unsupported", 1)
            continue
        item = {"question": question, "reason": _ground_text(q["reason"], allowed, ctx) or "Not stated in the sources."}
        if q.get("type") in ("gap", "conflict"):
            item["type"] = q["type"]
        if evidence:
            item["evidence"] = evidence
        out.append(item)
    return out


def repair_explicit(data: dict, docs: list[Document]) -> tuple[dict, dict]:
    """Clean pass 1 output. Returns (clean data, repair counts by kind)."""
    ctx = _Context(docs)
    heard = data.get("what_we_heard") or {}
    clean = {
        "what_we_heard": {s: [x for x in (_repair_heard(i, s, "stated", ctx) for i in heard.get(s, [])) if x]
                          for s in SECTIONS},
        "explicit_use_cases": [x for x in (_repair_use_case(i, ctx) for i in data.get("explicit_use_cases", [])) if x],
        "open_questions": _repair_questions(data.get("open_questions"), ctx),
    }
    return clean, dict(ctx.stats)


def repair_hidden(data: dict, docs: list[Document]) -> tuple[dict, dict]:
    """Clean pass 2 output. Hidden findings must also carry a category and a 'why hidden' text."""
    ctx = _Context(docs)
    cases = []
    for item in data.get("hidden_use_cases", []):
        if not isinstance(item, dict) or item.get("category") not in ("IMPLIED", "LATENT", "ADJACENT"):
            ctx.note("use_case_dropped_malformed", 1)
            continue
        uc = _repair_use_case(item, ctx)
        if uc is None:
            continue
        if not uc.get("why_hidden") or not uc.get("company_value"):
            ctx.note("use_case_dropped_text_unsupported", 1)
            continue
        uc["category"] = item["category"]
        cases.append(uc)
    inferred = data.get("inferred_heard_items") or {}
    clean = {
        "hidden_use_cases": cases,
        "inferred_heard_items": {s: [x for x in (_repair_heard(i, s, "inferred", ctx) for i in inferred.get(s, [])) if x]
                                 for s in SECTIONS},
        "open_questions": _repair_questions(data.get("open_questions"), ctx),
    }
    return clean, dict(ctx.stats)


def _rank_key(entry: tuple[int, dict]):
    order, uc = entry
    sources = len({e["source"] for e in uc["evidence"]})
    return (uc["priority"], CONFIDENCE_ORDER[uc["confidence"]], -sources, order)


def assemble_report(explicit: dict, hidden: dict, docs: list[Document], meta: dict) -> dict:
    """Combine the two cleaned passes into the final report and validate it."""
    candidates, seen = [], set()
    for item in explicit.get("explicit_use_cases", []):
        candidates.append({**item, "category": "EXPLICIT"})
    candidates += list(hidden.get("hidden_use_cases", []))

    unique = []
    for uc in candidates:
        key = normalize(uc["title"])
        if key in seen:
            continue
        seen.add(key)
        if uc["category"] == "EXPLICIT":
            uc.pop("why_hidden", None)
            if not uc.get("company_value"):
                uc["company_value"] = "Supports the engagement scope."  # neutral default; the model did not supply one
        unique.append(uc)

    ranked = [uc for _, uc in sorted(enumerate(unique), key=_rank_key)][:MAX_USE_CASES]
    use_cases = []
    for rank, uc in enumerate(ranked, 1):
        item = {**uc, "id": f"UC-{rank}", "rank": rank}
        if item["category"] != "EXPLICIT":
            item["hypothesis_label"] = "hypothesis, not confirmed"
        use_cases.append(item)

    top = [{"use_case_id": u["id"], "title": u["title"], "why_it_matters": u["customer_value"]}
           for u in use_cases if u["category"] != "EXPLICIT"][:5]

    heard, n = {}, 0
    for section in SECTIONS:
        items = list(explicit.get("what_we_heard", {}).get(section, []))
        items += list(hidden.get("inferred_heard_items", {}).get(section, []))
        heard[section] = []
        for it in items[:MAX_HEARD_PER_SECTION]:
            n += 1
            heard[section].append({"id": f"H-{n}", **it})

    questions, seen_q = [], set()
    for q in list(explicit.get("open_questions", [])) + list(hidden.get("open_questions", [])):
        key = normalize(q["question"])
        if key not in seen_q and len(questions) < MAX_OPEN_QUESTIONS:
            seen_q.add(key)
            questions.append({"id": f"Q-{len(questions) + 1}", **q})

    report = {"schema_version": "1.0", "meta": meta, "what_we_heard": heard, "use_cases": use_cases,
              "top_hidden_opportunities": top, "gaps_and_open_questions": questions}
    problems = validate_report(report, {d.id: d for d in docs})
    if problems:  # should be impossible after repair; fail loudly with kinds only, never content
        kinds = Counter(p.split(":")[0].split(" ")[0] for p in problems)
        raise RuntimeError(f"assembled report failed validation: {dict(kinds)}")
    return report
