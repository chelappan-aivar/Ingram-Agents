"""Draft check entry points: run every rule, derive issues, merge reviewer issues."""

from typing import Final

from app.modules.sow_generator.schemas import (
    ConsistencyIssue,
    DraftCheckResponse,
    PolicyCheckSummary,
    PolicyResult,
    UpstreamInputs,
)
from app.modules.sow_generator.schemas.review import IssueSeverity
from app.modules.sow_generator.services.draft_rules.config import Rule, load_rules
from app.modules.sow_generator.services.draft_rules.evaluators import apply_rule
from app.modules.sow_generator.services.draft_rules.markdown import parse_markdown

# Severity mapping for deterministic rule failures surfaced as consistency issues.
SEVERITY_FOR_RULE: Final[dict[str, IssueSeverity]] = {"required": "high", "advisory": "medium"}


# Cap on total consistency issues returned (matches the LLM verifier's 0-8 range).
ISSUE_CAP: Final = 8


# Rank for stable severity-ordered sorting of the merged issue list.
SEVERITY_RANK: Final = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def check_draft(sow_markdown: str, upstream: UpstreamInputs | None) -> DraftCheckResponse:
    """Evaluate a generated SOW draft against every compliance rule, in order.

    Returns reused ``PolicyResult`` rows plus deterministic ``ConsistencyIssue``
    entries derived from the failures. ``can_export`` stays True (humans approve
    drafts); the UI shows a blocking banner when a required rule fails.
    """
    rules = load_rules()
    draft = parse_markdown(sow_markdown)

    results: list[PolicyResult] = []
    issues: list[ConsistencyIssue] = []
    for r in rules:
        status, detail = apply_rule(r.rule, draft, upstream)
        results.append(
            PolicyResult(
                id=r.id,
                name=r.name,
                description=r.description,
                severity=r.severity,
                status=status,
                detail=detail,
            )
        )
        if status == "fail":
            issues.append(
                ConsistencyIssue(
                    id=r.id,
                    severity=SEVERITY_FOR_RULE[r.severity],
                    section=section_hint(r.rule),
                    title=r.name,
                    detail=detail,
                    suggestion=r.description,
                    source="deterministic",
                )
            )

    summary = PolicyCheckSummary(
        total=len(results),
        passed=sum(1 for r in results if r.status == "pass"),
        failed=sum(1 for r in results if r.status == "fail"),
        advisory=sum(1 for r in results if r.severity == "advisory" and r.status != "pass"),
    )
    # Default product choice: never hard-block export; the banner is advisory.
    return DraftCheckResponse(rules=results, issues=issues, summary=summary, can_export=True)


def merge_issues(
    deterministic: list[ConsistencyIssue], model: list[ConsistencyIssue]
) -> list[ConsistencyIssue]:
    """Merge deterministic and model issues, de-duplicating by (section, title).

    Deterministic issues win on conflict, the result is sorted by severity, and
    the total is capped at ISSUE_CAP.
    """
    merged: dict[tuple[str, str], ConsistencyIssue] = {}
    for issue in [*deterministic, *model]:
        key = (issue.section.strip().lower(), issue.title.strip().lower())
        if key not in merged:
            merged[key] = issue
    ordered = sorted(merged.values(), key=lambda i: SEVERITY_RANK.get(i.severity, 99))
    return ordered[:ISSUE_CAP]


def section_hint(rule: Rule) -> str:
    if rule.type in ("section_present", "numeric_compare") and rule.sections:
        return rule.sections[0]
    if rule.type == "numeric_compare" and rule.label:
        return rule.label
    if rule.type == "upstream_link_in_sow":
        return "Pricing"
    if rule.type == "services_coverage":
        return "Scope"
    if rule.type == "timeline_match":
        return "Schedule"
    return ""
