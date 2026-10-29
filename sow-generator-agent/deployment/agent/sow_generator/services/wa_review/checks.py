"""The deterministic Well-Architected checks run against a SOW draft.

Two kinds of check, because two different mistakes matter here:

  `REQUIRED_TOPICS` — the SOW is silent on something a Well-Architected engagement must commit
  to (backups, encryption, observability, performance targets). Absence is the finding.

  `DECISION_CHECKS` — the SOW says something that contradicts a pillar: it leaves a compute
  choice open, or it claims to be well-architected while scoping a single Availability Zone.
  Presence is the finding.

The checks are textual on purpose. A SOW is prose, so a regex over its sections is the honest
tool; anything more confident would be pretending. Everything here is advisory and names the
evidence it fired on, so a reviewer can disagree quickly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from sow_generator.schemas.upstream import UpstreamInputs
from sow_generator.schemas.wa import WaFinding
from sow_generator.services.draft_rules.markdown import ParsedDraft

# Sections whose prose carries the technical commitments. The scope section is where the
# architect records service decisions, so most checks read it plus the whole draft as fallback.
TECHNICAL_SECTIONS = [
    "Scope of Work", "Scope of Services", "Scope", "Solution", "Architecture",
    "Technical Approach", "Approach", "Deliverables", "Assumptions",
]

MAX_EVIDENCE_CHARS = 240


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _first_match(text: str, patterns: Iterable[re.Pattern[str]]) -> Optional[re.Match[str]]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match
    return None


def _evidence(text: str, match: re.Match[str]) -> str:
    """The sentence the match sits in, trimmed, so a reviewer can judge it at a glance."""
    start = max(0, text.rfind("\n", 0, match.start()) + 1)
    end = text.find("\n", match.end())
    line = text[start : end if end != -1 else len(text)].strip()
    line = re.sub(r"\s+", " ", line)
    return line[:MAX_EVIDENCE_CHARS]


@dataclass(frozen=True)
class RequiredTopic:
    """A commitment the SOW must make. The finding fires when no pattern matches."""

    id: str
    pillar: str
    severity: str
    title: str
    detail: str
    suggestion: str
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class DecisionCheck:
    """A contradiction in what the SOW does say. `evaluate` returns a finding or None."""

    id: str
    evaluate: Callable[[ParsedDraft, str, Optional[UpstreamInputs]], Optional[WaFinding]]


# ----------------------------------------------------------------- required topics
REQUIRED_TOPICS: tuple[RequiredTopic, ...] = (
    RequiredTopic(
        id="wa_reliability_backup_recovery",
        pillar="reliability",
        severity="high",
        title="No backup or recovery commitment",
        detail="The SOW does not state how the workload recovers from data loss, so RPO and RTO are "
               "undefined and the delivery team has no target to build to.",
        suggestion="State the backup mechanism and retention, and give RPO and RTO as outcomes of "
                   "restoring from backup.",
        patterns=_rx(r"\bRPO\b", r"\bRTO\b", r"\bback-?ups?\b", r"\bsnapshots?\b",
                     r"point-in-time recovery", r"\bdisaster recovery\b", r"\brestore\b"),
    ),
    RequiredTopic(
        id="wa_reliability_availability_target",
        pillar="reliability",
        severity="medium",
        title="No availability target",
        detail="No availability or uptime target is stated, so there is nothing to design the "
               "redundancy level against and nothing to hold delivery to.",
        suggestion="State the availability target (for example 99.9% monthly) or say explicitly that "
                   "the pilot carries no availability commitment.",
        patterns=_rx(r"\bavailability\b", r"\buptime\b", r"\bSLA\b", r"\bhigh[- ]availability\b",
                     r"\b99\.\d+\s*%", r"\bresilien\w+"),
    ),
    RequiredTopic(
        id="wa_security_encryption",
        pillar="security",
        severity="high",
        title="No encryption commitment",
        detail="The SOW does not commit to encrypting data at rest or in transit, which is a baseline "
               "Security pillar expectation and a common customer audit question.",
        suggestion="State encryption at rest (KMS) and in transit (TLS), and name the key management "
                   "approach.",
        patterns=_rx(r"\bencrypt\w*", r"\bKMS\b", r"\bTLS\b", r"\bHTTPS\b", r"\bat rest\b",
                     r"\bin transit\b"),
    ),
    RequiredTopic(
        id="wa_security_identity",
        pillar="security",
        severity="medium",
        title="No identity or access-control approach",
        detail="The SOW does not say how access is granted or restricted, so least privilege cannot be "
               "verified and the customer cannot review who will hold which permissions.",
        suggestion="Name the identity provider and sign-in model, and commit to least-privilege IAM "
                   "roles.",
        patterns=_rx(r"\bIAM\b", r"least privilege", r"\bRBAC\b", r"\bSSO\b", r"\bCognito\b",
                     r"\bEntra\b", r"\bSAML\b", r"\bOIDC\b", r"\bauthenticat\w+", r"\bauthoris\w+",
                     r"\bauthoriz\w+", r"access control"),
    ),
    RequiredTopic(
        id="wa_operations_observability",
        pillar="operational_excellence",
        severity="high",
        title="No observability commitment",
        detail="The SOW does not commit to logging, metrics or alerting, so the workload would go live "
               "with no way to detect or diagnose failure.",
        suggestion="State what is logged, which metrics and alarms exist, and who receives alerts.",
        patterns=_rx(r"\bmonitor\w*", r"\bobservabilit\w+", r"\blogg?ing\b", r"\bCloudWatch\b",
                     r"\balarms?\b", r"\balert\w*", r"\bdashboards?\b", r"\btracing\b", r"\bX-Ray\b"),
    ),
    RequiredTopic(
        id="wa_operations_handover",
        pillar="operational_excellence",
        severity="medium",
        title="No runbook or handover commitment",
        detail="No operational handover is described, so it is unclear who runs the workload after "
               "go-live or what documentation they receive.",
        suggestion="Add a runbook or operational handover deliverable and name the team that takes "
                   "ownership.",
        patterns=_rx(r"\brunbook\b", r"\bhand-?over\b", r"\bknowledge transfer\b", r"\bsupport model\b",
                     r"\boperational documentation\b", r"\btraining\b", r"\bdocumentation\b"),
    ),
    RequiredTopic(
        id="wa_performance_targets",
        pillar="performance_efficiency",
        severity="medium",
        title="No performance target",
        detail="The SOW states no latency, throughput or volume target, so the services cannot be sized "
               "and the cost estimate rests on guesses.",
        suggestion="State the expected volumes and a latency or throughput target for the main flow.",
        patterns=_rx(r"\blatenc\w+", r"\bthroughput\b", r"\brequests? per\b", r"\btransactions? per\b",
                     r"\bconcurren\w+", r"\bresponse time\b", r"\bp9[59]\b", r"\bTPS\b", r"\bQPS\b"),
    ),
    RequiredTopic(
        id="wa_cost_controls",
        pillar="cost_optimization",
        severity="low",
        title="No cost-control approach",
        detail="The SOW does not mention how running cost is kept under control, so there is no stated "
               "owner or mechanism for cost once the workload is live.",
        suggestion="Mention right-sizing, tagging for cost allocation, budgets or alarms, and any "
                   "Savings Plan assumption.",
        patterns=_rx(r"right[- ]siz\w+", r"\bSavings Plans?\b", r"\bReserved Instances?\b",
                     r"cost allocation", r"\btagging\b", r"\bbudgets?\b", r"\bcost alarm\w*",
                     r"\bauto[- ]?scal\w+", r"\bserverless\b"),
    ),
    RequiredTopic(
        id="wa_sustainability_efficiency",
        pillar="sustainability",
        severity="low",
        title="No efficiency or lifecycle commitment",
        detail="Nothing in the SOW addresses using no more resource than needed, which the "
               "Sustainability pillar asks for and which usually also lowers cost.",
        suggestion="Mention Graviton or managed serverless where it fits, storage lifecycle policies, "
                   "and switching off non-production outside working hours.",
        patterns=_rx(r"\bGraviton\b", r"\barm64\b", r"lifecycle polic\w+", r"\bstorage class\w*",
                     r"\bGlacier\b", r"Intelligent[- ]Tiering", r"\bscale to zero\b",
                     r"\bserverless\b", r"\bauto[- ]?scal\w+"),
    ),
)


# ----------------------------------------------------------------- decision checks
# Competing ways to run containers or code. Naming several without choosing is exactly the
# "ECS vs EKS vs Fargate" ambiguity this stage exists to catch before a diagram is drawn.
COMPUTE_OPTIONS: dict[str, tuple[re.Pattern[str], ...]] = {
    "Amazon ECS": _rx(r"\bECS\b", r"Elastic Container Service"),
    "Amazon EKS": _rx(r"\bEKS\b", r"Elastic Kubernetes Service", r"\bKubernetes\b"),
    "AWS Fargate": _rx(r"\bFargate\b"),
    "Amazon EC2": _rx(r"\bEC2\b", r"Elastic Compute Cloud"),
    "AWS Lambda": _rx(r"\bLambda\b"),
}

# Phrasings where two services are one coherent choice, not a pair of alternatives:
# "an ECS service on Fargate" is the normal way to describe Fargate, not an open question.
COMBINED_RX = _rx(
    r"\bECS\b[^.\n]{0,40}\b(?:on|with|using)\b[^.\n]{0,20}\bFargate\b",
    r"\bEKS\b[^.\n]{0,40}\b(?:on|with|using)\b[^.\n]{0,20}\bFargate\b",
    r"\bFargate\b[^.\n]{0,30}\b(?:on|for)\b[^.\n]{0,20}\b(?:ECS|EKS)\b",
    r"\bECS\b[^.\n]{0,40}\b(?:on|with|using)\b[^.\n]{0,20}\bEC2\b",
)

# Markers that a sentence is listing alternatives or deferring the choice rather than making it.
UNDECIDED_RX = _rx(
    r"\bor\b", r"\bversus\b", r"\bvs\.?\b", r"\beither\b", r"\bone of\b", r"\bany of\b",
    r"\bTBD\b", r"to be (?:determined|decided|confirmed|agreed|selected)", r"\boptions?\b",
    r"\bcould be\b", r"\bmay be\b", r"\bas (?:appropriate|needed|required)\b",
)

SINGLE_AZ_RX = _rx(
    r"single[- ]A\.?Z", r"\bone Availability Zone\b", r"a single Availability Zone",
    r"availability[_ ]zones?\s*[:=]\s*1\b", r"\bmulti[_ ]az\s*[:=]\s*(?:false|no)\b",
)
MULTI_AZ_RX = _rx(
    r"multi[- ]A\.?Z", r"\btwo or more Availability Zones\b", r"\btwo Availability Zones\b",
    r"\bthree Availability Zones\b", r"across (?:two|three|multiple) Availability Zones",
    r"\bAvailability Zones\b",
)
WA_CLAIM_RX = _rx(
    r"well[- ]architected", r"\bhighly available\b", r"\bhigh availability\b",
    r"\bfault[- ]toleran\w+", r"\bresilient by design\b",
)


def _single_az(text: str, upstream: Optional[UpstreamInputs]) -> Optional[str]:
    """Evidence that the engagement is scoped to one AZ, from the SOW or the architecture block."""
    match = _first_match(text, SINGLE_AZ_RX)
    if match:
        return _evidence(text, match)
    arch = upstream.architecture if upstream else None
    for line in list(getattr(arch, "assumptions", []) or []) + list(getattr(arch, "nfrs", []) or []):
        hit = _first_match(str(line), SINGLE_AZ_RX)
        if hit:
            return re.sub(r"\s+", " ", str(line))[:MAX_EVIDENCE_CHARS]
    return None


def _check_compute_decision(draft: ParsedDraft, text: str,
                            upstream: Optional[UpstreamInputs]) -> Optional[WaFinding]:
    """A single sentence that offers competing compute options instead of choosing one.

    Scoped to one sentence on purpose. A SOW that uses Lambda for events and Fargate for services
    legitimately names both; what matters is "ECS, EKS or Fargate" in the same breath, which means
    the decision has not been made. "An ECS service on Fargate" is one choice and is excluded.
    """
    for line in _sentences(text):
        if _first_match(line, COMBINED_RX):
            continue
        named = sorted(name for name, patterns in COMPUTE_OPTIONS.items()
                       if _first_match(line, patterns))
        if len(named) < 2 or not _first_match(line, UNDECIDED_RX):
            continue
        return WaFinding(
            id="wa_operations_compute_decision_open",
            pillar="operational_excellence",
            severity="high",
            title="Competing compute options left undecided",
            detail=f"The SOW offers {' / '.join(named)} as alternatives rather than recording which "
                   f"one is chosen and why. The architecture diagram and the cost estimate are both "
                   f"built from this choice, so leaving it open forces a loop back through all three "
                   f"stages.",
            suggestion="Pick one compute model in the SOW, state the reason, and record the others as "
                       "rejected alternatives or out of scope.",
            evidence=re.sub(r"\s+", " ", line).strip()[:MAX_EVIDENCE_CHARS],
            section=_best_section(draft),
            source="deterministic",
        )
    return None


def _sentences(text: str) -> list[str]:
    """Lines, then sentences within them. Bullets are lines, so a bullet is one unit."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        out.extend(part for part in re.split(r"(?<=[.;:])\s+", line) if part.strip())
    return out


