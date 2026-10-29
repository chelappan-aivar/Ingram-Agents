"""Service coverage validation for the cost estimate agent.

Two checks around the MCP ``build_estimate`` tool:

1. PRE-validation (``ServiceValidator``, a Strands hook): before ``build_estimate``
   runs, compare the services in the call with the services the user asked for.
   If any are missing the call is cancelled with a message that names them, so
   the agent adds the missing services and calls ``build_estimate`` again.

2. POST-validation (``post_validate``): after the calculator URL is produced,
   read the saved estimate back with ``import_estimate`` and log the result and
   a coverage summary to CloudWatch. This step is log-only; it never changes
   the response.

Both checks are fail-open: a validator error is logged and the estimate flow
continues, so validation can never break estimate generation.
"""

import json
from typing import Any, Optional

from pydantic import BaseModel, Field
from strands import Agent
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

# After this many rejected build_estimate calls the next one is let through, so a
# service that genuinely cannot be found cannot trap the agent in a loop.
MAX_PREFLIGHT_REJECTIONS = 2

# Cap for the raw import_estimate JSON written to a single CloudWatch log line.
IMPORT_LOG_MAX_CHARS = 60_000


def _log(message: str) -> None:
    """Write to stdout. This is the same channel the Strands tool-call output uses,
    so it reaches the AgentCore runtime log stream regardless of logging config."""
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Structured-output models
# ---------------------------------------------------------------------------

class ServiceRequirement(BaseModel):
    """One AWS service the user asked to have priced."""

    name: str = Field(description="AWS service as written in the request, e.g. 'Amazon RDS for MySQL' or 'AWS Batch'")
    environment: str = Field(
        default="",
        description="Environment the service belongs to (e.g. 'Production'). Empty if the request has a single environment.",
    )


class ServiceChecklist(BaseModel):
    services: list[ServiceRequirement] = Field(description="Every distinct service requested, one entry per service per environment")


class CoverageReport(BaseModel):
    missing_ids: list[int] = Field(
        default_factory=list,
        description="ids of REQUIRED services that have no matching provided entry. Empty list if everything is covered.",
    )


def _label(req: ServiceRequirement) -> str:
    return f"{req.name} ({req.environment})" if req.environment else req.name


# ---------------------------------------------------------------------------
# Checklist extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
List every distinct AWS service that the request below asks to be priced.

Rules:
- One entry per service, per environment. If the request defines several environments (for example Development, Quality, Production), repeat a service once for each environment it applies to. If there is a single environment, leave environment empty.
- Include every service, even minor or unusual ones. Do not merge services together and do not skip any.
- Use the service name as written in the request. Do not add services that were not requested.

Request:
{user_input}
"""


def extract_checklist(user_input: str, model: Any) -> list[ServiceRequirement]:
    """Return the services the user asked for. Returns [] (validation disabled) on failure."""
    try:
        extractor = Agent(model=model, callback_handler=None)
        result = extractor(_EXTRACT_PROMPT.format(user_input=user_input), structured_output_model=ServiceChecklist)
        checklist = result.structured_output.services
        _log(f"[VALIDATION] Checklist extracted: {len(checklist)} requested services -> {[_label(r) for r in checklist]}")
        return checklist
    except Exception as exc:
        _log(f"[VALIDATION] WARNING - could not extract service checklist, validation disabled for this request: {exc}")
        return []


# ---------------------------------------------------------------------------
# Coverage judge
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """\
You are checking an AWS cost estimate for completeness.

REQUIRED services (id, service, environment):
{required}

PROVIDED estimate entries (service key, name, group/environment, description):
{provided}

Return the ids of REQUIRED services that have NO matching provided entry.

Matching rules:
- A provided entry covers a required service when it prices that service (for example the key 'amazonRDSMySQLDB' covers 'RDS MySQL', 'awsFargate' covers 'ECS on Fargate').
- When the request has several environments, the entry must belong to the matching group/environment.
- An entry whose description explicitly says it models or stands in for a required service covers it (for example 'AWS Batch compute modeled with Fargate').
- A different service is NOT a match just because it is related.
- If every required service is covered, return an empty list.
"""


class CoverageJudge:
    """Decides which required services are not covered by a list of estimate entries."""

    def __init__(self, model: Any):
        self._model = model

    def missing(self, required: list[ServiceRequirement], provided: list[dict]) -> list[str]:
        """Return labels of required services that have no matching entry in ``provided``."""
        if not required:
            return []
        required_payload = [
            {"id": i, "service": r.name, "environment": r.environment} for i, r in enumerate(required)
        ]
        prompt = _JUDGE_PROMPT.format(
            required=json.dumps(required_payload, ensure_ascii=False),
            provided=json.dumps(provided, ensure_ascii=False),
        )
        judge = Agent(model=self._model, callback_handler=None)
        report = judge(prompt, structured_output_model=CoverageReport).structured_output
        return [_label(required[i]) for i in sorted(set(report.missing_ids)) if 0 <= i < len(required)]


# ---------------------------------------------------------------------------
# PRE-validation: Strands hook on build_estimate
# ---------------------------------------------------------------------------

def _parse_services_arg(raw: Any) -> list[dict]:
    """build_estimate takes its services as a JSON string (a list is accepted too)."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list):
        raise ValueError("services is not a JSON array")
    return [e for e in data if isinstance(e, dict)]


