"""The data-driven compliance rule set: models, loading and strict validation.

The rules live in `resources/policies/sow_compliance_rules.yaml`; the engine interprets only the
closed set of `rule.type` primitives below. No code from the config file is ever executed.
"""

from collections.abc import Callable
from functools import cache
from typing import Final, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.modules.sow_generator.repositories.resource_loader import read_resource_text

RULES_RESOURCE: Final = "sow_compliance_rules.yaml"

RULE_TYPES = frozenset(
    {
        "section_present",
        "numeric_compare",
        "upstream_numeric_compare",
        "upstream_link_in_sow",
        "services_coverage",
        "timeline_match",
    }
)


COMPARE_OPS: Final[dict[str, Callable[[float, float], bool]]] = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
}


class Rule(BaseModel):
    type: str
    sections: list[str] = Field(default_factory=list)
    label: str | None = None
    op: str | None = None
    value: float | None = None
    unit: str | None = None
    source: str | None = None
    # Dotted path into UpstreamInputs for upstream-sourced rules,
    # e.g. "cost.monthly_total".
    field: str | None = None


class ComplianceRule(BaseModel):
    id: str
    name: str
    description: str = ""
    severity: Literal["required", "advisory"]
    rule: Rule


class RulesConfig(BaseModel):
    version: int = 1
    rules: list[ComplianceRule] = Field(default_factory=list)


class RulesConfigError(RuntimeError):
    """Raised when the compliance rules config is missing or structurally invalid."""


@cache
def load_rules() -> list[ComplianceRule]:
    """Load, validate, and cache the SOW compliance rule set.

    Validation is strict and fails loudly at load time so a broken rules file
    cannot silently disable the compliance gate.
    """
    try:
        text = read_resource_text("policies", RULES_RESOURCE)
    except OSError as exc:
        raise RulesConfigError(f"Compliance rules config not found: {RULES_RESOURCE}") from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RulesConfigError(f"Compliance rules config is not valid YAML: {exc}") from exc
    try:
        config = RulesConfig.model_validate(raw)
    except ValidationError as exc:
        raise RulesConfigError(f"Compliance rules config is structurally invalid: {exc}") from exc

    validate_rules(config.rules)
    return config.rules


def validate_rules(rules: list[ComplianceRule]) -> None:
    """Enforce invariants the Pydantic schema alone cannot."""
    seen: set[str] = set()
    for r in rules:
        if r.id in seen:
            raise RulesConfigError(f"Duplicate rule id: {r.id!r}")
        seen.add(r.id)

        rt = r.rule.type
        if rt not in RULE_TYPES:
            raise RulesConfigError(f"Rule {r.id!r}: unknown rule type {rt!r}")
        if rt == "section_present" and not r.rule.sections:
            raise RulesConfigError(
                f"Rule {r.id!r}: 'section_present' requires non-empty 'sections'"
            )
        if rt == "numeric_compare":
            if not r.rule.label:
                raise RulesConfigError(f"Rule {r.id!r}: 'numeric_compare' requires 'label'")
            if r.rule.op not in COMPARE_OPS:
                raise RulesConfigError(
                    f"Rule {r.id!r}: 'numeric_compare' requires op in {sorted(COMPARE_OPS)}"
                )
            if r.rule.value is None:
                raise RulesConfigError(f"Rule {r.id!r}: 'numeric_compare' requires 'value'")
        if rt == "upstream_numeric_compare":
            if not r.rule.field:
                raise RulesConfigError(
                    f"Rule {r.id!r}: 'upstream_numeric_compare' requires 'field' "
                    "(dotted upstream path)"
                )
            if r.rule.op not in COMPARE_OPS:
                raise RulesConfigError(
                    f"Rule {r.id!r}: 'upstream_numeric_compare' "
                    f"requires op in {sorted(COMPARE_OPS)}"
                )
            if r.rule.value is None:
                raise RulesConfigError(
                    f"Rule {r.id!r}: 'upstream_numeric_compare' requires 'value'"
                )
        if rt == "upstream_link_in_sow" and r.rule.source != "cost":
            raise RulesConfigError(
                f"Rule {r.id!r}: 'upstream_link_in_sow' supports only source 'cost'"
            )