def _check_single_az(draft: ParsedDraft, text: str,
                     upstream: Optional[UpstreamInputs]) -> Optional[WaFinding]:
    """A single-AZ scope, which the Reliability pillar expects to be at least two."""
    evidence = _single_az(text, upstream)
    if not evidence:
        return None
    return WaFinding(
        id="wa_reliability_single_az",
        pillar="reliability",
        severity="high",
        title="Workload scoped to a single Availability Zone",
        detail="The Reliability pillar expects a workload to span at least two Availability Zones "
               "(REL10-BP02). A single zone means a zone failure is an outage recoverable only from "
               "backup, and because the diagram feeds the cost calculator the quoted price is a "
               "single-zone price.",
        suggestion="Scope at least two Availability Zones, or state plainly that single-AZ is a "
                   "deliberate pilot limitation with the residual risk and the path to multi-AZ.",
        evidence=evidence,
        section="Scope of Work",
        source="deterministic",
    )


def _check_well_architected_claim(draft: ParsedDraft, text: str,
                                  upstream: Optional[UpstreamInputs]) -> Optional[WaFinding]:
    """A resiliency claim the scope does not support."""
    claim = _first_match(text, WA_CLAIM_RX)
    if not claim:
        return None
    if not _single_az(text, upstream):
        return None
    return WaFinding(
        id="wa_reliability_overclaimed_resiliency",
        pillar="reliability",
        severity="high",
        title="Resiliency claim the scope does not support",
        detail="The SOW describes the solution as well-architected or highly available while scoping a "
               "single Availability Zone. The claim is not defensible in a customer review and "
               "undermines the rest of the document.",
        suggestion="Either scope two or more Availability Zones, or remove the resiliency claim and "
                   "describe the single-AZ limitation honestly.",
        evidence=_evidence(text, claim),
        section="Scope of Work",
        source="deterministic",
    )


