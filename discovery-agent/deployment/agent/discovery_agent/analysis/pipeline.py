import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from botocore.config import Config
from strands import Agent
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig
from strands.types.exceptions import MaxTokensReachedException

from .. import settings
from . import trim
from .assemble import assemble_report, repair_explicit, repair_hidden
from ..data.documents import Document
from .validate import numbers_in, quoted_numbers, validate_report

log = logging.getLogger("discovery_agent")

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
MAX_RETRIES = 2  # retries only for unusable output (invalid JSON, cut off); unsupported content is repaired in code

MODELS = {
    "extraction": settings.MODEL_EXTRACTION,
    "hidden_signals": settings.MODEL_HIDDEN_SIGNALS,
    "narrative": settings.MODEL_NARRATIVE,
    "trim": settings.MODEL_TRIM,
    "resolve": settings.MODEL_HIDDEN_SIGNALS,  # Opus: clarification resolution needs the strongest reasoning
}

# A full-corpus prompt (100k+ tokens) can take well over botocore's default 60 s before the first token streams back,
# which surfaced as ReadTimeoutError. Generous read timeout; connection retries stay on.
BEDROCK_CONFIG = Config(read_timeout=settings.BEDROCK_READ_TIMEOUT_SECONDS, connect_timeout=30,
                        retries={"max_attempts": 3, "mode": "standard"})


def model_id(stage: str) -> str:
    return MODELS[stage]


def make_agent(stage: str, prompt_file: str, instructions: str = "") -> Agent:
    system_prompt = (PROMPTS / prompt_file).read_text()
    if instructions and instructions.strip():
        # The user's per-run guidance is appended, clearly fenced, AFTER the fixed prompt. It can steer focus, audience
        # and emphasis, but the hard rules above it (evidence must be verbatim, JSON shape, no fabrication) still win.
        system_prompt += (
            "\n\n## Additional reviewer instructions for THIS run\n"
            "Follow these where they do not conflict with the rules above (evidence, grounding and output format "
            "always take precedence):\n" + instructions.strip()
        )
    model = BedrockModel(model_id=model_id(stage), boto_session=settings.aws_session(), boto_client_config=BEDROCK_CONFIG,
                         max_tokens=settings.MAX_OUTPUT_TOKENS,
                         cache_config=CacheConfig(strategy="auto"))  # retries reuse the cached corpus prefix
    return Agent(model=model, system_prompt=system_prompt, tools=[], callback_handler=None)


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


_PROBLEM_KINDS = (
    ("output was not valid JSON", "invalid_json"), ("output was cut off", "output_too_long"), ("schema:", "schema"),
    ("quote not found", "quote_not_verbatim"), ("speaker ", "unknown_speaker"), ("unknown source", "unknown_source"),
    ("number(s)", "ungrounded_number"), ("use 'customers'", "wording_clients"), ("system ", "unknown_system"),
)


def problem_kinds(problems: list[str]) -> dict[str, int]:
    """Count validation problems by kind. Logs and error messages carry only these counts, never the problem
    text, because that text can contain quotes from customer documents."""
    return dict(Counter(next((k for pre, k in _PROBLEM_KINDS if p.startswith(pre)), "other") for p in problems))


