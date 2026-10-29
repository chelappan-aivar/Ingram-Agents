"""Run the Well-Architected checks over a SOW draft and summarise them.

Deterministic first, then an optional LLM pass whose findings are merged and de-duplicated the
same way the draft check merges its advisory issues. A model finding never replaces a
deterministic one: the rule carries the evidence, so the rule wins on a collision.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from sow_generator.schemas.upstream import UpstreamInputs
from sow_generator.schemas.wa import (
    PILLAR_LABELS,
    WaAiReviewMeta,
    WaFinding,
    WaPillarSummary,
    WaReviewRequest,
    WaReviewResponse,
    WaReviewSummary,
)
from sow_generator.services.draft_rules.markdown import parse_markdown
from sow_generator.services.wa_review.checks import (
    DECISION_CHECKS,
    REQUIRED_TOPICS,
    evaluate_required_topic,
)

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
PILLAR_ORDER = list(PILLAR_LABELS)
MAX_FINDINGS = 60


# Template categories that describe an assessment, not a build. A Cloud Viability Assessment installs a
# collector and writes a report; it never deploys or runs a workload, so "no backup commitment" or "no
# observability commitment" would be false blockers. Only the checks that stay meaningful for a document
# that merely *describes* a target architecture are kept.
ASSESSMENT_CATEGORIES = frozenset({"cva"})
SKIPPED_FOR_ASSESSMENT = frozenset({"wa_operations_compute_decision_open"})


def is_assessment(template_id: str | None) -> bool:
    return (template_id or "").split("/", 1)[0].strip().lower() in ASSESSMENT_CATEGORIES


def review_sow(sow_markdown: str, upstream: UpstreamInputs | None = None,
               template_id: str | None = None) -> WaReviewResponse:
    """The deterministic review. Never raises on odd markdown; an empty draft simply fails more checks.

    `template_id` selects the profile: an assessment template skips the build-oriented checks.
    """
    draft = parse_markdown(sow_markdown or "")
    text = sow_markdown or ""
    assessment = is_assessment(template_id)

    findings: list[WaFinding] = []
    for topic in ([] if assessment else REQUIRED_TOPICS):
        finding = evaluate_required_topic(topic, draft, text)
        if finding is not None:
            findings.append(finding)
    for check in DECISION_CHECKS:
        if assessment and check.id in SKIPPED_FOR_ASSESSMENT:
            continue
        finding = check.evaluate(draft, text, upstream)
        if finding is not None:
            findings.append(finding)

    return _respond(_order(findings))


def merge_findings(rules: list[WaFinding], model: list[WaFinding]) -> list[WaFinding]:
    """Deterministic findings plus the model's, keyed by (pillar, title) so near-duplicates collapse.

    The rule-sourced finding wins a collision because it names the text it fired on.
    """
    merged: dict[tuple[str, str], WaFinding] = {}
    for finding in rules:
        merged[(finding.pillar, finding.title.strip().lower())] = finding
    for finding in model:
        key = (finding.pillar, finding.title.strip().lower())
        if key not in merged:
            merged[key] = finding
    return _order(list(merged.values()))


def _order(findings: list[WaFinding]) -> list[WaFinding]:
    """Highest severity first, then pillar order, so the blocking items read first."""
    return sorted(
        findings,
        key=lambda f: (SEVERITY_RANK.get(f.severity, 9),
                       PILLAR_ORDER.index(f.pillar) if f.pillar in PILLAR_ORDER else 99,
                       f.id),
    )[:MAX_FINDINGS]


def _respond(findings: list[WaFinding]) -> WaReviewResponse:
    by_pillar = [
        WaPillarSummary(
            pillar=pillar,                                  # type: ignore[arg-type]
            label=PILLAR_LABELS[pillar],
            findings=sum(1 for f in findings if f.pillar == pillar),
            high=sum(1 for f in findings if f.pillar == pillar and f.severity == "high"),
        )
        for pillar in PILLAR_ORDER
    ]
    summary = WaReviewSummary(
        total=len(findings),
        high=sum(1 for f in findings if f.severity == "high"),
        medium=sum(1 for f in findings if f.severity == "medium"),
        low=sum(1 for f in findings if f.severity == "low"),
        pillars=by_pillar,
    )
    return WaReviewResponse(
        findings=findings,
        summary=summary,
        # Advisory, like the draft check: a human decides. A high finding is the signal to fix the
        # SOW before the diagram is drawn, which is the whole point of running this stage first.
        can_proceed=summary.high == 0,
    )


type WaVerifier = Callable[[str, UpstreamInputs | None], Awaitable["WaVerificationOutcome"]]


class WaVerificationOutcome:
    """What the optional LLM pass returned. Mirrors models.VerificationOutcome's shape."""

    def __init__(self, status: str = "skipped", findings: list[WaFinding] | None = None,
                 model_id: str = "", detail: str = "") -> None:
        self.status = status
        self.findings = findings or []
        self.model_id = model_id
        self.detail = detail


async def run_wa_review(request: WaReviewRequest, verify: WaVerifier | None = None) -> WaReviewResponse:
    """Deterministic review, then the advisory LLM pass when asked for and available."""
    result = review_sow(request.sow_markdown, request.upstream, request.template_id)
    if not request.verify:
        return result
    if verify is None:
        result.ai_review = WaAiReviewMeta(requested=True, status="skipped",
                                          detail="No reviewer is configured in this deployment.")
        return result
    outcome = await verify(request.sow_markdown, request.upstream)
    result.findings = merge_findings(result.findings, outcome.findings)
    result.ai_review = WaAiReviewMeta(
        requested=True,
        status=outcome.status,                              # type: ignore[arg-type]
        findings_added=sum(1 for f in result.findings if f.source == "model"),
        model_id=outcome.model_id,
        detail=outcome.detail,
    )
    # Recompute the gate and the counts over the merged set.
    merged = _respond(result.findings)
    result.summary, result.can_proceed = merged.summary, merged.can_proceed
    return result
