"""One evaluator per rule type; each returns (status, detail) and never raises on missing inputs.

`not_applicable` (never `fail`) is returned when a rule's inputs are absent, so a rule that
cannot be evaluated never blocks export.
"""

import re
from typing import Any

from app.modules.sow_generator.schemas import UpstreamInputs
from app.modules.sow_generator.schemas.policies import PolicyStatus
from app.modules.sow_generator.services.draft_rules.config import COMPARE_OPS, Rule
from app.modules.sow_generator.services.draft_rules.markdown import ParsedDraft
from app.modules.sow_generator.services.draft_rules.numbers import (
    TIMELINE_TOLERANCE_WEEKS,
    find_labelled_number,
    normalise_durations,
    number_appears,
)
from app.modules.sow_generator.services.draft_rules.service_coverage import (
    SERVICE_COVERAGE_SECTIONS,
    service_covered,
)

# A markdown table row: a line with at least two cell separators, e.g. "| a | b |".
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|.*\|\s*$", re.MULTILINE)


def apply_rule(
    rule: Rule, draft: ParsedDraft, upstream: UpstreamInputs | None
) -> tuple[PolicyStatus, str]:
    rt = rule.type
    if rt == "section_present":
        return _rule_section_present(rule, draft)
    if rt == "numeric_compare":
        return _rule_numeric_compare(rule, draft)
    if rt == "upstream_numeric_compare":
        return _rule_upstream_numeric_compare(rule, upstream)
    if rt == "upstream_link_in_sow":
        return _rule_upstream_link_in_sow(rule, draft, upstream)
    if rt == "services_coverage":
        return _rule_services_coverage(draft, upstream)
    if rt == "timeline_match":
        return _rule_timeline_match(draft, upstream)
    return "not_applicable", f"Unsupported rule type {rt!r}."


def resolve_path(root: Any, dotted: str) -> Any:
    """Resolve a dotted attribute path (e.g. 'cost.monthly_total') on the
    Pydantic model. Returns None if any segment is missing."""
    current: Any = root
    for segment in dotted.split("."):
        if current is None:
            return None
        current = getattr(current, segment, None)
    return current


def _rule_section_present(rule: Rule, draft: ParsedDraft) -> tuple[PolicyStatus, str]:
    for name in rule.sections:
        needle = name.lower()
        for heading in draft.headings:
            if needle in heading.lower():
                return "pass", f"Found section '{heading}'."
    joined = ", ".join(f"'{s}'" for s in rule.sections)
    return "fail", f"No section matching any of: {joined}."


def _rule_numeric_compare(rule: Rule, draft: ParsedDraft) -> tuple[PolicyStatus, str]:
    label = rule.label or ""
    value, evidence = find_labelled_number(draft.text, label)
    if value is None:
        return "not_applicable", f"No '{label}' value found in the draft."
    if rule.value is None:
        return "not_applicable", "The rule has no threshold value."
    op_fn = COMPARE_OPS[rule.op or "lt"]
    unit = rule.unit or ""
    if op_fn(value, rule.value):
        return "pass", f"{label} is {value}{unit} ({rule.op} {rule.value}{unit}). Row: {evidence}"
    return (
        "fail",
        f"{label} is {value}{unit}, which violates '{rule.op} {rule.value}{unit}'. Row: {evidence}",
    )


def _rule_upstream_numeric_compare(
    rule: Rule, upstream: UpstreamInputs | None
) -> tuple[PolicyStatus, str]:
    """Compare an authoritative upstream number against a threshold.

    This is a LEGAL gate primitive: it reads the structured value
    from UpstreamInputs (the single source of truth) rather than scraping the
    rendered document, so a mis-rendered draft can never make the rule pass.
    """
    if upstream is None:
        return "not_applicable", "No upstream inputs supplied."
    value = resolve_path(upstream, rule.field or "")
    if value is None:
        return "not_applicable", f"Upstream '{rule.field}' is not set."
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "not_applicable", f"Upstream '{rule.field}' is not numeric: {value!r}."
    if rule.value is None:
        return "not_applicable", "The rule has no threshold value."
    op_fn = COMPARE_OPS[rule.op or "lt"]
    unit = rule.unit or ""
    if op_fn(value, rule.value):
        return "pass", f"Upstream {rule.field} is {value}{unit} ({rule.op} {rule.value}{unit})."
    return (
        "fail",
        f"Upstream {rule.field} is {value}{unit}, which violates '{rule.op} {rule.value}{unit}'.",
    )


