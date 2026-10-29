"""Deterministic SOW rules (resources/policies/sow_compliance_rules.yaml) on a sample draft."""

import asyncio

from conftest import SAMPLE_SOW

from sow_generator.models import VerificationOutcome
from sow_generator.schemas import ConsistencyIssue, UpstreamInputs, ValidateDraftRequest
from sow_generator.services.draft_check import run_draft_check
from sow_generator.services.draft_rules import check_draft, load_rules

UPSTREAM = UpstreamInputs.model_validate({
    "meta": {"opportunity_id": "OPP-1", "template_id": "aiml/ai-ml"},
    "intake": {"customer_name": "Acme Corp", "timeline": "8 weeks"},
    "architecture": {"services": [{"name": "Amazon Bedrock (Claude)"}, {"name": "Amazon S3"},
                                  {"name": "Amazon Kinesis"}]},
    "cost": {"currency": "USD", "monthly_total": 1000, "annual_total": 12000,
             "source_link": "https://calculator.aws/#/estimate?id=sample123"},
})


def _status(result, rule_id):
    return next(r.status for r in result.rules if r.id == rule_id)


def test_rules_load():
    assert {"scope-section-present", "pricing-section-present"} <= {r.id for r in load_rules()}


def test_sample_passes_section_rules_without_upstream():
    result = check_draft(SAMPLE_SOW, None)
    assert _status(result, "scope-section-present") == "pass"
    assert _status(result, "pricing-section-present") == "pass"
    assert _status(result, "pricing-refers-to-cost-estimate") == "not_applicable"
    assert result.can_export is True
    assert result.summary.total == len(result.rules)


def test_cross_checks_with_upstream():
    result = check_draft(SAMPLE_SOW, UPSTREAM)
    assert _status(result, "pricing-refers-to-cost-estimate") == "pass"
    assert _status(result, "timeline-consistent") == "pass"
    assert _status(result, "services-covered") == "fail"            # Amazon Kinesis is not in the draft
    assert any(i.id == "services-covered" and i.source == "deterministic" for i in result.issues)


def test_missing_sections_fail_and_become_issues():
    result = check_draft("# About Ingram Micro\nJust an intro.\n", None)
    assert _status(result, "scope-section-present") == "fail"
    assert {"scope-section-present", "pricing-section-present"} <= {i.id for i in result.issues}
    assert result.summary.failed >= 2


def test_draft_check_merges_reviewer_issues():
    async def verifier(markdown, upstream):
        return VerificationOutcome(status="ok", model_id="fake", issues=[ConsistencyIssue(
            id="vague_scope", severity="medium", section="Scope", title="Vague scope", source="model")])

    request = ValidateDraftRequest(sow_markdown=SAMPLE_SOW, verify=True)
    result = asyncio.run(run_draft_check(request, verifier))
    assert result.ai_review and result.ai_review.status == "ok" and result.ai_review.issues_added == 1
    assert any(i.source == "model" for i in result.issues)


def _sow_with_pricing(body: str) -> str:
    """SAMPLE_SOW with its Pricing section body swapped out."""
    head, _, tail = SAMPLE_SOW.partition("# Pricing\n")
    _, _, rest = tail.partition("# Schedule I")
    return f"{head}# Pricing\n{body}\n# Schedule I{rest}"


def test_pricing_without_cost_estimate_link_fails():
    draft = _sow_with_pricing("One-time delivery (fixed price): USD 50,000.")
    result = check_draft(draft, UPSTREAM)
    assert _status(result, "pricing-refers-to-cost-estimate") == "fail"


def test_pricing_restating_run_rate_fails():
    draft = _sow_with_pricing(
        "One-time delivery (fixed price): USD 50,000.\n"
        "Monthly AWS run-rate: USD 1,000.\n"
        "AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample123"
    )
    result = check_draft(draft, UPSTREAM)
    assert _status(result, "pricing-refers-to-cost-estimate") == "fail"


def test_pricing_cost_table_fails():
    draft = _sow_with_pricing(
        "AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample123\n"
        "| Item | Amount |\n| --- | --- |\n| One-time delivery | USD 50,000 |"
    )
    result = check_draft(draft, UPSTREAM)
    assert _status(result, "pricing-refers-to-cost-estimate") == "fail"
