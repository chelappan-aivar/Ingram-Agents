"""Assembles the generation system prompt from the template, company profile and inputs."""

import re

from sow_generator.core.config import CompanyProfile
from sow_generator.repositories.prompt_repository import load_common_system_prompt
from sow_generator.repositories.template_repository import category_label
from sow_generator.schemas import UpstreamInputs


def fill_system_prompt(client_name: str, category: str, company: CompanyProfile) -> str:
    common = load_common_system_prompt()
    # Prefer the human-readable category label (e.g. "AI/ML", "DevOps").
    category_text = category_label(category)
    return (
        common.replace("[CATEGORY]", category_text)
        .replace("[CLIENT_NAME]", client_name)
        .replace("[COMPANY_NAME]", company.name)
        .replace("[COMPANY_SHORT_NAME]", company.short_name)
        .replace("[COMPANY_ABOUT]", company.about)
    )


def inject_tag(prompt: str, tag: str, body: str) -> str:
    pattern = re.compile(rf"(<{tag}>)(.*?)(</{tag}>)", flags=re.DOTALL | re.IGNORECASE)
    # A function replacement keeps backslashes in the body (such as Windows paths) literal.
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", prompt, count=1)


def render_upstream_inputs(upstream: UpstreamInputs) -> str:
    """Render UpstreamInputs into fixed-order markdown sections for the prompt."""
    parts: list[str] = []

    intake = upstream.intake
    intake_lines = [f"- Customer: {intake.customer_name}"]
    if intake.industry:
        intake_lines.append(f"- Industry: {intake.industry}")
    if intake.engagement_type:
        intake_lines.append(f"- Engagement type: {intake.engagement_type}")
    if intake.problem_statement:
        intake_lines.append(f"- Problem statement: {intake.problem_statement}")
    for goal in intake.goals:
        intake_lines.append(f"- Goal: {goal}")
    if intake.timeline:
        intake_lines.append(f"- Timeline: {intake.timeline}")
    if intake.budget_range:
        intake_lines.append(f"- Budget range: {intake.budget_range}")
    for c in intake.constraints:
        intake_lines.append(f"- Constraint: {c}")
    for contact in intake.contacts:
        role = f" ({contact.role})" if contact.role else ""
        intake_lines.append(f"- Contact: {contact.name}{role}")
    parts.append("## Intake\n" + "\n".join(intake_lines))

    for notes in (upstream.discovery, upstream.live_assist):
        if not notes:
            continue
        label = "Discovery" if notes.source == "discovery" else "Live Assist"
        lines: list[str] = []
        for f in notes.findings:
            lines.append(f"- Finding: {f}")
        for r in notes.requirements:
            lines.append(f"- Requirement: {r}")
        for s in notes.stakeholders:
            role = f" ({s.role})" if s.role else ""
            lines.append(f"- Stakeholder: {s.name}{role}")
        if notes.notes:
            lines.append(f"- Notes: {notes.notes}")
        for ref in notes.transcript_refs:
            title = ref.title or "transcript"
            lines.append(f"\n### {label} transcript — {title}\n{ref.text}")
        if lines:
            parts.append(f"## {label}\n" + "\n".join(lines))

    arch = upstream.architecture
    if arch and (arch.services or arch.assumptions or arch.nfrs):
        lines = []
        for svc in arch.services:
            purpose = f" — {svc.purpose}" if svc.purpose else ""
            lines.append(f"- Service: {svc.name}{purpose}")
        for a in arch.assumptions:
            lines.append(f"- Assumption: {a}")
        for n in arch.nfrs:
            lines.append(f"- NFR: {n}")
        parts.append("## Architecture\n" + "\n".join(lines))

    cost = upstream.cost
    if cost and (cost.line_items or cost.source_link or cost.assumptions or cost.region):
        lines = []
        # Deliberately omits monthly/annual totals and per-service amounts. The cost
        # estimate agent's calculator is the single source of truth for AWS run-rate;
        # restating its numbers in the SOW is how the two drift apart.
        if cost.source_link:
            lines.append(f"- Cost estimate link: {cost.source_link}")
        if cost.region:
            lines.append(f"- Region: {cost.region}")
        for li in cost.line_items:
            lines.append(f"- Costed service: {li.service}")
        for a in cost.assumptions:
            lines.append(f"- Assumption: {a}")
        lines.append(
            "- Pricing instruction: the cost estimate link above is the authoritative source "
            "for the AWS run-rate. Do NOT restate monthly or annual AWS cost figures and do "
            "NOT build a cost table in the SOW. In the Pricing section, state the one-time "
            "delivery fee, then refer the reader to the cost estimate and reproduce the link."
        )
        parts.append("## Cost\n" + "\n".join(lines))

    return "\n\n".join(parts)


