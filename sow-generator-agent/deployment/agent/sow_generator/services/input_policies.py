"""Deterministic, LLM-free company-policy completeness check.

The Step 2 completeness check verifies a single, complete ``UpstreamInputs``
payload against a set of company policies declared in
``resources/policies/company_policies.yaml``. Each policy is evaluated to a
``pass`` / ``fail`` / ``not_applicable`` status and returned as an ordered list
so the UI can show each one by one.

Design notes
------------
* The policy *set* is data-driven (YAML), but the engine interprets only a
  fixed, closed set of ``rule.type`` primitives. No code from the config file is
  ever executed — a malformed or hostile file can at worst be rejected at load.
* ``can_generate`` is ``True`` only when no ``required`` policy fails. ``advisory``
  policies are reported but never block generation.
"""

from functools import cache
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from sow_generator.errors import TemplateError
from sow_generator.repositories.resource_loader import read_resource_text
from sow_generator.repositories.template_repository import load_template
from sow_generator.schemas import (
    PolicyCheckSummary,
    PolicyResult,
    UpstreamInputs,
    ValidateInputsResponse,
)
from sow_generator.schemas.policies import PolicyStatus

POLICIES_RESOURCE: Final = "company_policies.yaml"

# Tolerance for the monthly x 12 vs annual reconciliation (max of $1 or 1%).
_COST_TOLERANCE_ABS = 1.0
_COST_TOLERANCE_PCT = 0.01

# The closed set of rule primitives the engine understands.
_RULE_TYPES = frozenset(
    {
        "field_present",
        "any_present",
        "non_empty_list",
        "template_resolves",
        "cost_reconciled",
        "branch_source_consistent",
    }
)


# ─── Policy config model ─────────────────────────────────────────────────────


class _Rule(BaseModel):
    type: str
    field: str | None = None
    fields: list[str] = Field(default_factory=list)


class _Policy(BaseModel):
    id: str
    name: str
    description: str = ""
    severity: Literal["required", "advisory"]
    rule: _Rule


class _PolicyConfig(BaseModel):
    version: int = 1
    policies: list[_Policy] = Field(default_factory=list)


class PolicyConfigError(RuntimeError):
    """Raised when the policy config file is missing or structurally invalid."""


@cache
def load_policies() -> list[_Policy]:
    """Load, validate, and cache the company policy set.

    Validation is strict and fails loudly at load time (not mid-request) so a
    broken policy file cannot silently disable the completeness gate.
    """
    try:
        text = read_resource_text("policies", POLICIES_RESOURCE)
    except OSError as exc:
        raise PolicyConfigError(f"Policy config not found: {POLICIES_RESOURCE}") from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise PolicyConfigError(f"Policy config is not valid YAML: {exc}") from exc
    try:
        config = _PolicyConfig.model_validate(raw)
    except ValidationError as exc:
        raise PolicyConfigError(f"Policy config is structurally invalid: {exc}") from exc

    _validate_policies(config.policies)
    return config.policies


def _validate_policies(policies: list[_Policy]) -> None:
    """Enforce invariants the Pydantic schema alone cannot: unique ids, known
    rule types, required rule arguments, and valid severities."""
    seen: set[str] = set()
    for p in policies:
        if p.id in seen:
            raise PolicyConfigError(f"Duplicate policy id: {p.id!r}")
        seen.add(p.id)

        rt = p.rule.type
        if rt not in _RULE_TYPES:
            raise PolicyConfigError(f"Policy {p.id!r}: unknown rule type {rt!r}")
        if rt in ("field_present", "non_empty_list") and not p.rule.field:
            raise PolicyConfigError(f"Policy {p.id!r}: rule '{rt}' requires 'field'")
        if rt == "any_present" and not p.rule.fields:
            raise PolicyConfigError(
                f"Policy {p.id!r}: rule 'any_present' requires non-empty 'fields'"
            )


# ─── Public entry point ──────────────────────────────────────────────────────


def check_completeness(upstream: UpstreamInputs) -> ValidateInputsResponse:
    """Verify the inputs against every company policy, in declared order."""
    policies = load_policies()
    results: list[PolicyResult] = [_evaluate(p, upstream) for p in policies]

    can_generate = not any(r.severity == "required" and r.status == "fail" for r in results)
    summary = PolicyCheckSummary(
        total=len(results),
        passed=sum(1 for r in results if r.status == "pass"),
        failed=sum(1 for r in results if r.status == "fail"),
        advisory=sum(1 for r in results if r.severity == "advisory" and r.status != "pass"),
    )
    return ValidateInputsResponse(can_generate=can_generate, results=results, summary=summary)


