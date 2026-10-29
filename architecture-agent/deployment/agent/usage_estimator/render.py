"""Deterministic renderers: Estimate -> usage.json (the exact cost-calculator schema) and usage_assumptions.md.

usage.json holds nothing but the schema in usage_estimator/expectedOutput.md: every group, in schema order, each a
list of entries `{"service": ..., <fields in schema order>}`. Everything else (warnings, the workload profile, how
each figure was reached, the model's notes) goes to usage_assumptions.md, which is written for people.
"""
from __future__ import annotations

from adr_generator.availability import AvailabilityPolicy
from adr_generator.contracts import Design
from adr_generator.render.adr_md import NONE, _cell, _table, _txt
from usage_estimator.registry import GROUPS, HOURS_PER_MONTH, SERVICES
from usage_estimator.rules import Entry, Estimate


def _ordered(est: Estimate) -> list[Entry]:
    """Registry order of services, then the model's order within a service."""
    rank = {s.name: i for i, s in enumerate(SERVICES)}
    return sorted(est.entries, key=lambda e: rank[e.service.name])


def build_usage(est: Estimate) -> dict:
    services: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for e in _ordered(est):
        services[e.service.group].append({"service": e.service.name, **e.values})
    return {"services": services}


def _fmt(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, list):
        return ", ".join(v) or "none"
    return str(v)


def render_assumptions(est: Estimate, design: Design, *, source_name: str, run_date: str, model_id: str) -> str:
    names = {c.id: _txt(c.name) for c in design.components}
    region = design.region or "Not specified"
    policy = AvailabilityPolicy.from_design(design)
    lines = [f"# Usage estimate: {_txt(design.title) or 'Untitled design'}", ""]
    lines += _table(["Field", "Value"], [
        ["Source document", _cell(source_name)],
        ["Date", run_date],
        ["Model", _cell(model_id)],
        ["Estimate", "One expected month (no ranges)"],
        ["Month length", f"{HOURS_PER_MONTH} hours"],
        ["Deployment", _cell(policy.deployment_summary(design.region, design.availability_zones))],
        ["Usage entries", str(len(est.entries))],
        ["Warnings", str(len(est.warnings))],
    ])

    lines += ["", "## 1. Warnings", ""]
    lines += [f"- {_txt(w)}" for w in est.warnings] or [NONE]

    lines += ["", "## 2. Workload profile", "",
              "Every usage figure below is derived from these drivers. Basis: stated (in the SRS), inferred "
              "(calculated from stated numbers) or assumed (the SRS is silent).", ""]
    if est.profile:
        lines += _table(["Driver", "Value", "Basis", "Reason"],
                        [[_cell(d["name"]), _cell(f"{d['value']} {d['unit']}".strip()), _cell(d["basis"]),
                          _cell(d["reason"])] for d in est.profile])
    else:
        lines.append(NONE)

    lines += ["", "## 3. Usage by component", ""]
    if est.entries:
        rows = []
        for e in _ordered(est):
            comp = f"{e.component_id} {names.get(e.component_id, '')}".strip()
            figures = "; ".join(f"{k}: {_fmt(v)}" for k, v in e.values.items())
            rows.append([_cell(comp), _cell(e.service.name), _cell(figures), _cell(e.explanation),
                         _cell(", ".join(e.assumed_fields) or "none")])
        lines += _table(["Component", "Service", "Usage figures", "How the figures were reached", "Assumed fields"],
                        rows)
    else:
        lines.append(NONE)

    lines += ["", "## 4. Notes", ""]
    lines += [f"- {_txt(n)}" for n in est.notes] or [NONE]

    lines += ["", "## 5. Method", "",
              "- Configuration fields (instance class, engine, memory, storage size, counts) are copied from the "
              "ADR design; usage fields are estimated from the workload profile by one model call.",
              f"- One expected steady-state month of {HOURS_PER_MONTH} hours. Prices, free tier and cost are left "
              "to the cost calculator.",
              policy.report_method_line(),
              "- Components added later by the diagram's Assumer are not included; usage.json covers the ADR "
              "design only.", ""]
    return "\n".join(lines)