def upstream_cross_checks(upstream: UpstreamInputs | None) -> list[str]:
    """Build concrete verify cross-check instructions from upstream inputs."""
    if upstream is None:
        return []
    checks: list[str] = []

    cost = upstream.cost
    if cost and (cost.source_link or cost.line_items):
        if cost.source_link:
            checks.append(
                "The Pricing section must refer the reader to the cost estimate and include "
                f"the cost calculator link ({cost.source_link}). It must NOT contain a cost "
                "table or restate monthly/annual AWS run-rate figures, which would duplicate "
                "and drift from the cost estimate agent's output."
            )
        if cost.line_items:
            names = ", ".join(li.service for li in cost.line_items)
            checks.append(f"SOW pricing/services should reconcile with cost line items: {names}.")

    arch = upstream.architecture
    if arch and arch.services:
        names = ", ".join(s.name for s in arch.services)
        checks.append(
            "Name every architecture service explicitly in the Scope of Work (or "
            "architecture/scope section), with its full name including any parenthetical "
            f"qualifier, as either in scope or out of scope: {names}."
        )

    intake = upstream.intake
    if intake.timeline:
        checks.append(
            f"SOW timeline must be consistent with the intake timeline ({intake.timeline})."
        )
    if intake.budget_range:
        checks.append(
            "SOW commercials must be consistent with the intake budget range "
            f"({intake.budget_range})."
        )

    return checks


def render_project_transcript(
    project_details: str,
    transcripts: list[str],
    clarification_answers: dict[str, str],
    upstream: UpstreamInputs | None = None,
) -> str:
    parts: list[str] = []
    if upstream is not None:
        parts.append(render_upstream_inputs(upstream))
    if project_details.strip():
        parts.append(f"## Project Details\n{project_details.strip()}")
    if clarification_answers:
        qa_lines = [f"- **{qid}**: {ans}" for qid, ans in clarification_answers.items() if ans]
        if qa_lines:
            parts.append("## Clarification Answers\n" + "\n".join(qa_lines))
    non_empty = [t.strip() for t in transcripts if t and t.strip()]
    if non_empty:
        parts.append("## Transcripts\n" + "\n\n---\n\n".join(non_empty))
    return "\n\n".join(parts) if parts else "(No project details or transcripts were provided.)"


def build_full_system_prompt(
    client_name: str,
    category: str,
    sample: str,
    project_details: str,
    transcripts: list[str],
    clarification_answers: dict[str, str],
    company: CompanyProfile,
    upstream: UpstreamInputs | None = None,
) -> str:
    prompt = fill_system_prompt(client_name, category, company)
    transcript_body = render_project_transcript(
        project_details, transcripts, clarification_answers, upstream
    )
    checks = upstream_cross_checks(upstream)
    if checks:
        # Guide the model up front; the post-generation rules still verify these deterministically.
        transcript_body += "\n\n## Consistency Requirements\n" + "\n".join(f"- {c}" for c in checks)
    prompt = inject_tag(prompt, "project_transcript", transcript_body)
    prompt = inject_tag(prompt, "template_reference", sample or "(No sample provided.)")
    return prompt
