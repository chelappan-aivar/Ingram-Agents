"""The Well-Architected review of a SOW, run before the architecture diagram is generated."""

import asyncio

from sow_generator.schemas import UpstreamInputs, WaFinding, WaReviewRequest
from sow_generator.services.wa_review import (
    REQUIRED_TOPICS,
    WaVerificationOutcome,
    merge_findings,
    review_sow,
    run_wa_review,
)

# A SOW that commits to everything the required-topic checks look for, so only the
# decision checks can fire. Keeps each test's intent isolated.
SOUND_SOW = """# Scope of Work
## In Scope
- An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serving the claims API.
- Amazon RDS for PostgreSQL 17, Multi-AZ, across two Availability Zones.
- Encryption at rest with AWS KMS and TLS 1.2 in transit.
- Least-privilege IAM roles; sign-in federated through Microsoft Entra ID (SAML).
- Amazon CloudWatch logs, metrics, dashboards and alarms routed to the on-call rota.
- Automated backups with 7-day retention and point-in-time recovery. RPO 15 minutes, RTO 2 hours.
- Availability target of 99.9% monthly.
- Expected load is 40 concurrent users with a p95 latency target of 800 ms.
- Right-sizing review at go-live, cost allocation tagging and a monthly budget alarm.
- Graviton (arm64) throughout and S3 Intelligent-Tiering lifecycle policies.
- A runbook and operational handover to the customer's platform team, with training.
## Out of Scope
- Data migration from the legacy platform.
"""


def _ids(result) -> set[str]:
    return {f.id for f in result.findings}


def test_a_sound_sow_raises_nothing_and_can_proceed():
    result = review_sow(SOUND_SOW)
    assert result.findings == []
    assert result.can_proceed is True
    assert result.summary.total == 0


def test_every_required_topic_fires_on_an_empty_sow():
    """An empty draft must fail every absence check, not throw."""
    result = review_sow("# Scope of Work\nNothing has been decided yet.\n")
    assert {t.id for t in REQUIRED_TOPICS} <= _ids(result)
    assert result.can_proceed is False


def test_review_never_raises_on_odd_input():
    for md in ("", "   ", "no headings at all", "#", "####### seven hashes"):
        assert review_sow(md) is not None


def test_summary_counts_and_pillar_breakdown():
    result = review_sow("# Scope of Work\nNothing.\n")
    assert result.summary.total == len(result.findings)
    assert result.summary.high + result.summary.medium + result.summary.low == result.summary.total
    # all six pillars are always reported, even at zero, so the UI has a stable shape
    assert [p.pillar for p in result.summary.pillars] == [
        "operational_excellence", "security", "reliability",
        "performance_efficiency", "cost_optimization", "sustainability",
    ]
    assert sum(p.findings for p in result.summary.pillars) == result.summary.total
    assert all(p.label for p in result.summary.pillars)


def test_findings_are_ordered_high_severity_first():
    result = review_sow("# Scope of Work\nNothing.\n")
    ranks = [{"high": 0, "medium": 1, "low": 2}[f.severity] for f in result.findings]
    assert ranks == sorted(ranks)


# ----------------------------------------------------------------- single AZ (Sparsh)
def test_single_az_scope_is_a_high_reliability_finding():
    md = SOUND_SOW.replace("Multi-AZ, across two Availability Zones", "deployed in a single Availability Zone")
    result = review_sow(md)
    assert "wa_reliability_single_az" in _ids(result)
    finding = next(f for f in result.findings if f.id == "wa_reliability_single_az")
    assert finding.severity == "high" and finding.pillar == "reliability"
    assert "REL10-BP02" in finding.detail
    assert "single Availability Zone" in finding.evidence
    assert result.can_proceed is False


def test_single_az_is_detected_from_the_architecture_block_too():
    """The SOW may be silent while the upstream architecture assumption says single-AZ."""
    upstream = UpstreamInputs.model_validate({
        "meta": {"opportunity_id": "OPP-1", "template_id": "aiml/ai-ml"},
        "intake": {"customer_name": "Acme"},
        "architecture": {"assumptions": ["Single-AZ deployment is acceptable for the pilot."]},
    })
    md = SOUND_SOW.replace("Multi-AZ, across two Availability Zones", "in the primary Availability Zone")
    assert "wa_reliability_single_az" in _ids(review_sow(md, upstream))
    # ...and stays quiet when the architecture block says nothing of the kind
    assert "wa_reliability_single_az" not in _ids(review_sow(SOUND_SOW, upstream=None))


def test_multi_az_scope_raises_no_az_finding():
    assert "wa_reliability_single_az" not in _ids(review_sow(SOUND_SOW))