def run_json(agent: Agent, prompt: str, check=None, stage: str = "") -> dict:
    """Call the agent; on unusable output (invalid JSON, cut off) or failed checks, re-prompt up to MAX_RETRIES times.

    The same agent is reused so the retry sees its previous answer.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            data = _parse_json(str(agent(prompt)))
            problems = check(data) if check else []
            if not problems:
                return data
        except MaxTokensReachedException:
            problems = ["output was cut off because it was too long: return a more compact answer that follows "
                        "the size limits (fewer items, at most 2 evidence entries per item, shorter quotes)"]
        except ValueError as e:  # includes json.JSONDecodeError
            problems = [f"output was not valid JSON: {e}"]
        log.warning("stage=%s attempt=%d problems=%s", stage, attempt + 1, problem_kinds(problems))
        prompt = (
            "Your previous output was rejected. Fix ALL of these problems and return the full corrected JSON "
            "object only:\n- " + "\n- ".join(problems)
        )
    raise RuntimeError(f"stage {stage!r} failed after {MAX_RETRIES} retries: {problem_kinds(problems)}")


# ── corpus ───────────────────────────────────────────────────────────────────────────────────────────────────
# Rough token estimate (chars / 3.5, deliberately conservative). Raise it only for models with a larger window.
CORPUS_TOKEN_BUDGET = settings.CORPUS_TOKEN_BUDGET


class CorpusTooLarge(RuntimeError):
    pass


def _doc_block(d: Document) -> str:
    return f'<document id="{d.id}" kind="{d.kind}">\n{d.text}\n</document>'


def build_corpus(docs: list[Document]) -> str:
    corpus = "<documents>\n" + "\n\n".join(_doc_block(d) for d in docs) + "\n</documents>"
    tokens = int(len(corpus) / 3.5)
    if tokens > CORPUS_TOKEN_BUDGET:
        raise CorpusTooLarge(f"corpus is about {tokens:,} tokens, over the {CORPUS_TOKEN_BUDGET:,} budget")
    return corpus


def trim_documents(docs: list[Document]) -> list[Document]:
    """Remove small talk from large documents (a fast model picks turns to keep; code cuts them from the original)."""
    def ask(prompt: str, lo: int, hi: int) -> dict:
        return run_json(make_agent("trim", "trim.md"), prompt, lambda d: trim.range_problems(d, lo, hi), "trim")

    with ThreadPoolExecutor(max_workers=4) as pool:
        trimmed = list(pool.map(lambda d: trim.trim_document(d, ask), docs))  # keeps the document order
    log.info("trimmed documents kept_fraction=%s", {d.id: d.kept_fraction for d in trimmed if d.kept_fraction < 1})
    return trimmed


def prepare_corpus(docs: list[Document]) -> tuple[str, list[Document]]:
    """Build the corpus; if it is over budget, trim small talk from large documents and try once more."""
    try:
        return build_corpus(docs), docs
    except CorpusTooLarge as e:
        log.info("corpus over budget, trimming small talk: %s", e)
    trimmed = trim_documents(docs)
    try:
        return build_corpus(trimmed), trimmed
    except CorpusTooLarge as e:
        raise CorpusTooLarge(f"still over budget after removing small talk: {e}") from None


# ── analysis stages ──────────────────────────────────────────────────────────────────────────────────────────
def extract_explicit(corpus: str, docs: list[Document], instructions: str = "") -> dict:
    """Pass 1: explicit needs across all documents. Unsupported content is removed in code, not retried."""
    raw = run_json(make_agent("extraction", "extraction.md", instructions), corpus, stage="extraction")
    clean, repairs = repair_explicit(raw, docs)
    log.info("extraction kept=%d repairs=%s", len(clean["explicit_use_cases"]), repairs)
    return clean


def find_hidden(corpus: str, explicit: dict, docs: list[Document], instructions: str = "") -> dict:
    """Pass 2: hidden signals across all documents. Sees the cleaned pass 1 so it does not repeat explicit use cases."""
    raw = run_json(
        make_agent("hidden_signals", "hidden_signals.md", instructions),
        f"{corpus}\n\n<explicit_extraction>\n{json.dumps(explicit, indent=1)}\n</explicit_extraction>",
        stage="hidden_signals",
    )
    clean, repairs = repair_hidden(raw, docs)
    log.info("hidden_signals kept=%d repairs=%s", len(clean["hidden_use_cases"]), repairs)
    return clean


def build_meta(docs: list[Document], engagement: str, instructions: str = "") -> dict:
    used = ["extraction", "hidden_signals"] + (["trim"] if any(d.kept_fraction < 1 for d in docs) else [])
    sources = []
    for d in docs:
        src = {"id": d.id, "kind": d.kind, "label": d.label or d.id}
        if d.kept_fraction < 1:
            src["kept_fraction"] = d.kept_fraction
        sources.append(src)
    meta = {
        "engagement": engagement,
        "sources": sources,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {s: model_id(s) for s in used},
        "review_status": "pending human review",
    }
    if instructions and instructions.strip():
        meta["reviewer_instructions"] = instructions.strip()  # traceability: what steered this run
    return meta


def merge_report(explicit: dict, hidden: dict, docs: list[Document], engagement: str, instructions: str = "") -> dict:
    """Combine both cleaned passes into the final, validated report. Plain code: no model call."""
    # Cleaning is idempotent and costs nothing, so it is applied again here: a resumed run may hold checkpoints that
    # were cleaned by an older version of the rules.
    explicit, _ = repair_explicit(explicit, docs)
    hidden, _ = repair_hidden(hidden, docs)
    return assemble_report(explicit, hidden, docs, build_meta(docs, engagement, instructions))


def run(docs: list[Document], engagement: str, instructions: str = "") -> dict:
    """Analyze ALL documents of one engagement in a single pass and return the validated report.

    Both analysis passes see the whole corpus, so signals that only show up across documents (repeated
    complaints, conflicting statements, follow-ups that never happened) are visible to the model directly.
    Composes the stage functions above; the orchestrator agent's tools wrap the same functions.
    """
    corpus, docs = prepare_corpus(docs)
    log.info("start engagement=%s docs=%s est_tokens=%d instructions_len=%d", engagement,
             [(d.id, d.kind, len(d.text)) for d in docs], len(corpus) // 3.5, len(instructions or ""))
    explicit = extract_explicit(corpus, docs, instructions)
    hidden = find_hidden(corpus, explicit, docs, instructions)
    report = merge_report(explicit, hidden, docs, engagement, instructions)
    log.info("done engagement=%s use_cases=%d", engagement, len(report["use_cases"]))
    return report


NARRATIVE_CHUNK = 6  # use-case summaries written per model call; the calls run in parallel
_CLIENTS_WORD = re.compile(r"\bclient(s?)\b", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _clean_prose(text, allowed: set[str], stats: Counter) -> str | None:
    """Make narrative text safe in code instead of re-prompting: 'clients' becomes 'customers', and a sentence with a
    number the report's quotes do not contain is dropped. None when nothing usable is left."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = _CLIENTS_WORD.sub(lambda m: "customers" if m.group(1) else "customer", text.strip())
    sentences = _SENTENCES.split(text)
    kept = [s for s in sentences if not (numbers_in(s) - allowed)]
    stats["sentences_dropped_ungrounded_number"] += len(sentences) - len(kept)
    return " ".join(kept) or None


