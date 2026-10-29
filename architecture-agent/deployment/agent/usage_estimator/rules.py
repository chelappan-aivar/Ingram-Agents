"""Code rules applied to the model's `submit_usage` answer (no LLM). Never raises on any tool input.

  - keeps only services in the registry, every field in schema order, each coerced to its schema type;
    a missing or unreadable value becomes null with a warning, a negative number becomes 0 with a warning
  - sets the availability fields (multi_az, availability_zones) from the design's own availability model,
    so what is priced matches what was designed and drawn, and computes memory_gb from memory_mb
  - checks coverage: every AWS component of the design has an entry, or a warning says why it has none
  - a few consistency checks between figures, reported as warnings (values are never changed by them)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from adr_generator.availability import AvailabilityPolicy
from adr_generator.contracts import Design
from usage_estimator.estimator import NO_USAGE_CATEGORIES
from usage_estimator.registry import AZ_FIELDS, BY_KEY, BY_NAME, Service

MAX_INPUT_TOKENS_PER_CALL = 200_000


@dataclass
class Entry:
    service: Service
    component_id: str
    values: dict[str, Any]              # schema fields, in schema order
    explanation: str = ""
    assumed_fields: list[str] = field(default_factory=list)


@dataclass
class Estimate:
    profile: list[dict[str, str]] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)    # the model's warnings first, then the code checks
    notes: list[str] = field(default_factory=list)


def _str(v: Any) -> str:
    return "" if v is None else " ".join(str(v).split())


def _strs(v: Any) -> list[str]:
    if isinstance(v, str):
        return [_str(v)] if v.strip() else []
    return [_str(x) for x in v if _str(x)] if isinstance(v, list) else []


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").replace("_", "").strip())
        except ValueError:
            return None
    return None


def _coerce(kind: str, v: Any) -> Any:
    """The value as the schema type, or None when it cannot be read."""
    if v is None:
        return None
    if kind in ("integer", "number"):
        n = _number(v)
        if n is None:
            return None
        if kind == "integer" or n.is_integer():
            return int(round(n))
        return round(n, 3)
    if kind == "boolean":
        if isinstance(v, bool):
            return v
        s = _str(v).lower()
        return True if s in ("true", "yes", "1", "on", "enabled") else False if s in ("false", "no", "0", "off",
                                                                                      "disabled") else None
    if kind == "list":
        return _strs(v)
    return _str(v) or None


def _entry(svc: Service, item: dict, warnings: list[str], az_fields: dict[str, Any]) -> Entry:
    cid = _str(item.get("component_id")) or "?"
    values: dict[str, Any] = {}
    for f in svc.fields:
        if f.name in AZ_FIELDS:
            values[f.name] = az_fields[f.name]
            continue
        if f.name == "memory_gb":
            mb = _number(item.get("memory_mb"))
            values[f.name] = None if mb is None else _coerce("number", mb / 1024)
            continue
        value = _coerce(f.type, item.get(f.name))
        if value is None:
            warnings.append(f"{cid} {svc.name}: {f.name} is missing; it is left empty (null) in usage.json.")
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            warnings.append(f"{cid} {svc.name}: {f.name} was negative ({value}); it is set to 0.")
            value = 0
        values[f.name] = value
    return Entry(svc, cid, values, _str(item.get("explanation")),
                 [a for a in _strs(item.get("assumed_fields")) if a in values])


def _consistency(entries: list[Entry]) -> list[str]:
    out = []
    for e in entries:
        v, who = e.values, f"{e.component_id} {e.service.name}"
        if e.service.name == "AWS Step Functions":
            ex, st, tr = (v.get(k) or 0 for k in ("executions_per_month", "average_states_per_execution",
                                                  "state_transitions_per_month"))
            if ex and st and abs(tr - ex * st) > 0.1 * ex * st:
                out.append(f"{who}: state_transitions_per_month ({tr}) does not match executions x states "
                           f"({ex} x {st} = {ex * st}).")
        if e.service.name == "Amazon Bedrock":
            calls, tokens = v.get("invocations_per_month") or 0, v.get("input_tokens_per_month") or 0
            if tokens and not calls:
                out.append(f"{who}: input tokens are given but invocations_per_month is 0.")
            elif calls and tokens / calls > MAX_INPUT_TOKENS_PER_CALL:
                out.append(f"{who}: {tokens // calls} input tokens per call is above the model's context window; "
                           f"check the token figures.")
    return out


def apply(raw: Any, design: Design) -> Estimate:
    raw = raw if isinstance(raw, dict) else {}
    est = Estimate(warnings=_strs(raw.get("warnings")), notes=_strs(raw.get("notes")))
    checks: list[str] = []
    # The design decides the availability model; the estimate reports it. Reading it off the
    # design (not a constant) is what keeps the diagram, usage.json and the price in agreement.
    az_fields = AvailabilityPolicy.from_design(design).usage_fields()

    for d in raw.get("workload_profile") or []:
        if isinstance(d, dict) and _str(d.get("name")):
            est.profile.append({k: _str(d.get(k)) for k in ("name", "value", "unit", "basis", "reason")})

    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    for key, items in usage.items():
        svc = BY_KEY.get(key) or BY_NAME.get(key)
        if svc is None:
            checks.append(f"The model answered for '{key}', which is not in the cost-calculator schema; it is "
                          f"left out of usage.json.")
            continue
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                est.entries.append(_entry(svc, item, checks, az_fields))

    by_id = {c.id: c for c in design.components}
    covered = {e.component_id for e in est.entries}
    for e in est.entries:
        if e.component_id not in by_id:
            checks.append(f"{e.service.name}: the entry names component '{e.component_id}', which is not in the "
                          f"design.")
    for c in design.components:
        if c.category in NO_USAGE_CATEGORIES or c.id in covered:
            continue
        if not any(c.id in w for w in est.warnings):
            label = c.name if c.name == c.service else f"{c.name} ({c.service})"
            checks.append(f"{c.id} {label} has no usage entry, so it is not in usage.json; check whether its "
                          f"service is in the cost-calculator schema.")
    seen: set[tuple[str, str]] = set()
    for e in est.entries:
        pair = (e.component_id, e.service.name)
        if pair in seen and e.service.name != "Amazon Bedrock":
            checks.append(f"{e.component_id} has more than one {e.service.name} entry; each one is priced.")
        seen.add(pair)

    est.warnings += checks + _consistency(est.entries)
    return est
