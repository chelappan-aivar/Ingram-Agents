"""ADR design (adr_generator.contracts.Design, saved as design.json) -> FinalSpec, in code (no LLM).

The ADR step already decided every component, boundary and flow. Drawing exactly that design, instead of
re-reading the rendered adr.md with an LLM, means the diagram cannot miss, rename or invent a service, and the
diagram costs no model calls for its content.

Only presentation is added: an "AWS Cloud" group (and a Region group from `design.region` when the design has
none) so the drawing follows the AWS reference-diagram frame. No service is ever added.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from diagram_generator.contracts import BoundaryType, FinalSpec, SpecBoundary, SpecComponent, SpecRelationship

BOUNDARY_TYPES = set(BoundaryType.__args__)  # type: ignore[attr-defined]
OUTSIDE_TYPES = {"on_prem", "external"}
ACTOR_CATEGORIES = {"actor", "actors", "user", "users"}
EXTERNAL_CATEGORIES = {"external", "saas", "third_party", "on_prem"}
MAX_LABEL_WORDS = 4
SUBTITLE_MAX = 140


@dataclass
class DesignSpecResult:
    spec: FinalSpec
    subtitle: str = ""
    warnings: list[str] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def looks_like_design(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("components"), list) and any(
        isinstance(c, dict) and c.get("id") for c in data["components"])


def _safe_id(raw: str, used: set[str], fallback: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", (raw or "").strip()).strip("_") or fallback
    sid, n = base, 2
    while sid in used:
        sid, n = f"{base}_{n}", n + 1
    used.add(sid)
    return sid


def _subtitle(design) -> str:
    text = " ".join((design.context or "").split())
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= SUBTITLE_MAX:
        return first
    return first[:SUBTITLE_MAX].rsplit(" ", 1)[0] + "..."


def _label(text: str) -> str:
    # kept whole: the ADR decided the wording, and cutting "reads raw and writes Parquet" mid-phrase loses meaning
    return " ".join((text or "").split())


def build_spec_from_design(design, catalog) -> DesignSpecResult:
    """`design` is an adr_generator Design (validated). Never raises on content problems: they become warnings."""
    warnings: list[str] = []
    dropped: list[dict] = []
    used: set[str] = set()

    # ---- boundaries
    bmap: dict[str, str] = {}               # design id -> spec id
    raw_bounds = []
    for i, b in enumerate(design.boundaries):
        if not b.id.strip():
            dropped.append({"id": f"boundaries[{i}]", "reason": "boundary without id"})
            continue
        if b.id in bmap:
            dropped.append({"id": b.id, "reason": "duplicate boundary id"})
            continue
        bmap[b.id] = _safe_id(b.id, used, f"boundary_{i + 1}")
        raw_bounds.append(b)

    boundaries: list[SpecBoundary] = []
    for b in raw_bounds:
        btype = b.type if b.type in BOUNDARY_TYPES else "generic"
        if btype != b.type:
            warnings.append(f"boundary '{b.id}': unknown type '{b.type}', drawn as a generic group")
        parent = bmap.get(b.parent) if b.parent else None
        if b.parent and parent is None:
            warnings.append(f"boundary '{b.id}': unknown parent '{b.parent}', drawn at top level")
        if parent == bmap[b.id]:
            parent = None
        boundaries.append(SpecBoundary(id=bmap[b.id], type=btype, name=b.name or b.id, parent=parent,
                                       origin="stated"))

    # ---- presentation frame: AWS Cloud > Region around the AWS groups
    by_id = {b.id: b for b in boundaries}
    has_cloud = any(b.type == "aws_cloud" for b in boundaries)
    regions = [b for b in boundaries if b.type == "region"]
    cloud_id: Optional[str] = next((b.id for b in boundaries if b.type == "aws_cloud"), None)
    if not regions and (design.region or "").strip():
        rid = _safe_id("region", used, "region")
        region = SpecBoundary(id=rid, type="region", name=f"AWS Region {design.region.strip()}", parent=None,
                              origin="inferred")
        for b in boundaries:   # top-level AWS groups move into the Region
            if b.parent is None and b.type not in OUTSIDE_TYPES and b.type != "aws_cloud":
                b.parent = rid
        boundaries.insert(0, region)
        by_id[rid] = region
        regions = [region]
    if not has_cloud:
        cloud_id = _safe_id("aws_cloud", used, "aws_cloud")
        cloud = SpecBoundary(id=cloud_id, type="aws_cloud", name="AWS Cloud", parent=None, origin="inferred")
        for b in boundaries:
            if b.parent is None and b.type not in OUTSIDE_TYPES:
                b.parent = cloud_id
        boundaries.insert(0, cloud)

    # ---- components
    cmap: dict[str, str] = {}
    components: list[SpecComponent] = []
    unmatched: list[str] = []
    for i, c in enumerate(design.components):
        if not c.id.strip():
            dropped.append({"id": f"components[{i}]", "reason": "component without id"})
            continue
        if c.id in cmap:
            dropped.append({"id": c.id, "reason": "duplicate component id"})
            continue
        category = (c.category or "").strip().lower()
        if category in ACTOR_CATEGORIES:
            kind = "actor"
        elif category in EXTERNAL_CATEGORIES:
            kind = "external"
        else:
            kind = catalog.kind_of(c.service) or catalog.kind_of(c.name) or "aws_service"
        service = c.service.strip() or None
        icon = catalog.resolve(service, c.name, kind)
        if kind in ("actor", "external"):
            service = None   # external systems show their name only; their "service" text is a description
        elif not icon.matched:
            unmatched.append(c.id)
            warnings.append(f"component '{c.id}' ({c.name}): no icon for service '{c.service}'; "
                            f"drawn with a generic icon")
        boundary = bmap.get(c.boundary) if c.boundary else None
        if c.boundary and boundary is None:
            warnings.append(f"component '{c.id}': unknown boundary '{c.boundary}', drawn without a group")
        name = c.name.strip() or c.service or c.id
        if c.quantity > 1:
            name = f"{name} (x{c.quantity})"
        cmap[c.id] = _safe_id(c.id, used, f"component_{i + 1}")
        components.append(SpecComponent(id=cmap[c.id], name=name, service=service, kind=kind, boundary=boundary,
                                        origin="inferred" if c.assumed else "stated", icon=icon))

    # ---- relationships
    relationships: list[SpecRelationship] = []
    pairs: dict[tuple[str, str], SpecRelationship] = {}
    for i, r in enumerate(design.relationships):
        src, tgt = cmap.get(r.source), cmap.get(r.target)
        rid = r.id or f"relationships[{i}]"
        if src is None or tgt is None:
            missing = r.source if src is None else r.target
            dropped.append({"id": rid, "reason": f"unknown component '{missing}'"})
            warnings.append(f"relationship '{rid}' dropped: unknown component '{missing}'")
            continue
        if src == tgt:
            dropped.append({"id": rid, "reason": "self-loop"})
            continue
        if (tgt, src) in pairs and pairs[(tgt, src)].direction == "one_way":   # A->B and B->A: one two-way line
            pairs[(tgt, src)].direction = "bidirectional"
            continue
        label = _label(r.label)
        if (src, tgt) in pairs:   # second flow between the same boxes: one line, both labels
            kept = pairs[(src, tgt)]
            if label and label.lower() not in kept.label.lower():
                kept.label = f"{kept.label}; {label}" if kept.label else label
            warnings.append(f"relationship '{rid}' drawn on the same line as '{kept.id}' (same components)")
            continue
        if len(label.split()) > MAX_LABEL_WORDS:
            warnings.append(f"relationship '{rid}': label has more than {MAX_LABEL_WORDS} words")
        rel = SpecRelationship(id=_safe_id(r.id, used, f"r{i + 1}"), source=src, target=tgt, label=label,
                               direction="one_way", origin="stated")
        pairs[(src, tgt)] = rel
        relationships.append(rel)

    spec = FinalSpec(title=design.title or "Architecture", components=components, relationships=relationships,
                     boundaries=boundaries)
    counts = {"components": len(components), "relationships": len(relationships), "boundaries": len(boundaries),
              "unmatched_icons": len(unmatched), "dropped": len(dropped)}
    return DesignSpecResult(spec=spec, subtitle=_subtitle(design), warnings=warnings, dropped=dropped, counts=counts)