def _narrative_call(content: str, task: dict, instructions: str, stage_label: str) -> dict:
    """One model call that writes only the parts listed in `task`. Retries only for unusable (non-JSON) output."""
    prompt = f"{content}\n\n<task>\n{json.dumps(task)}\n</task>"
    return run_json(make_agent("narrative", "narrative.md", instructions), prompt,
                    lambda d: [] if isinstance(d, dict) else ["the answer must be one JSON object"], stage_label)


def write_narrative(report: dict, instructions: str = "") -> dict | None:
    """Haiku writes prose summaries from the validated report. Returns None if it cannot produce a safe result.

    The work is split into independent calls that run in parallel: one for the executive summary and the top
    opportunities, and one per NARRATIVE_CHUNK use cases. Text the report cannot support is repaired in code (a figure
    that is in no quote removes its sentence). A use case or opportunity the model skipped keeps the text the report
    already has, so no call is repeated because of content problems.
    """
    ucs, tops = report["use_cases"], report["top_hidden_opportunities"]
    allowed, stats = quoted_numbers(report), Counter()
    content = json.dumps({k: v for k, v in report.items() if k != "meta"}, indent=1)
    ids = [u["id"] for u in ucs]

    tasks = [{"executive_summary": True, "opportunity_ids": [t["use_case_id"] for t in tops], "use_case_ids": []}]
    tasks += [{"executive_summary": False, "opportunity_ids": [], "use_case_ids": ids[i:i + NARRATIVE_CHUNK]}
              for i in range(0, len(ids), NARRATIVE_CHUNK)]

    def run_task(task: dict) -> dict | None:
        try:
            return _narrative_call(content, task, instructions, "narrative")
        except RuntimeError as e:
            log.error("narrative part failed: %s", e)
            return None

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        parts = list(pool.map(run_task, tasks))

    head = parts[0] or {}
    raw_paragraphs = head.get("executive_summary")
    paragraphs = [p for p in (_clean_prose(x, allowed, stats) for x in (raw_paragraphs if isinstance(raw_paragraphs, list) else [])) if p]
    if not paragraphs:
        log.error("narrative skipped: no usable executive summary")
        return None

    summaries_in, opps_in = {}, (head.get("opportunity_narratives") if isinstance(head.get("opportunity_narratives"), dict) else {})
    for part in parts[1:]:
        if part and isinstance(part.get("use_case_summaries"), dict):
            summaries_in.update(part["use_case_summaries"])
    summaries = {u["id"]: _clean_prose(summaries_in.get(u["id"]), allowed, stats) or u["problem"] for u in ucs}
    opportunities = {t["use_case_id"]: _clean_prose(opps_in.get(t["use_case_id"]), allowed, stats) or t["why_it_matters"]
                     for t in tops}
    fell_back = sum(1 for u in ucs if summaries[u["id"]] == u["problem"]) \
        + sum(1 for t in tops if opportunities[t["use_case_id"]] == t["why_it_matters"])
    log.info("narrative calls=%d fallbacks=%d repairs=%s", len(tasks), fell_back, dict(stats))
    return {"executive_summary": paragraphs, "use_case_summaries": summaries, "opportunity_narratives": opportunities}


# ── clarification resolution (#2): phase-2 Opus pass that folds the reviewer's answers into the report ─────────
_SKIP = {"", "skip", "unknown", "i don't know", "idk", "n/a", "na", "-"}


def _is_skipped(answer: str) -> bool:
    return str(answer or "").strip().lower() in _SKIP


