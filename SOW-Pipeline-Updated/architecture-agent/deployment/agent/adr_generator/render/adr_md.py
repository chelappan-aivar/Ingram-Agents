"""Deterministic renderer: Design -> adr.md (format defined in HANDOFF.md).

Plain Markdown only: headings, GitHub tables, lists, bold. No front matter, raw HTML or code fences. The network
layout is written as plain sentences generated from boundaries and component placement, because a downstream
Extractor LLM reads this file to build a diagram. Never raises on a valid Design.
"""
from __future__ import annotations

from adr_generator.contracts import Boundary, Component, Design

NOT_SPECIFIED = "Not specified"
NONE = "None."
NON_AWS = ("actor", "external")          # categories that live outside AWS
OUTSIDE_TYPES = ("external", "on_prem")  # boundary types that live outside AWS


# ---------------------------------------------------------------- helpers

def _txt(s: str) -> str:
    """Single-line text: collapse all whitespace (incl. newlines)."""
    return " ".join((s or "").split())


def _cell(s: str) -> str:
    """Table cell: one line, `|` escaped, empty -> Not specified."""
    t = _txt(s).replace("|", "\\|")
    return t or NOT_SPECIFIED


def _val(s: str) -> str:
    return _txt(s) or NOT_SPECIFIED


