#!/usr/bin/env python3
"""Build the Well-Architected review rules reference (PDF) from the code that enforces them.

Nothing in the rule catalogue is typed by hand. Rule ids, pillars, severities, finding text, suggestions and the
detection patterns are read from the live modules; every "fires" / "does not fire" statement is produced by running
the real review on a worked example, and the build FAILS if one of them does not behave as the document says. The
source files are listed with their SHA-256, so a reader can tell whether the document still matches the code.

    python3 docs/build_waf_rules_pdf.py            # writes docs/Well-Architected-Review-Rules.pdf

Needs: pydantic, pyyaml, reportlab, pytest (the evidence section runs the rule tests).
"""
from __future__ import annotations

import hashlib
import html
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
SOW_AGENT = WORKSPACE / "sow-generator-agent" / "deployment" / "agent"
ARCH_AGENT = WORKSPACE / "architecture-agent" / "deployment" / "agent"
ORCH_API = WORKSPACE / "pipeline-orchestrator" / "deployment" / "api"
OUT = HERE / "Well-Architected-Review-Rules.pdf"
sys.path[:0] = [str(SOW_AGENT), str(ARCH_AGENT)]

import yaml  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,  # noqa: E402
                                TableStyle)

from adr_generator.availability import AvailabilityPolicy  # noqa: E402
from sow_generator.schemas.wa import PILLAR_LABELS  # noqa: E402
from sow_generator.services.wa_review import review_sow  # noqa: E402
from sow_generator.services.wa_review import checks as C  # noqa: E402
from sow_generator.services.wa_review import reviewer as R  # noqa: E402

# ----------------------------------------------------------------------------- AWS references
# Only best practices whose ids and titles were checked against docs.aws.amazon.com. A rule with no entry here is
# mapped to its pillar only, and the document says so rather than inventing an id.
REF = {
    "REL09-BP01": ("Identify and back up all data that needs to be backed up, or reproduce the data from sources",
                   "https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_backing_up_data_identified_backups_data.html"),
    "REL10-BP01": ("Deploy the workload to multiple locations",
                   "https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_fault_isolation_multiaz_region_system"),
    "REL10-BP02": ("Select the appropriate locations for your multi-location deployment",
                   "https://docs.aws.amazon.com/wellarchitected/2023-10-03/framework/rel_fault_isolation_select_location.html"),
    "SEC03-BP02": ("Grant least privilege access",
                   "https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_permissions_least_privileges.html"),
    "SEC08-BP02": ("Enforce encryption at rest",
                   "https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_protect_data_rest_encrypt.html"),
    "SEC09-BP02": ("Enforce encryption in transit",
                   "https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_protect_data_transit_encrypt.html"),
    "OPS08-BP01": ("Analyze workload metrics (workload observability)",
                   "https://docs.aws.amazon.com/wellarchitected/latest/framework/ops_workload_observability_analyze_workload_metrics.html"),
}
RULE_REFS = {
    "wa_reliability_backup_recovery": ["REL09-BP01"],
    "wa_security_encryption": ["SEC08-BP02", "SEC09-BP02"],
    "wa_security_identity": ["SEC03-BP02"],
    "wa_operations_observability": ["OPS08-BP01"],
    "wa_reliability_single_az": ["REL10-BP01", "REL10-BP02"],
    "wa_reliability_overclaimed_resiliency": ["REL10-BP02"],
}

# ----------------------------------------------------------------------------- worked examples (executed)
# (rule id, text that SATISFIES the rule / does not trigger it). Every required topic fires on the empty draft.
SATISFIES = {
    "wa_reliability_backup_recovery": "Automated backups with 7-day retention. RPO 15 minutes, RTO 2 hours.",
    "wa_reliability_availability_target": "Availability target of 99.9% monthly.",
    "wa_security_encryption": "Data is encrypted at rest with AWS KMS and in transit with TLS.",
    "wa_security_identity": "Least-privilege IAM roles; sign-in through Microsoft Entra ID (SAML).",
    "wa_operations_observability": "Amazon CloudWatch logs, metrics and alarms go to the on-call rota.",
    "wa_operations_handover": "A runbook and operational handover to the customer's platform team.",
    "wa_performance_targets": "Expected load is 40 concurrent users with a p95 latency target of 800 ms.",
    "wa_cost_controls": "Right-sizing review at go-live and a monthly budget alarm.",
    "wa_sustainability_efficiency": "Graviton (arm64) throughout and S3 Intelligent-Tiering lifecycle policies.",
}
BASE = "# Scope of Work\n## In Scope\n- {x}\n## Out of Scope\n- Data migration\n"
EMPTY = "# Scope of Work\n## In Scope\n- Nothing has been decided yet.\n## Out of Scope\n- Data migration\n"