def _check_scope_split(draft: ParsedDraft, text: str,
                       upstream: Optional[UpstreamInputs]) -> Optional[WaFinding]:
    """No out-of-scope list. The architecture stage consumes this split, so it cannot be missing."""
    if _first_match(text, _rx(r"out of scope", r"out-of-scope", r"\bexclusions?\b", r"\bnot included\b")):
        return None
    return WaFinding(
        id="wa_operations_missing_out_of_scope",
        pillar="operational_excellence",
        severity="high",
        title="No out-of-scope list",
        detail="The SOW states what is in scope but never what is excluded. The architecture stage "
               "takes this split as its input, so without it the design has nothing stopping it from "
               "adding services the customer never agreed to pay for.",
        suggestion="Add an explicit Out of Scope list naming the capabilities and services excluded "
                   "from this engagement.",
        evidence="No 'out of scope', 'exclusions' or 'not included' wording found in the draft.",
        section="Scope of Work",
        source="deterministic",
    )


DECISION_CHECKS: tuple[DecisionCheck, ...] = (
    DecisionCheck("wa_operations_compute_decision_open", _check_compute_decision),
    DecisionCheck("wa_reliability_single_az", _check_single_az),
    DecisionCheck("wa_reliability_overclaimed_resiliency", _check_well_architected_claim),
    DecisionCheck("wa_operations_missing_out_of_scope", _check_scope_split),
)


def evaluate_required_topic(topic: RequiredTopic, draft: ParsedDraft, text: str) -> Optional[WaFinding]:
    """A finding when the SOW says nothing about the topic anywhere."""
    if _first_match(text, topic.patterns):
        return None
    return WaFinding(
        id=topic.id,
        pillar=topic.pillar,          # type: ignore[arg-type]
        severity=topic.severity,      # type: ignore[arg-type]
        title=topic.title,
        detail=topic.detail,
        suggestion=topic.suggestion,
        evidence="The SOW contains no wording on this topic.",
        section=_best_section(draft),
        source="deterministic",
    )


def _best_section(draft: ParsedDraft) -> str:
    for name in TECHNICAL_SECTIONS:
        for section in draft.sections:
            if name.lower() in section.heading.lower():
                return section.heading
    return ""
