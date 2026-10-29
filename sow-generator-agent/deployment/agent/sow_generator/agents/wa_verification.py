"""The advisory LLM half of the SOW Well-Architected review; never raises and never flips a rule.

The deterministic checks in services/wa_review/checks.py find the absences and contradictions a
regex can prove. This pass is for the judgement a regex cannot make: whether the service the
architect chose actually fits the requirements. Its findings are advisory and merge behind the
deterministic ones (see services/wa_review/reviewer.merge_findings).
"""

import logging

from sow_generator.agents.factory import ROLE_VERIFICATION, build_agent
from sow_generator.agents.prompt_builder import render_upstream_inputs
from sow_generator.agents.verification import extract_json_object
from sow_generator.core.config import SowSettings
from sow_generator.repositories.prompt_repository import load_wa_review_prompt
from sow_generator.schemas.upstream import UpstreamInputs
from sow_generator.schemas.wa import WaFinding
from sow_generator.services.wa_review.reviewer import WaVerificationOutcome

logger = logging.getLogger(__name__)

_MAX_TOKENS = 4000
_DEFAULT_TEMPERATURE = 0.0
_MAX_MODEL_FINDINGS = 12


def render_wa_review_input(sow_markdown: str, upstream: UpstreamInputs | None) -> str:
    parts = [f"## SOW Draft (markdown)\n{sow_markdown.strip()}"]
    if upstream is not None:
        parts.append("## Original Inputs\n" + render_upstream_inputs(upstream))
    return "\n\n".join(parts)


def parse_findings(parsed: dict[str, object]) -> list[WaFinding]:
    """Model findings, skipping any that do not validate. A bad item never fails the review."""
    raw_findings = parsed.get("findings", [])
    if not isinstance(raw_findings, list):
        return []
    findings: list[WaFinding] = []
    for raw in raw_findings:
        if not isinstance(raw, dict):
            continue
        try:
            findings.append(WaFinding.model_validate({**raw, "source": "model"}))
        except ValueError:
            logger.warning("skipped a malformed model finding")
    return findings[:_MAX_MODEL_FINDINGS]


async def wa_review_draft(
    settings: SowSettings,
    sow_markdown: str,
    upstream: UpstreamInputs | None = None,
    *,
    model_id: str | None = None,
) -> WaVerificationOutcome:
    """Run the LLM Well-Architected pass over a draft.

    A model or transport failure yields ``status="failed"`` and non-JSON output yields
    ``status="empty_output"``, so a silent failure is distinguishable from a clean run with
    nothing to report.
    """
    model = model_id or settings.verification_model
    agent = build_agent(
        settings,
        role=ROLE_VERIFICATION,
        model_id=model,
        default_temperature=_DEFAULT_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        system_prompt=load_wa_review_prompt(),
    )

    accumulated = ""
    try:
        async for event in agent.stream_async(render_wa_review_input(sow_markdown, upstream)):
            if event.get("data"):
                accumulated += event["data"]
    except Exception:
        logger.exception("model stream failed", extra={"model_id": model})
        return WaVerificationOutcome(status="failed", model_id=model,
                                     detail="Well-Architected reviewer call failed.")

    parsed = extract_json_object(accumulated)
    if not parsed:
        logger.warning("model returned no parseable JSON object", extra={"model_id": model})
        return WaVerificationOutcome(status="empty_output", model_id=model,
                                     detail="Well-Architected reviewer returned no structured output.")
    return WaVerificationOutcome(status="ok", findings=parse_findings(parsed), model_id=model)