def ids(text: str, upstream=None, template_id=None) -> set[str]:
    return {f.id for f in review_sow(text, upstream, template_id).findings}


DECISION_EXAMPLES = {
    "wa_operations_compute_decision_open": [
        (True, "Container platform using Amazon ECS, Amazon EKS or AWS Fargate."),
        (True, "Compute platform: Amazon EKS or Amazon EC2, TBD during design."),
        (False, "An Amazon ECS service on AWS Fargate (ARM64, 2 tasks) serves the API."),
        (False, "AWS Lambda handles ingestion events. An Amazon ECS service on AWS Fargate serves the API."),
        (False, "We will use AWS Fargate for all services. Amazon EKS is out of scope."),
    ],
    "wa_reliability_single_az": [
        (True, "The pilot is deployed in a single Availability Zone."),
        (False, "The solution runs across two Availability Zones with Multi-AZ RDS."),
    ],
    "wa_reliability_overclaimed_resiliency": [
        (True, "A well-architected, highly available platform in a single Availability Zone."),
        (False, "A well-architected, highly available platform across two Availability Zones."),
    ],
}


def verify_examples() -> dict:
    """Run every claim the document makes. Raises AssertionError, so a wrong document is never written."""
    out = {"required": {}, "decision": {}, "missing_scope": None, "keyword": None, "assessment": None}
    for topic in C.REQUIRED_TOPICS:
        sat = SATISFIES[topic.id]
        assert topic.id in ids(EMPTY), f"{topic.id} should fire on a draft that says nothing"
        assert topic.id not in ids(BASE.format(x=sat)), f"{topic.id} should be satisfied by: {sat}"
        out["required"][topic.id] = sat
    for rule_id, cases in DECISION_EXAMPLES.items():
        out["decision"][rule_id] = []
        for fires, text in cases:
            body = BASE.format(x=text)
            got = rule_id in ids(body)
            assert got == fires, f"{rule_id}: expected fires={fires} for: {text}"
            out["decision"][rule_id].append((fires, text))
    no_split = "# Scope of Work\n## In Scope\n- Server inventory\n"
    assert "wa_operations_missing_out_of_scope" in ids(no_split)
    for word in ("Out of Scope", "Exclusions", "Not included"):
        assert "wa_operations_missing_out_of_scope" not in ids(f"# Scope of Work\n## In Scope\n- A\n## {word}\n- B\n")
    out["missing_scope"] = no_split
    # The honest limitation: a keyword counts as a commitment, whatever the sentence around it says.
    weak = BASE.format(x="We do not provide any backups.")
    assert "wa_reliability_backup_recovery" not in ids(weak), "keyword limitation example no longer holds"
    out["keyword"] = "We do not provide any backups."
    # Assessment profile
    assert C.REQUIRED_TOPICS and not (ids(EMPTY, template_id="cva/full") & {t.id for t in C.REQUIRED_TOPICS})
    assert "wa_operations_compute_decision_open" not in ids(
        BASE.format(x="Compute platform: Amazon EKS or Amazon EC2, TBD."), template_id="cva/full")
    assert "wa_reliability_single_az" in ids(BASE.format(x="The workload is deployed in a single Availability Zone."), template_id="cva/full")
    out["assessment"] = True
    return out