# ─── Rule evaluation ─────────────────────────────────────────────────────────


def _evaluate(policy: _Policy, upstream: UpstreamInputs) -> PolicyResult:
    status, detail = _apply_rule(policy.rule, upstream)
    return PolicyResult(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        severity=policy.severity,
        status=status,
        detail=detail,
    )


def _apply_rule(rule: _Rule, upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    """Return (status, detail) for a single rule. Status is pass/fail/not_applicable."""
    rt = rule.type
    if rt == "field_present":
        return _rule_field_present(rule.field or "", upstream)
    if rt == "any_present":
        return _rule_any_present(rule.fields, upstream)
    if rt == "non_empty_list":
        return _rule_non_empty_list(rule.field or "", upstream)
    if rt == "template_resolves":
        return _rule_template_resolves(upstream)
    if rt == "cost_reconciled":
        return _rule_cost_reconciled(upstream)
    if rt == "branch_source_consistent":
        return _rule_branch_source_consistent(upstream)
    # Unreachable: load_policies() rejects unknown types.
    return "not_applicable", f"Unsupported rule type {rt!r}."


def _rule_field_present(field: str, upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    value = _resolve_path(upstream, field)
    return ("pass", "") if _is_present(value) else ("fail", f"'{field}' is missing or blank.")


def _rule_any_present(fields: list[str], upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    if any(_is_present(_resolve_path(upstream, f)) for f in fields):
        return "pass", ""
    joined = ", ".join(f"'{f}'" for f in fields)
    return "fail", f"Provide at least one of: {joined}."


def _rule_non_empty_list(field: str, upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    value = _resolve_path(upstream, field)
    if isinstance(value, (list, tuple)) and len(value) > 0:
        return "pass", ""
    return "fail", f"'{field}' must contain at least one item."


def _rule_template_resolves(upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    template_id = (upstream.meta.template_id or "").strip()
    if not template_id:
        return "fail", "A template id (e.g. 'aiml/ai-ml') is required."
    try:
        load_template(template_id)
    except TemplateError:
        return (
            "fail",
            f"Template '{template_id}' is not recognised. Use a known '<category>/<name>' id.",
        )
    return "pass", ""


def _rule_cost_reconciled(upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    cost = upstream.cost
    if cost is None:
        return "not_applicable", "No cost block supplied."

    monthly = cost.monthly_total
    annual = cost.annual_total

    if monthly is not None and annual is not None:
        expected = monthly * 12
        tolerance = max(_COST_TOLERANCE_ABS, _COST_TOLERANCE_PCT * abs(expected))
        if abs(expected - annual) > tolerance:
            return (
                "fail",
                f"Annual total {annual} does not match monthly {monthly} \u00d7 12 ({expected}).",
            )

    line_monthlies = [li.monthly for li in (cost.line_items or []) if li.monthly is not None]
    if monthly is not None and line_monthlies:
        line_sum = sum(line_monthlies)
        tolerance = max(_COST_TOLERANCE_ABS, _COST_TOLERANCE_PCT * abs(monthly))
        if abs(line_sum - monthly) > tolerance:
            return (
                "fail",
                f"Line-item monthly sum ({line_sum}) does not match monthly_total ({monthly}).",
            )

    return "pass", ""


def _rule_branch_source_consistent(upstream: UpstreamInputs) -> tuple[PolicyStatus, str]:
    for name, branch, expected in (
        ("discovery", upstream.discovery, "discovery"),
        ("live_assist", upstream.live_assist, "live_assist"),
    ):
        if branch is not None and branch.source != expected:
            return (
                "fail",
                f"{name} block carries source '{branch.source}', expected '{expected}'.",
            )
    return "pass", ""


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_path(root: Any, dotted: str) -> Any:
    """Resolve a dotted attribute path (e.g. 'intake.budget_range') on the
    Pydantic model. Returns None if any segment is missing."""
    current: Any = root
    for segment in dotted.split("."):
        if current is None:
            return None
        current = getattr(current, segment, None)
    return current


def _is_present(value: Any) -> bool:
    """Treat None, blank/whitespace strings, and empty collections as absent."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True