def _join(items: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _comp_name(c: Component) -> str:
    name = _txt(c.name) or _txt(c.id) or NOT_SPECIFIED
    return name + (" (assumed)" if c.assumed else "")


def _sku(c: Component) -> str:
    if _txt(c.sku_summary):
        return _txt(c.sku_summary)
    if c.configuration:
        return "; ".join(f"{_txt(k)}: {_txt(v)}" for k, v in c.configuration.items())
    return NOT_SPECIFIED


def _bname(b: Boundary) -> str:
    return _txt(b.name) or _txt(b.id) or NOT_SPECIFIED


def _outside(d: Design, c: Component) -> bool:
    b = d.boundary(c.boundary)
    return c.category in NON_AWS or (b is not None and b.type in OUTSIDE_TYPES)


def _placement(d: Design, c: Component) -> str:
    b = d.boundary(c.boundary)
    if b is not None:
        return _bname(b)
    if c.boundary:                       # id that matches no boundary: show it raw
        return _txt(c.boundary)
    return "External" if c.category in NON_AWS else "Region"


def _chain(d: Design, b: Boundary) -> list[Boundary]:
    """Ancestors of b, nearest first (cycle-safe)."""
    out, seen = [], {b.id}
    cur = d.boundary(b.parent)
    while cur is not None and cur.id not in seen:
        out.append(cur)
        seen.add(cur.id)
        cur = d.boundary(cur.parent)
    return out


# ---------------------------------------------------------------- sections

def _network_layout(d: Design) -> list[str]:
    sents: list[str] = []
    region = _txt(d.region)
    vpcs = [b for b in d.boundaries if b.type == "vpc"]
    azs = [_bname(b) for b in d.boundaries if b.type == "az"] or [_txt(z) for z in d.availability_zones if _txt(z)]

    sents.append(f"The solution runs in AWS Region {region}." if region
                 else "The AWS Region is not specified.")
    if vpcs:
        n = len(vpcs)
        lead = "one VPC" if n == 1 else f"{n} VPCs"
        where = ""
        if len(azs) == 1:
            where = f", in a single Availability Zone, {azs[0]}"
        elif len(azs) > 1:
            where = f", across {len(azs)} Availability Zones, {_join(azs)}"
        sents.append(f"It uses {lead}, {_join([_bname(b) for b in vpcs])}{where}.")
    elif azs:
        sents.append(f"It uses the Availability Zone {_join(azs)}.")
    else:
        sents.append("No VPC is specified.")

    # Components grouped by innermost boundary, in boundary order (AWS-side boundaries only).
    by_b: dict[str, list[Component]] = {}
    for c in d.components:
        if c.boundary and not _outside(d, c) and d.boundary(c.boundary) is not None \
                and d.boundary(c.boundary).type != "region":
            by_b.setdefault(c.boundary, []).append(c)
    for b in d.boundaries:
        comps = by_b.get(b.id)
        if not comps:
            continue
        anc = [_bname(a) for a in _chain(d, b) if a.type not in ("region", "az")]
        label = f"The {_bname(b)}" + (f", in {' / '.join(anc)}," if anc else "")
        names = [f"the {_txt(c.name) or _txt(c.id) or NOT_SPECIFIED}" for c in comps]
        sents.append(f"{label} contains {_join(names)}.")

    # Region-level services: no boundary (or the region boundary, or an unknown id), not actor/external.
    region_comps = [c for c in d.components if not _outside(d, c) and (
        not c.boundary or d.boundary(c.boundary) is None or d.boundary(c.boundary).type == "region")]
    if region_comps:
        sents.append("Region-level services used: "
                     + _join([_txt(c.name) or _txt(c.id) or NOT_SPECIFIED for c in region_comps]) + ".")

    outside = [c for c in d.components if _outside(d, c)]
    if outside:
        sents.append("Outside AWS, the solution involves: "
                     + _join([_txt(c.name) or _txt(c.id) or NOT_SPECIFIED for c in outside]) + ".")
    return [" ".join(sents)]


def _flow_sentence(d: Design, r) -> str:  # type: ignore[no-untyped-def]
    s = _txt(r.sentence)
    if not s:
        src, tgt = d.component(r.source), d.component(r.target)
        sn = _txt(src.name) if src and _txt(src.name) else (_txt(r.source) or NOT_SPECIFIED)
        tn = _txt(tgt.name) if tgt and _txt(tgt.name) else (_txt(r.target) or NOT_SPECIFIED)
        s = f"{sn} {_txt(r.label) or 'connects to'} {tn}."
    suffix = [p for p in (_txt(r.id), " ".join(p for p in (_txt(r.protocol), _txt(r.port)) if p)) if p]
    return f"{s} ({'; '.join(suffix)})" if suffix else s


def render_adr(design: Design, *, source_name: str, run_date: str) -> str:
    d = design
    L: list[str] = []
    L.append(f"# Architecture Decision Record: {_val(d.title)}")
    L.append("")
    L += _table(["Field", "Value"], [
        ["Customer", _cell(d.customer)], ["Status", "Proposed"], ["Date", _cell(run_date)],
        ["Region", _cell(d.region)],
        ["Availability Zones", _cell(", ".join(d.availability_zones))],
        ["Source document", _cell(source_name)], ["Components", str(len(d.components))]])

    L += ["", "## 1. Context", "", _val(d.context)]

    L += ["", "## 2. Requirements addressed", ""]
    if d.requirements:
        L += _table(["ID", "Requirement", "Type"],
                    [[_cell(r.id), _cell(r.text), _cell(r.kind)] for r in d.requirements])
    else:
        L.append(NONE)

    L += ["", "## 3. Architecture decisions"]
    if not d.decisions:
        L += ["", NONE]
    for x in d.decisions:
        L += ["", f"### {_val(x.id)}: {_val(x.title)}", "",
              f"**Status:** {_val(x.status)}", "", f"**Context:** {_val(x.context)}", "",
              f"**Decision:** {_val(x.decision)}", "", "**Consequences:**"]
        L += [f"- {_txt(c)}" for c in x.consequences if _txt(c)] or [f"- {NONE}"]
        L += ["", f"**Requirements:** {', '.join(_txt(i) for i in x.requirement_ids) or NOT_SPECIFIED}", ""]
        alts = [f"{_val(a.option)} ({_val(a.reason)})" if _txt(a.reason) else _val(a.option)
                for a in x.rejected_alternatives]
        L.append("**Alternatives considered (rejected, not part of this design):** "
                 + ("; ".join(alts) if alts else "None recorded"))

    L += ["", "## 4. Network layout", ""]
    L += _network_layout(d)

    L += ["", "## 5. Components", ""]
    if d.components:
        L += _table(["ID", "Component", "AWS service", "SKU / configuration", "Qty", "Placement", "Purpose"],
                    [[_cell(c.id), _cell(_comp_name(c)), _cell(c.service), _cell(_sku(c)), str(c.quantity),
                      _cell(_placement(d, c)), _cell(c.purpose or c.role)] for c in d.components])
    else:
        L.append(NONE)

    L += ["", "## 6. Request and data flow", ""]
    if d.relationships:
        L += [f"{i}. {_flow_sentence(d, r)}" for i, r in enumerate(d.relationships, 1)]
    else:
        L.append(NONE)

    L += ["", "## 7. Security and operations", ""]
    L += [f"- {_txt(s)}" for s in d.security_operations if _txt(s)] or [NONE]

    L += ["", "## 8. Assumptions", ""]
    rows = [[_cell(a.id), _cell(a.text), _cell(a.reason)] for a in d.assumptions]
    covered = {_txt(a.text).lower() for a in d.assumptions}
    for c in d.components:
        if not c.assumed:
            continue
        text = f"{_txt(c.name) or _txt(c.id) or NOT_SPECIFIED}: {_sku(c)}"
        if text.lower() in covered or any(_txt(c.id) and _txt(c.id) == _txt(a.id) for a in d.assumptions):
            continue
        rows.append([_cell(c.id), _cell(text), _cell(c.assumption_reason)])
    if rows:
        L += _table(["ID", "Assumption", "Reason"], rows)
    else:
        L.append(NONE)

    L += ["", "## 9. Open questions", ""]
    L += [f"- {_txt(q)}" for q in d.open_questions if _txt(q)] or [NONE]

    return "\n".join(L) + "\n"