def _rule_upstream_link_in_sow(
    rule: Rule, draft: ParsedDraft, upstream: UpstreamInputs | None
) -> tuple[PolicyStatus, str]:
    """The pricing section must point at the cost estimate, not reproduce it.

    The cost estimate agent's calculator is the single source of truth for AWS
    run-rate. A SOW that restates those numbers (as prose or a table) drifts from
    the calculator as soon as the estimate is revised, so restating them fails.
    """
    cost = upstream.cost if upstream else None
    if cost is None or not cost.source_link:
        return "not_applicable", "No upstream cost estimate link to refer to."

    pricing_body = draft.section_body_for(["Pricing", "Commercials", "Fees", "Investment"])
    scan = pricing_body if pricing_body is not None else draft.text
    where = "pricing section" if pricing_body is not None else "draft"

    if cost.source_link not in scan:
        return "fail", f"Cost estimate link is missing from the {where}: {cost.source_link}"

    restated = [
        f"{label} {cost.currency} {total:g}"
        for label, total in (("monthly", cost.monthly_total), ("annual", cost.annual_total))
        if total is not None and number_appears(scan, total)
    ]
    if restated:
        return (
            "fail",
            f"The {where} restates AWS run-rate figures from the cost estimate "
            f"({', '.join(restated)}). Refer to the cost estimate link instead.",
        )
    if pricing_body is not None and TABLE_ROW_RE.search(pricing_body):
        return (
            "fail",
            f"The {where} contains a table. AWS run-rate must not be tabulated in the "
            "SOW; refer to the cost estimate link instead.",
        )
    return "pass", f"The {where} refers to the cost estimate link without restating it."


def _rule_services_coverage(
    draft: ParsedDraft, upstream: UpstreamInputs | None
) -> tuple[PolicyStatus, str]:
    arch = upstream.architecture if upstream else None
    if not arch or not arch.services:
        return "not_applicable", "No upstream architecture services to cover."

    section_bodies = [draft.section_body_for([name]) for name in SERVICE_COVERAGE_SECTIONS]
    haystack = "\n".join(b for b in section_bodies if b).lower()
    full_text = draft.text.lower()

    # A service is covered if named in any relevant section, else anywhere in the draft.
    missing = [
        s.name
        for s in arch.services
        if not service_covered(s.name, haystack) and not service_covered(s.name, full_text)
    ]
    if missing:
        return (
            "fail",
            f"Architecture service(s) absent from scope/out-of-scope: {', '.join(missing)}.",
        )
    return "pass", "Every architecture service is covered in scope or out of scope."


def _rule_timeline_match(
    draft: ParsedDraft, upstream: UpstreamInputs | None
) -> tuple[PolicyStatus, str]:
    intake = upstream.intake if upstream else None
    timeline = (intake.timeline if intake else None) or ""
    if not timeline.strip():
        return "not_applicable", "No intake timeline to match."

    intake_weeks = normalise_durations(timeline)
    if not intake_weeks:
        return "not_applicable", f"Intake timeline '{timeline}' has no parseable duration."

    schedule_body = draft.section_body_for(
        ["Schedule", "Timeline", "Milestones", "Project Timeline", "Delivery Schedule"]
    )
    scan = schedule_body if schedule_body is not None else draft.text
    draft_weeks = normalise_durations(scan)

    if any(abs(i - d) <= TIMELINE_TOLERANCE_WEEKS for i in intake_weeks for d in draft_weeks):
        return "pass", f"Timeline duration matches intake ({sorted(intake_weeks)} weeks)."
    return (
        "fail",
        f"Intake timeline ~{sorted(intake_weeks)} weeks not reflected in the schedule "
        f"(found ~{sorted(draft_weeks)} weeks).",
    )