def clarification_outcomes(questions: list[dict], answers: dict) -> list[dict]:
    """Decide, in code, the status/resolved_by for each question from the reviewer's answers.

    - reviewer answered            -> resolved,           resolved_by=reviewer
    - skipped a gap                -> open                (stays an open question)
    - skipped a conflict           -> needs_confirmation, resolved_by=agent_assumed (the Opus pass fills the stand)
    Returns the questions list with these fields set (the resolution TEXT for reviewer answers is the answer itself;
    for assumed conflicts the Opus pass supplies the grounded stand).
    """
    out = []
    for q in questions:
        qid = q.get("id")
        answer = answers.get(qid, "")
        qtype = q.get("type", "gap")
        item = dict(q)
        if not _is_skipped(answer):
            item["status"] = "resolved"
            item["resolved_by"] = "reviewer"
            item["resolution"] = str(answer).strip()
        elif qtype == "conflict":
            item["status"] = "needs_confirmation"
            item["resolved_by"] = "agent_assumed"
            # A deterministic, always-valid stand so the field is never empty even if the Opus pass is rejected and we
            # fall back. The Opus path may replace this with richer reasoning (see _merge_outcomes).
            if not item.get("resolution"):
                item["resolution"] = ("Reviewer did not confirm; keeping the position best supported by the cited "
                                      "evidence until a reviewer confirms. See the reason and quotes above.")
        else:
            item["status"] = "open"
        out.append(item)
    return out


def clarifications_meta(questions: list[dict], answers: dict) -> dict:
    """The traceability block stored in report.meta.clarifications (reviewer answers live here, verbatim)."""
    items = []
    for q in questions:
        answer = answers.get(q.get("id"), "")
        skipped = _is_skipped(answer)
        item = {
            "id": q.get("id"),
            "question": q.get("question"),
            "type": q.get("type", "gap"),
            "answer": "skipped" if skipped else str(answer).strip(),
        }
        # resolved_by is only meaningful when there IS a resolver: the reviewer, or the agent (for a skipped conflict).
        # A skipped gap has no resolver, so the field is omitted (the schema allows only reviewer|agent_assumed).
        if not skipped:
            item["resolved_by"] = "reviewer"
        elif q.get("type") == "conflict":
            item["resolved_by"] = "agent_assumed"
        items.append(item)
    return {"items": items}


def resolve_clarifications(corpus: str, draft_report: dict, questions: list[dict], answers: dict,
                           docs: list[Document], instructions: str = "") -> dict:
    """Phase 2: replay the full context + the reviewer's answers through Opus, then validate and stitch.

    The model re-reasons the report body; code owns the clarification bookkeeping (status/resolved_by/meta) so it is
    deterministic and testable. Returns the final, schema-valid report.
    """
    outcomes = clarification_outcomes(questions, answers)
    prompt = (
        f"{corpus}\n\n"
        f"<draft_report>\n{json.dumps(draft_report, indent=1)}\n</draft_report>\n\n"
        f"<answers>\n{json.dumps({q.get('id'): answers.get(q.get('id'), 'skip') for q in questions}, indent=1)}\n</answers>"
    )
    revised = run_json(make_agent("resolve", "resolve_clarifications.md", instructions), prompt, stage="resolve")

    # The model may re-rank and rewrite the body; code re-asserts the clarification outcomes so they cannot drift.
    revised["gaps_and_open_questions"] = _merge_outcomes(revised.get("gaps_and_open_questions", []), outcomes)
    meta = dict(draft_report.get("meta", {}))
    meta["clarifications"] = clarifications_meta(questions, answers)
    revised["meta"] = meta
    revised["schema_version"] = draft_report.get("schema_version", "1.0")

    docmap = {d.id: d for d in docs}
    problems = validate_report(revised, docmap)
    if problems:
        log.warning("resolve: revised report had %d problems, falling back to a safe stitch: %s",
                    len(problems), problem_kinds(problems))
        # Safe fallback: keep the validated draft body, only attach the clarification outcomes + meta. Quality of the
        # body is unchanged from phase 1, but the reviewer's answers are still recorded and conflicts still get a stand.
        safe = dict(draft_report)
        safe["gaps_and_open_questions"] = outcomes
        safe["meta"] = meta
        problems2 = validate_report(safe, docmap)
        if problems2:
            raise RuntimeError(f"resolve produced an invalid report: {problem_kinds(problems2)}")
        return safe
    return revised


def _merge_outcomes(model_questions: list[dict], outcomes: list[dict]) -> list[dict]:
    """Trust code for status/resolved_by; take the model's grounded `resolution` for assumed conflicts if present."""
    by_id = {q.get("id"): q for q in model_questions if isinstance(q, dict)}
    merged = []
    for o in outcomes:
        item = dict(o)
        model_q = by_id.get(o.get("id"), {})
        if o.get("resolved_by") == "agent_assumed":
            # the model supplies the stand text; keep code's status/resolved_by
            stand = model_q.get("resolution") or model_q.get("question")
            if stand:
                item["resolution"] = stand
        merged.append(item)
    return merged