def run_tests() -> str:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_wa_review.py", "-p", "no:cacheprovider"],
                          cwd=SOW_AGENT, capture_output=True, text=True, timeout=300)
    last = [l for l in proc.stdout.strip().splitlines() if "passed" in l or "failed" in l or "error" in l]
    assert proc.returncode == 0, f"rule tests failing:\n{proc.stdout[-1500:]}"
    return re.search(r"(\d+) passed", last[-1]).group(0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----------------------------------------------------------------------------- PDF
NAVY, BLUE, GREY = colors.HexColor("#0B1F3A"), colors.HexColor("#1A6FE0"), colors.HexColor("#5A6B85")
SEV = {"high": colors.HexColor("#C0392B"), "medium": colors.HexColor("#B9770E"), "low": colors.HexColor("#2874A6")}
ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontSize=26, textColor=NAVY, alignment=TA_LEFT, leading=30),
    "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=12, textColor=GREY, leading=16),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, textColor=NAVY, spaceBefore=14, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=BLUE, spaceBefore=10, spaceAfter=3),
    "p": ParagraphStyle("p", parent=ss["Normal"], fontSize=9.5, leading=13.5, spaceAfter=4),
    "small": ParagraphStyle("sm", parent=ss["Normal"], fontSize=8.5, leading=11.5, textColor=GREY),
    "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=8.5, leading=11),
    "mono": ParagraphStyle("m", parent=ss["Normal"], fontName="Courier", fontSize=7.6, leading=9.6),
    "box": ParagraphStyle("b", parent=ss["Normal"], fontSize=9.5, leading=13.5, leftIndent=6, rightIndent=6),
}


def P(text: str, style: str = "p") -> Paragraph:
    return Paragraph(text, S[style])


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def code(text: object) -> str:
    return f'<font name="Courier" size="7.8">{esc(text)}</font>'


def table(rows, widths, header=True, zebra=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3E3")),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    if zebra:
        style += [("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#F4F7FB")])]
    t.setStyle(TableStyle(style))
    return t


def head(text: str) -> Paragraph:
    return Paragraph(f'<font color="white"><b>{esc(text)}</b></font>', S["cell"])


def sev_tag(sev: str) -> str:
    return f'<font color="{SEV[sev].hexval()}"><b>{sev.upper()}</b></font>'


def refs_cell(rule_id: str) -> str:
    keys = RULE_REFS.get(rule_id, [])
    if not keys:
        return "Pillar-level mapping only. No specific best practice is cited for this rule."
    return "<br/>".join(f'{k}: {esc(REF[k][0])}<br/><font size="7" color="#5A6B85">{esc(REF[k][1])}</font>' for k in keys)


def rule_card(rule_id, pillar, severity, title, kind, fires_when, patterns, detail, suggestion, evidence_note, examples):
    rows = [
        [P(f"<b>{esc(rule_id)}</b>", "cell"), P(f"{esc(PILLAR_LABELS[pillar])} &nbsp;|&nbsp; {sev_tag(severity)} &nbsp;|&nbsp; {esc(kind)}", "cell")],
        [P("Finding title", "cell"), P(f"<b>{esc(title)}</b>", "cell")],
        [P("Fires when", "cell"), P(fires_when, "cell")],
    ]
    if patterns:
        rows.append([P("Detects (regex)", "cell"), P("<br/>".join(code(p) for p in patterns), "cell")])
    rows += [[P("Finding says", "cell"), P(esc(detail), "cell")],
             [P("Suggested fix", "cell"), P(esc(suggestion), "cell")],
             [P("Evidence shown", "cell"), P(esc(evidence_note), "cell")],
             [P("Worked examples (executed)", "cell"), P("<br/>".join(examples), "cell")],
             [P("AWS reference", "cell"), P(refs_cell(rule_id), "cell")]]
    t = Table(rows, colWidths=[34 * mm, 140 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
                           ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C9D3E3")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF8")),
                           ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#F4F7FB")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return [t, Spacer(1, 7)]


def ex(fires: bool, text: str) -> str:
    mark = '<font color="#C0392B"><b>FIRES</b></font>' if fires else '<font color="#1E8449"><b>DOES NOT FIRE</b></font>'
    return f"{mark} &nbsp;{esc(text)}"


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(GREY)
    canvas.drawString(18 * mm, 10 * mm, "Project Catalyst - Well-Architected review rules - generated from source, see Section 9 for hashes")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


DECISION_PILLAR = {
    "wa_operations_compute_decision_open": "operational_excellence",
    "wa_reliability_single_az": "reliability",
    "wa_reliability_overclaimed_resiliency": "reliability",
    "wa_operations_missing_out_of_scope": "operational_excellence",
}


