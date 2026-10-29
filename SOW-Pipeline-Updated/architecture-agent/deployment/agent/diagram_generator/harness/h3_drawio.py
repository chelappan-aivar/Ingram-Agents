"""Harness 3: does the draw.io XML cover the FinalSpec? (see ../../ADR.md and INTERFACES.md, "WP4")

Rules:
- every spec component id must exist as a cell id (errors otherwise)
- every spec relationship must exist as an edge, direction-insensitive (errors otherwise; reversed = warning)
- edges with a missing/unknown endpoint are errors; validator errors are errors ("validator: " prefix)
- boundary cells absent = warning; extra AWS-icon vertices that are not in the spec = warning
- ok = no errors
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import defusedxml.ElementTree as ET

from diagram_generator.contracts import FinalSpec, HarnessRecord

# Cells the coder adds around a spec component <id>: category container, inner icon, visible label.
DECOR_SUFFIXES = ("__box", "__icon", "__label")


@dataclass
class DrawioFacts:
    vertices: dict[str, dict] = field(default_factory=dict)     # id -> {value, style, parent}
    edges: list[tuple] = field(default_factory=list)            # (id, source, target, label)
    parse_error: Optional[str] = None

    @property
    def cell_ids(self) -> set[str]:
        return set(self.vertices) | {e[0] for e in self.edges}


# ----------------------------------------------------------------------------- parsing
def parse_drawio(xml_text: str) -> DrawioFacts:
    """Parse draw.io XML into vertices and edges. Never raises; parse problems go in .parse_error."""
    facts = DrawioFacts()
    try:
        root = ET.fromstring(xml_text.strip().encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except Exception as e:  # ParseError, DefusedXmlException, ...
        facts.parse_error = f"XML parse error: {e}"
        return facts

    # <object>/<UserObject> carry id and label around an inner mxCell
    wrapped: dict[int, tuple[str, str]] = {}
    for el in root.iter():
        if el.tag in ("object", "UserObject"):
            for child in el:
                if child.tag == "mxCell":
                    wrapped[id(child)] = (el.get("id", ""), el.get("label", el.get("value", "")))

    raw_edges: list[tuple[str, str, str, str]] = []
    for el in root.iter("mxCell"):
        if id(el) in wrapped:
            cid, value = wrapped[id(el)]
        else:
            cid, value = el.get("id", ""), el.get("value", "")
        if not cid:
            continue
        style = el.get("style", "")
        parent = el.get("parent", "")
        if el.get("edge") == "1":
            raw_edges.append((cid, el.get("source", ""), el.get("target", ""), value))
        elif el.get("vertex") == "1":
            facts.vertices[cid] = {"value": value, "style": style, "parent": parent}

    # Edge label: the edge's own value, else the text of a child cell styled edgeLabel
    child_labels: dict[str, str] = {}
    for cid, v in facts.vertices.items():
        if "edgeLabel" in v["style"] and v["value"]:
            child_labels.setdefault(v["parent"], v["value"])
    for cid, src, tgt, value in raw_edges:
        facts.edges.append((cid, src, tgt, value or child_labels.get(cid, "")))
    return facts


# ----------------------------------------------------------------------------- resolution helpers
def _decor_base(cell_id: str, spec_ids: set[str]) -> Optional[str]:
    for suf in DECOR_SUFFIXES:
        if cell_id.endswith(suf):
            base = cell_id[: -len(suf)]
            if base in spec_ids:
                return base
    return None


def _resolver(facts: DrawioFacts, spec_ids: set[str], boundary_ids: set[str]):
    children: dict[str, list[str]] = {}
    for cid, v in facts.vertices.items():
        children.setdefault(v["parent"], []).append(cid)

    def own_spec(cid: str) -> Optional[str]:
        if cid in spec_ids:
            return cid
        return _decor_base(cid, spec_ids)

    def resolve(endpoint: str) -> set[str]:
        direct = own_spec(endpoint)
        if direct:
            return {direct}
        cell = facts.vertices.get(endpoint)
        if cell is not None:
            parent_spec = own_spec(cell["parent"])      # endpoint is a child of X
            if parent_spec:
                return {parent_spec}
        if endpoint not in boundary_ids:                # endpoint is the container of exactly one X
            held = {own_spec(c) for c in children.get(endpoint, [])} - {None}
            if len(held) == 1:
                return held                              # type: ignore[return-value]
        return set()

    return resolve, own_spec


def _is_group_or_title(cid: str, style: str) -> bool:
    if "shape=mxgraph.aws4.group" in style or "grIcon=" in style or "group_aws" in style:
        return True
    if "title" in cid.lower():
        return True
    return False


# ----------------------------------------------------------------------------- the harness
def check_drawio(xml_text: str, spec: FinalSpec, validation, attempt: int) -> HarnessRecord:
    """Compare the diagram XML against the FinalSpec; `validation` is a drawio_tools.ValidationOutcome (or None)."""
    comp_ids = [c.id for c in spec.components]
    spec_ids = set(comp_ids)
    boundary_ids = [b.id for b in spec.boundaries]
    in_counts = {"components": len(spec.components), "relationships": len(spec.relationships),
                 "boundaries": len(spec.boundaries)}
    errors: list[str] = []
    warnings: list[str] = []

    if validation is not None:
        errors.extend(f"validator: {e}" for e in validation.errors)
        warnings.extend(f"validator: {w}" for w in validation.warnings)

    facts = parse_drawio(xml_text)
    if facts.parse_error:
        if not (validation is not None and validation.errors):
            errors.append(facts.parse_error)
        out = {"components_found": 0, "relationships_found": 0, "boundaries_found": 0,
               "extra_icons": 0, "edges_total": 0}
        return HarnessRecord(step="harness_3", attempt=attempt, ok=False, in_counts=in_counts,
                             out_counts=out, errors=errors, warnings=warnings, details={})

    # --- components
    missing_components = [c for c in comp_ids if c not in facts.vertices]
    for cid in missing_components:
        errors.append(f"component '{cid}' has no cell with that id")

    # --- boundaries
    missing_boundaries = [b for b in boundary_ids if b not in facts.vertices]
    for bid in missing_boundaries:
        warnings.append(f"boundary '{bid}' has no cell with that id")

    # --- edges: dangling endpoints
    all_ids = facts.cell_ids
    dangling: list[dict] = []
    for eid, src, tgt, _label in facts.edges:
        for role, ep in (("source", src), ("target", tgt)):
            if not ep:
                dangling.append({"edge": eid, "end": role, "id": ""})
                errors.append(f"edge '{eid}' has no {role}")
            elif ep not in all_ids:
                dangling.append({"edge": eid, "end": role, "id": ep})
                errors.append(f"edge '{eid}' {role} '{ep}' does not exist")

    # --- relationships
    resolve, own_spec = _resolver(facts, spec_ids, set(boundary_ids))
    resolved_edges = [(eid, resolve(src), resolve(tgt)) for eid, src, tgt, _l in facts.edges]
    missing_rel: list[list[str]] = []
    reversed_rel: list[list[str]] = []
    found = 0
    used_edges: set[str] = set()
    for r in spec.relationships:
        fwd = [e for e, s, t in resolved_edges if r.source in s and r.target in t]
        rev = [e for e, s, t in resolved_edges if r.target in s and r.source in t]
        if fwd or (rev and r.direction == "bidirectional"):
            found += 1
            used_edges.update(fwd or rev)
        elif rev:
            found += 1
            used_edges.update(rev)
            reversed_rel.append([r.source, r.target])
            warnings.append(f"relationship {r.source} -> {r.target} is drawn in the opposite direction")
        else:
            missing_rel.append([r.source, r.target])
            errors.append(f"relationship {r.source} -> {r.target} ({r.id}) has no edge")

    # --- extra AWS icons
    boundary_set = set(boundary_ids)
    extra_icons: list[str] = []
    for cid, v in facts.vertices.items():
        style = v["style"]
        if "resIcon=mxgraph.aws4." not in style and "shape=mxgraph.aws4." not in style:
            continue
        if cid in spec_ids or cid in boundary_set or own_spec(cid) or own_spec(v["parent"]):
            continue
        if _is_group_or_title(cid, style):
            continue
        extra_icons.append(cid)
    for cid in extra_icons:
        warnings.append(f"extra AWS icon cell '{cid}' is not in the spec")

    unmapped_edges = [eid for eid, s, t in resolved_edges if eid not in used_edges]
    out_counts = {
        "components_found": len(comp_ids) - len(missing_components),
        "relationships_found": found,
        "boundaries_found": len(boundary_ids) - len(missing_boundaries),
        "extra_icons": len(extra_icons),
        "edges_total": len(facts.edges),
    }
    details = {
        "missing_components": missing_components,
        "missing_relationships": missing_rel,
        "reversed_relationships": reversed_rel,
        "missing_boundaries": missing_boundaries,
        "extra_icon_ids": extra_icons,
        "dangling_edges": dangling,
        "unmapped_edges": unmapped_edges,
    }
    return HarnessRecord(step="harness_3", attempt=attempt, ok=not errors, in_counts=in_counts,
                         out_counts=out_counts, errors=errors, warnings=warnings, details=details)
