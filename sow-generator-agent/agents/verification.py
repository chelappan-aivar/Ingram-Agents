"""The advisory LLM reviewer of a generated draft; never raises and never flips a rule."""

import json
import logging

from app.core.config import SowSettings
from app.modules.sow_generator.agents.factory import ROLE_VERIFICATION, build_agent
from app.modules.sow_generator.agents.prompt_builder import render_upstream_inputs
from app.modules.sow_generator.models import VerificationOutcome
from app.modules.sow_generator.repositories.prompt_repository import load_verification_prompt
from app.modules.sow_generator.schemas import ConsistencyIssue, UpstreamInputs

logger = logging.getLogger(__name__)

_MAX_TOKENS = 4000
_DEFAULT_TEMPERATURE = 0.0


def extract_json_object(text: str) -> dict[str, object] | None:
    """The first balanced top-level JSON object in text, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def render_verification_input(sow_markdown: str, upstream: UpstreamInputs | None) -> str:
    parts = [f"## SOW Draft (markdown)\n{sow_markdown.strip()}"]
    if upstream is not None:
        parts.append("## Original Inputs\n" + render_upstream_inputs(upstream))
    return "\n\n".join(parts)


def _parse_issues(parsed: dict[str, object]) -> list[ConsistencyIssue]:
    raw_issues = parsed.get("issues", [])
    if not isinstance(raw_issues, list):
        return []
    issues: list[ConsistencyIssue] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            continue
        try:
            issues.append(ConsistencyIssue.model_validate({**raw, "source": "model"}))
        except ValueError:
            logger.warning("skipped a malformed model issue")
    return issues


async def verify_draft(
    settings: SowSettings,
    sow_markdown: str,
    upstream: UpstreamInputs | None = None,
    *,
    model_id: str | None = None,
) -> VerificationOutcome:
    """Run the LLM reviewer over a draft.

    A model or transport failure yields ``status="failed"`` and non-JSON output yields
    ``status="empty_output"``, so callers can tell a silent failure from a clean run.
    """
    model = model_id or settings.verification_model
    agent = build_agent(
        settings,
        role=ROLE_VERIFICATION,
        model_id=model,
        default_temperature=_DEFAULT_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        system_prompt=load_verification_prompt(),
    )

    accumulated = ""
    try:
        async for event in agent.stream_async(render_verification_input(sow_markdown, upstream)):
            if event.get("data"):
                accumulated += event["data"]
    except Exception:
        logger.exception("model stream failed", extra={"model_id": model})
        return VerificationOutcome(
            status="failed", model_id=model, detail="AI reviewer call failed."
        )

    parsed = extract_json_object(accumulated)
    if not parsed:
        logger.warning("model returned no parseable JSON object", extra={"model_id": model})
        return VerificationOutcome(
            status="empty_output",
            model_id=model,
            detail="AI reviewer returned no structured output.",
        )
    return VerificationOutcome(status="ok", issues=_parse_issues(parsed), model_id=model)