# ----------------------------------------------------------------- overclaimed resiliency (Sparsh)
def test_well_architected_claim_with_single_az_is_flagged():
    md = SOUND_SOW.replace(
        "Amazon RDS for PostgreSQL 17, Multi-AZ, across two Availability Zones.",
        "A well-architected, highly available platform in a single Availability Zone.",
    )
    result = review_sow(md)
    assert "wa_reliability_overclaimed_resiliency" in _ids(result)


def test_the_same_claim_is_fine_when_the_scope_supports_it():
    md = SOUND_SOW.replace(
        "Amazon RDS for PostgreSQL 17, Multi-AZ, across two Availability Zones.",
        "A well-architected, highly available platform across two Availability Zones with Multi-AZ RDS.",
    )
    assert "wa_reliability_overclaimed_resiliency" not in _ids(review_sow(md))


# ----------------------------------------------------------------- compute decision (Ashwin)
def test_competing_compute_options_in_one_sentence_are_flagged():
    md = SOUND_SOW.replace(
        "An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serving the claims API.",
        "Container platform using Amazon ECS, Amazon EKS or AWS Fargate.",
    )
    result = review_sow(md)
    assert "wa_operations_compute_decision_open" in _ids(result)
    finding = next(f for f in result.findings if f.id == "wa_operations_compute_decision_open")
    assert finding.severity == "high"
    assert "Amazon ECS" in finding.evidence and "AWS Fargate" in finding.evidence


def test_tbd_compute_choice_is_flagged():
    md = SOUND_SOW.replace(
        "An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serving the claims API.",
        "Compute platform: Amazon EKS or Amazon EC2, TBD during design.",
    )
    assert "wa_operations_compute_decision_open" in _ids(review_sow(md))


def test_ecs_on_fargate_is_one_decision_not_two_options():
    """'An ECS service on Fargate' is how Fargate is normally described; it is not ambiguity."""
    assert "wa_operations_compute_decision_open" not in _ids(review_sow(SOUND_SOW))


def test_a_legitimate_mix_of_lambda_and_fargate_is_not_flagged():
    md = SOUND_SOW.replace(
        "An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serving the claims API.",
        "AWS Lambda handles ingestion events.\n- An Amazon ECS service on AWS Fargate serves the API.",
    )
    assert "wa_operations_compute_decision_open" not in _ids(review_sow(md))


def test_a_stated_decision_with_rejected_alternatives_is_not_flagged():
    md = SOUND_SOW.replace(
        "An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serving the claims API.",
        "We will use AWS Fargate for all services.\n- Amazon EKS is out of scope.",
    )
    assert "wa_operations_compute_decision_open" not in _ids(review_sow(md))


# ----------------------------------------------------------------- scope split (Sparsh)
def test_missing_out_of_scope_list_is_flagged_because_architecture_consumes_it():
    md = SOUND_SOW.split("## Out of Scope")[0]
    result = review_sow(md)
    finding = next(f for f in result.findings if f.id == "wa_operations_missing_out_of_scope")
    assert finding.severity == "high"
    assert "architecture stage" in finding.detail


def test_exclusions_wording_also_satisfies_the_scope_split():
    md = SOUND_SOW.replace("## Out of Scope", "## Exclusions")
    assert "wa_operations_missing_out_of_scope" not in _ids(review_sow(md))


# ----------------------------------------------------------------- evidence quality
def test_every_finding_names_its_evidence_and_a_fix():
    for md in (SOUND_SOW, "# Scope of Work\nNothing.\n",
               SOUND_SOW.replace("two Availability Zones", "a single Availability Zone")):
        for finding in review_sow(md).findings:
            assert finding.evidence, finding.id
            assert finding.suggestion, finding.id
            assert finding.detail, finding.id
            assert len(finding.title) <= 80, finding.id


def test_the_managed_agent_note_explains_why_this_review_is_local():
    note = review_sow(SOUND_SOW).managed_agent_note
    assert "CDK or Terraform" in note and "cannot review this SOW" in note


# ----------------------------------------------------------------- LLM pass merge
def _finding(**kw) -> WaFinding:
    base = {"id": "x", "pillar": "security", "severity": "medium", "title": "T",
            "detail": "d", "suggestion": "s", "evidence": "e", "source": "model"}
    return WaFinding.model_validate({**base, **kw})