def _summarize_entry(entry: dict) -> dict:
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    return {
        "service": entry.get("service"),
        "group": entry.get("group"),
        "instance": entry.get("instance"),
        "description": config.get("description"),
    }


class ServiceValidator(HookProvider):
    """Cancels a build_estimate call when requested services are missing from it."""

    TOOL_NAME = "build_estimate"

    def __init__(self, checklist: list[ServiceRequirement], judge: CoverageJudge,
                 max_rejections: int = MAX_PREFLIGHT_REJECTIONS):
        self.checklist = checklist
        self.judge = judge
        self.max_rejections = max_rejections
        self.rejections = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)

    def _before_tool_call(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use.get("name") != self.TOOL_NAME or not self.checklist:
            return

        try:
            entries = _parse_services_arg(event.tool_use.get("input", {}).get("services"))
            missing = self.judge.missing(self.checklist, [_summarize_entry(e) for e in entries])
        except Exception as exc:
            _log(f"[PRE-VALIDATION] skipped, validator error (build_estimate allowed): {exc}")
            return

        if not missing:
            _log(f"[PRE-VALIDATION] OK - all {len(self.checklist)} requested services are present "
                 f"in build_estimate ({len(entries)} entries)")
            return

        names = ", ".join(missing)
        if self.rejections >= self.max_rejections:
            _log(f"[PRE-VALIDATION] WARNING - still missing after {self.rejections} rejections, "
                 f"allowing build_estimate anyway: {names}")
            return

        self.rejections += 1
        _log(f"[PRE-VALIDATION] BLOCKED build_estimate ({self.rejections}/{self.max_rejections}) - "
             f"{len(missing)} missing: {names}")
        event.cancel_tool = (
            f"build_estimate was NOT run. These requested services are missing from the services list: {names}. "
            "For each one, call search_services (try the full name, alternate names and abbreviations) and "
            "get_service_fields, build its entry, then call build_estimate again with the COMPLETE list: "
            "every service already included plus the missing ones. If a service truly has no entry in the "
            "calculator, include its closest billable equivalent and say so in that entry's description "
            "(for example 'AWS Batch compute modeled with Fargate')."
        )


# ---------------------------------------------------------------------------
# POST-validation: read the saved estimate back with import_estimate (log only)
# ---------------------------------------------------------------------------

def _result_text(result: Any) -> str:
    """Join the text parts of an MCP tool result."""
    parts = result.get("content", []) if isinstance(result, dict) else []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _saved_services(estimate: dict) -> list[dict]:
    """Flatten an imported estimate into one summary dict per saved service."""
    saved = []
    for group in (estimate.get("groups") or {}).values():
        group_name = group.get("name")
        for svc in (group.get("services") or {}).values():
            saved.append({
                "service": svc.get("serviceCode"),
                "name": svc.get("serviceName"),
                "group": group_name,
                "description": svc.get("description"),
            })
    for svc in (estimate.get("services") or {}).values():
        saved.append({
            "service": svc.get("serviceCode"),
            "name": svc.get("serviceName"),
            "group": None,
            "description": svc.get("description"),
        })
    return saved


def post_validate(mcp_client: Any, estimate_ref: Optional[str],
                  checklist: list[ServiceRequirement], judge: CoverageJudge) -> Optional[dict]:
    """Read the saved estimate back with import_estimate and log the result. Never raises.

    Returns the logged summary ({"saved", "requested", "missing"}) for the caller to show, or None when the check
    could not run. The return value is informational only; the estimate response is never changed by it."""
    try:
        if not estimate_ref:
            _log("[POST-VALIDATION] skipped - no calculator_url / estimate_id in the agent result")
            return None

        result = mcp_client.call_tool_sync(
            tool_use_id="post-validate-import",
            name="import_estimate",
            arguments={"estimate_id": estimate_ref},
        )
        text = _result_text(result)
        if result.get("status") == "error":
            _log(f"[POST-VALIDATION] import_estimate returned an error: {text[:500]}")
            return None

        try:
            estimate = json.loads(text)
        except json.JSONDecodeError:
            _log(f"[POST-VALIDATION] import_estimate did not return JSON: {text[:500]}")
            return None

        compact = json.dumps(estimate, separators=(",", ":"), ensure_ascii=False)
        if len(compact) > IMPORT_LOG_MAX_CHARS:
            compact = compact[:IMPORT_LOG_MAX_CHARS] + f"...[truncated, {len(compact)} chars total]"
        _log(f"[POST-VALIDATION] import_estimate result ({len(text)} chars): {compact}")

        saved = _saved_services(estimate)
        summary = f"[POST-VALIDATION] saved services: {len(saved)}"
        coverage = {"saved": len(saved), "requested": len(checklist) if checklist else None, "missing": None}
        if checklist:
            missing = judge.missing(checklist, saved)
            coverage["missing"] = missing
            summary += f" | requested: {len(checklist)} | missing: {len(missing)}"
            if missing:
                summary += f" -> {', '.join(missing)}"
        else:
            summary += " | requested: unknown (checklist unavailable)"
        _log(summary)
        _log("[POST-VALIDATION] saved: " + ", ".join(
            f"{s['name'] or s['service']}" + (f" [{s['group']}]" if s["group"] else "") for s in saved
        ))
        return coverage
    except Exception as exc:
        _log(f"[POST-VALIDATION] failed (response unaffected): {exc}")
        return None