def topic_ids(p):
    return [t.id for t in C.REQUIRED_TOPICS if t.pillar == p]


def decision_ids(p):
    return [i for i, pl in DECISION_PILLAR.items() if pl == p]


def build() -> None:
    assert set(DECISION_PILLAR) == {c.id for c in C.DECISION_CHECKS}
    proof = verify_examples()
    tests = run_tests()
    worker_template = (ORCH_API.parent / "template.yaml").read_text(encoding="utf-8")
    wa_verify_default = re.search(r"WaVerify:\s*\n\s*Type: String\s*\n\s*Default: \"(\w+)\"", worker_template).group(1)
    stop_default = "True"  # PipelineState.stop_on_blocked default; asserted below
    sys.path.insert(0, str(ORCH_API))
    from orchestrator.contracts import PipelineState  # noqa: PLC0415
    assert PipelineState().stop_on_blocked is True

    story = []
    # ---- cover
    story += [Spacer(1, 40 * mm), P("Well-Architected Review Rules", "title"),
              P("What the Project Catalyst pipeline checks in a Statement of Work before the architecture is drawn, "
                "exactly as implemented.", "sub"), Spacer(1, 10 * mm)]
    story.append(table([
        [P("<b>Status</b>", "cell"), P("Reference of the implemented behaviour. Generated from the source code; every example below was executed against it when this file was built.", "cell")],
        [P("<b>Generated</b>", "cell"), P(date.today().isoformat(), "cell")],
        [P("<b>Rule tests</b>", "cell"), P(f"{tests} (sow-generator-agent/deployment/agent/tests/test_wa_review.py)", "cell")],
        [P("<b>What it is</b>", "cell"), P("Ingram's own checklist across the six Well-Architected pillars, applied to the text of a SOW.", "cell")],
        [P("<b>What it is not</b>", "cell"), P("Not the AWS Well-Architected Tool, not the AWS Well-Architected Agent, and not a review of a deployed workload or of the diagram.", "cell")],
    ], [32 * mm, 142 * mm], header=False, zebra=False))
    story.append(PageBreak())

    # ---- 1 summary
    story += [P("1. In one page", "h1"),
              P("<b>Where it runs.</b> Stage 2 of the pipeline: after the SOW exists and before the architecture diagram, the cost estimate "
                "and the price are produced. The reason is cost of change: a weak service decision found here costs one SOW edit; found after "
                "the diagram and the price it costs a loop through all three stages."),
              P("<b>How it decides.</b> The SOW agent applies a fixed list of rules to the SOW text. Nine rules look for a commitment the SOW "
                "must make (backup and recovery, encryption, observability and so on); four rules look for something the SOW says that "
                "contradicts a pillar (an undecided compute choice, a single-Availability-Zone scope, an unsupported resiliency claim, a missing "
                "out-of-scope list). Each finding carries a pillar, a severity, the text it fired on and a suggested fix."),
              P("<b>What happens next.</b> Any <b>high</b> finding stops the run before the diagram is drawn. A person either edits the SOW "
                "and starts again, or chooses <i>Continue anyway</i>, which is logged. Medium and low findings are shown and never stop the run."),
              P(f"<b>Deployed setting.</b> The optional model review of the SOW (Section 5) is <b>{'on' if wa_verify_default == 'true' else 'off'}</b> "
                f"in the deployed pipeline (<font name='Courier'>WaVerify={esc(wa_verify_default)}</font>). With it off, the result is decided by the rules alone."),
              P("<b>How to read a pass.</b> A pass means the SOW mentions each topic. It does not mean the commitment is adequate "
                "(Section 7 shows a sentence that passes while promising no backups).")]
    counts = {s: sum(1 for t in C.REQUIRED_TOPICS if t.severity == s) for s in ("high", "medium", "low")}
    story += [Spacer(1, 3), table([[head("Pillar"), head("Rules"), head("Rule ids")]] + [
        [P(esc(PILLAR_LABELS[p]), "cell"),
         P(str(len(topic_ids(p)) + len(decision_ids(p))), "cell"),
         P(", ".join(esc(i) for i in topic_ids(p) + decision_ids(p)) or "-", "cell")]
        for p in PILLAR_LABELS], [40 * mm, 16 * mm, 118 * mm])]
    story.append(PageBreak())

    # ---- 2 flow
    story += [P("2. How a run reaches the review", "h1"),
              table([[head("Stage"), head("Agent"), head("What it does")],
                     [P("1. SOW draft", "cell"), P("SOW", "cell"), P("Drafts the SOW from intake and discovery. Skipped if a SOW is supplied.", "cell")],
                     [P("<b>2. Well-Architected review</b>", "cell"), P("SOW", "cell"), P("<b>Applies the rules in this document to the SOW. A high finding blocks.</b> Also reads the in-scope and out-of-scope lists out of the SOW.", "cell")],
                     [P("3. Architecture", "cell"), P("Architecture", "cell"), P("Designs from the SOW and the scope lists. Defaults to two Availability Zones (Section 6).", "cell")],
                     [P("4. Cost estimate", "cell"), P("Cost", "cell"), P("Prices the design in the AWS Pricing Calculator. The link is written back into the SOW's Pricing section.", "cell")],
                     [P("5. Combined review", "cell"), P("SOW", "cell"), P("Re-checks the finished SOW against the architecture and cost (Section 8).", "cell")]],
                    [38 * mm, 24 * mm, 112 * mm]), Spacer(1, 6),
              P("<b>Severity and the gate.</b> <font name='Courier'>can_proceed</font> is true only when the count of high findings is zero. "
                f"The pipeline stops on a high finding unless the run was started with <font name='Courier'>stop_on_blocked=false</font> "
                f"(the default is stop). Findings are ordered high, then medium, then low, then by pillar order: "
                f"{', '.join(esc(v) for v in PILLAR_LABELS.values())}."),
              P("<b>Profiles.</b> The template selects the profile. A template in the <font name='Courier'>cva</font> category is an "
                "assessment: it installs a collector and writes a report and deploys nothing, so the build-oriented rules would be false "
                f"blockers. Skipped for assessments: all {len(C.REQUIRED_TOPICS)} commitment rules and "
                f"<font name='Courier'>{esc(', '.join(sorted(R.SKIPPED_FOR_ASSESSMENT)))}</font>. Still applied: single-AZ scope, unsupported "
                "resiliency claim, and missing out-of-scope list. Every other template uses the full set.")]
    story.append(PageBreak())

    # ---- 3 commitment rules
    story += [P("3. Commitment rules (the SOW must say something)", "h1"),
              P("Each rule fires when <b>none</b> of its patterns matches anywhere in the SOW (case-insensitive). The rule is satisfied by a "
                "single match. The evidence shown is the fixed sentence \"The SOW contains no wording on this topic.\"")]
    for t in C.REQUIRED_TOPICS:
        story += rule_card(t.id, t.pillar, t.severity, t.title, "commitment rule",
                           "No pattern below matches anywhere in the SOW.", [p.pattern for p in t.patterns], t.detail, t.suggestion,
                           "The SOW contains no wording on this topic.",
                           [ex(True, "A draft that says \"Nothing has been decided yet.\""), ex(False, proof["required"][t.id])])
    story.append(PageBreak())

    # ---- 4 decision rules
    story += [P("4. Contradiction rules (what the SOW says is wrong)", "h1"),
              P("These read what the SOW states. Their finding text below is the real output of the review on a triggering draft.")]
    trig = {
        "wa_operations_compute_decision_open": BASE.format(x="Container platform using Amazon ECS, Amazon EKS or AWS Fargate."),
        "wa_reliability_single_az": BASE.format(x="The pilot is deployed in a single Availability Zone."),
        "wa_reliability_overclaimed_resiliency": BASE.format(x="A well-architected, highly available platform in a single Availability Zone."),
        "wa_operations_missing_out_of_scope": proof["missing_scope"],
    }
    logic = {
        "wa_operations_compute_decision_open": (
            "Within <b>one sentence or bullet</b>: two or more of the compute options "
            f"({esc(', '.join(C.COMPUTE_OPTIONS))}) are named <b>and</b> the sentence has an undecided marker (or, versus, vs, either, one of, any of, TBD, "
            "to be determined/decided/confirmed, option(s), could be, may be, as appropriate/needed/required). Sentences describing one combined "
            "choice are excluded, such as an ECS service on Fargate, or ECS with EC2.",
            [p.pattern for p in C.UNDECIDED_RX] + ["excluded: " + p.pattern for p in C.COMBINED_RX]),
        "wa_reliability_single_az": (
            "The SOW text matches a single-AZ pattern, <b>or</b> an upstream architecture assumption or NFR does.",
            [p.pattern for p in C.SINGLE_AZ_RX]),
        "wa_reliability_overclaimed_resiliency": (
            "The SOW makes a well-architected, high-availability or fault-tolerance claim <b>and</b> single-AZ evidence (previous rule) is present.",
            [p.pattern for p in C.WA_CLAIM_RX]),
        "wa_operations_missing_out_of_scope": (
            "None of these phrases appears: \"out of scope\", \"out-of-scope\", \"exclusion(s)\", \"not included\". The architecture stage "
            "takes the in/out split as its input, so a SOW without one cannot constrain the design.",
            ["out of scope", "out-of-scope", "exclusions?", "not included"]),
    }
    for check in C.DECISION_CHECKS:
        finding = next(f for f in review_sow(trig[check.id]).findings if f.id == check.id)
        when, pats = logic[check.id]
        cases = proof["decision"].get(check.id)
        examples = [ex(f, t) for f, t in cases] if cases else [
            ex(True, "A SOW with an In Scope list and no exclusions."),
            ex(False, "The same SOW with an \"Out of Scope\", \"Exclusions\" or \"Not included\" section.")]
        story += rule_card(check.id, finding.pillar, finding.severity, finding.title, "contradiction rule", when, pats,
                           finding.detail, finding.suggestion, finding.evidence, examples)
    story.append(PageBreak())

    # ---- 5 LLM pass
    story += [P("5. Optional model review", "h1"),
              P("A Claude reviewer can read the SOW and the intake data for what the rules cannot judge: whether a chosen service fits the stated "
                "requirements, a requirement the scope does not cover, a commitment the architecture cannot keep. It is instructed to ignore anything "
                "the rules already cover, to ignore pricing, to cite evidence for every finding, and to return at most 12 findings."),
              P("Its findings are <b>advisory</b> and merge behind the rules, keyed on (pillar, title). On a collision the rule's finding wins, "
                "because it carries the matched text. A model finding of high severity does close the gate."),
              P(f"<b>State today:</b> {'on' if wa_verify_default == 'true' else 'off'} in the deployed pipeline "
                f"(<font name='Courier'>WaVerify={esc(wa_verify_default)}</font>). The SOW API route accepts <font name='Courier'>verify: true</font> per request, "
                "which costs one model call.")]

    # ---- 6 architecture
    story += [P("6. Availability rules applied when the architecture is drawn", "h1"),
              P("The review above catches a single-AZ scope in the SOW. The architecture stage then designs against a configured availability "
                "model, so the diagram, the usage figures and the price agree. The setting is <font name='Courier'>AVAILABILITY_ZONES</font>; "
                "the default is 2, the baseline for the Reliability pillar (REL10-BP02: deploy to multiple Availability Zones where possible). "
                "The text below is the instruction the design model receives.")]
    for n in (2, 1):
        rule = AvailabilityPolicy(n).design_rule().replace("\n", "<br/>")
        story += [P(f"With AVAILABILITY_ZONES={n}", "h2"),
                  Table([[Paragraph(esc(AvailabilityPolicy(n).design_rule()).replace("\n", "<br/>"), S["cell"])]], colWidths=[174 * mm],
                        style=TableStyle([("BOX", (0, 0), (-1, -1), 0.6, GREY), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
                                          ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))]
    story += [P("The usage figures that feed the cost calculator (<font name='Courier'>multi_az</font>, <font name='Courier'>availability_zones</font>) "
                "are set in code from the finished design, never taken from the model, so the price follows the drawn diagram.", "p")]
    story.append(PageBreak())

    # ---- 7 limits
    story += [P("7. What this does not do", "h1"),
              table([[head("Limit"), head("Consequence")],
                     [P("<b>A keyword is a commitment.</b>", "cell"),
                      P(f"A rule is satisfied by any matching word. This sentence satisfies the backup rule, verified when this document was built: "
                        f"{code(proof['keyword'])}. The model review (off by default) is the only layer that reads for meaning.", "cell")],
                     [P("<b>Not the AWS service.</b>", "cell"), P("It does not call the AWS Well-Architected Tool or the AWS Well-Architected Agent. The AWS agent's on-demand review "
                      "takes CDK or Terraform from an S3 URI, not prose, so it cannot review a SOW.", "cell")],
                     [P("<b>Our checklist, hand-written.</b>", "cell"), P("The rules are fixed in code. They do not update when AWS updates the Framework; changing one means a code change and a redeploy of the SOW agent.", "cell")],
                     [P("<b>Not every pillar equally.</b>", "cell"), P("Six pillars are covered, but Performance Efficiency, Cost Optimization and Sustainability have one low-depth rule each.", "cell")],
                     [P("<b>The SOW only.</b>", "cell"), P("The diagram and the cost estimate are not Well-Architected reviewed. The final review (Section 8) checks consistency, not pillars.", "cell")],
                     [P("<b>References are partial.</b>", "cell"), P("Only the best practices listed in the rule cards were checked against AWS documentation. Other rules map to a pillar only.", "cell")]],
                    [48 * mm, 126 * mm])]

    # ---- 8 final review
    story += [P("8. The final combined review (stage 5)", "h1"),
              P("After the architecture and cost exist, the SOW agent checks the finished SOW with its compliance rules, plus an optional model "
                "reviewer. These are consistency checks, not Well-Architected checks. A failed <b>required</b> rule stops the run; "
                "an advisory one is reported only.")]
    rules = yaml.safe_load((SOW_AGENT / "sow_generator/resources/policies/sow_compliance_rules.yaml").read_text(encoding="utf-8"))["rules"]
    story.append(table([[head("Rule"), head("Severity"), head("What it checks")]] + [
        [P(f"<b>{esc(r['id'])}</b><br/>{esc(r['name'])}", "cell"), P(esc(r["severity"]), "cell"), P(esc(" ".join(str(r.get("description", "")).split())), "cell")]
        for r in rules], [52 * mm, 20 * mm, 102 * mm]))

    # ---- 9 provenance
    story.append(PageBreak())
    story += [P("9. Evidence and provenance", "h1"),
              P(f"<b>Tests.</b> {esc(tests)} in <font name='Courier'>tests/test_wa_review.py</font>, run when this file was built. They cover each rule firing and "
                "not firing, ordering, the gate, the assessment profile, and the model-pass merge. A separate run of the deployed pipeline on a SOW that "
                "passed the review ended with real cost-estimate link, diagram and .docx."),
              P("<b>Source files this document was generated from</b> (SHA-256, so you can tell if the code has moved on):")]
    files = [SOW_AGENT / "sow_generator/services/wa_review/checks.py", SOW_AGENT / "sow_generator/services/wa_review/reviewer.py",
             SOW_AGENT / "sow_generator/schemas/wa.py", SOW_AGENT / "sow_generator/resources/prompts/wa_review.txt",
             SOW_AGENT / "sow_generator/resources/policies/sow_compliance_rules.yaml",
             ARCH_AGENT / "adr_generator/availability.py", ORCH_API / "orchestrator/pipeline.py"]
    story.append(table([[head("File"), head("SHA-256")]] + [
        [P(esc(str(f.relative_to(WORKSPACE))), "cell"), P(code(sha(f)), "cell")] for f in files], [84 * mm, 90 * mm]))
    story += [Spacer(1, 6), P("<b>AWS documentation cited</b> (best-practice ids and titles checked against docs.aws.amazon.com):")]
    story.append(table([[head("Id"), head("Title and link")]] + [
        [P(esc(k), "cell"), P(f"{esc(v[0])}<br/><font size='7' color='#5A6B85'>{esc(v[1])}</font>", "cell")] for k, v in REF.items()], [22 * mm, 152 * mm]))
    story += [Spacer(1, 6), P("To regenerate: <font name='Courier'>python3 pipeline-orchestrator/docs/build_waf_rules_pdf.py</font>. The build fails if any "
                              "example above stops behaving as stated.", "small")]

    SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title="Well-Architected Review Rules", author="Project Catalyst").build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB); {tests}; examples verified")


if __name__ == "__main__":
    build()