def test_model_findings_merge_and_do_not_override_a_rule():
    rules = review_sow("# Scope of Work\nNothing.\n").findings
    rule_titles = {(f.pillar, f.title.lower()) for f in rules}
    duplicate = _finding(id="dup", pillar=rules[0].pillar, title=rules[0].title)
    fresh = _finding(id="fresh", pillar="security", title="Secrets stored in the repository")

    merged = merge_findings(rules, [duplicate, fresh])
    assert len(merged) == len(rules) + 1
    # the deterministic one survives the collision, because it carries the evidence
    kept = next(f for f in merged if (f.pillar, f.title.lower()) in rule_titles and f.pillar == rules[0].pillar
                and f.title == rules[0].title)
    assert kept.source == "deterministic"
    assert any(f.id == "fresh" and f.source == "model" for f in merged)


def test_verify_false_skips_the_model_entirely():
    called = False

    async def verifier(md, up):
        nonlocal called
        called = True
        return WaVerificationOutcome(status="ok")

    result = asyncio.run(run_wa_review(WaReviewRequest(sow_markdown=SOUND_SOW), verifier))
    assert called is False and result.ai_review is None


def test_verify_true_merges_and_recomputes_the_gate():
    async def verifier(md, up):
        return WaVerificationOutcome(
            status="ok", model_id="m-1",
            findings=[_finding(id="m1", severity="high", title="Fargate is the wrong fit here")],
        )

    result = asyncio.run(
        run_wa_review(WaReviewRequest(sow_markdown=SOUND_SOW, verify=True), verifier))
    assert result.ai_review.requested is True and result.ai_review.status == "ok"
    assert result.ai_review.findings_added == 1 and result.ai_review.model_id == "m-1"
    # a high model finding closes the gate even though the rules all passed
    assert result.summary.high == 1 and result.can_proceed is False


def test_a_failed_model_pass_leaves_the_rule_results_intact():
    async def verifier(md, up):
        return WaVerificationOutcome(status="failed", model_id="m-1", detail="boom")

    rules_only = review_sow(SOUND_SOW.split("## Out of Scope")[0])
    result = asyncio.run(run_wa_review(
        WaReviewRequest(sow_markdown=SOUND_SOW.split("## Out of Scope")[0], verify=True), verifier))
    assert result.ai_review.status == "failed" and result.ai_review.detail == "boom"
    assert _ids(result) == _ids(rules_only)


def test_verify_true_without_a_configured_reviewer_is_reported_not_crashed():
    result = asyncio.run(run_wa_review(WaReviewRequest(sow_markdown=SOUND_SOW, verify=True), None))
    assert result.ai_review.status == "skipped" and "No reviewer" in result.ai_review.detail


# ----------------------------------------------------------------- CVA (assessment) profile
def test_assessment_template_skips_build_oriented_checks():
    """A Cloud Viability Assessment deploys nothing, so 'no backup commitment' would be a false blocker."""
    md = "# Scope of Work\n## In Scope\n- Server inventory\n## Out of Scope\n- Migration\n"
    build = review_sow(md)
    assessment = review_sow(md, template_id="cva/full")
    assert {t.id for t in REQUIRED_TOPICS} & _ids(build)
    assert not ({t.id for t in REQUIRED_TOPICS} & _ids(assessment))
    assert assessment.can_proceed is True


def test_assessment_profile_still_catches_an_unsupported_resiliency_claim():
    md = ("# Scope of Work\n## In Scope\n- A well-architected, highly available target in a single "
          "Availability Zone\n## Out of Scope\n- Migration\n")
    result = review_sow(md, template_id="cva/full")
    assert "wa_reliability_overclaimed_resiliency" in _ids(result) and result.can_proceed is False


def test_unknown_or_missing_template_id_uses_the_build_profile():
    for tid in (None, "", "aiml/ai-ml", "devops/devops-standard"):
        assert review_sow("# Scope of Work\nNothing.\n", template_id=tid).findings


def test_cva_template_is_listed_and_loads():
    from sow_generator.repositories.template_repository import load_template
    from sow_generator.services.template_service import list_template_options

    ids = {t.id for t in list_template_options()}
    assert "cva/full" in ids
    label = next(t.label for t in list_template_options() if t.id == "cva/full")
    assert label.startswith("CVA")
    loaded = load_template("cva/full")
    assert "Cloud Viability Assessment" in str(loaded)


def test_cva_template_has_the_sections_the_pipeline_and_rules_depend_on():
    from pathlib import Path

    import sow_generator

    md = (Path(sow_generator.__file__).parent / "resources/templates/cva/full.md").read_text(encoding="utf-8")
    for needle in ("# Service/Engagement Scope", "### Exclusion(s)", "# Pricing", "[cost calculator link]",
                   "![Architecture Diagram](arch:1)", "# Acceptance of Statement of Work"):
        assert needle in md, needle
    assert "| Signed on behalf of Ingram Micro EMEA Unified Services Centre" in md
