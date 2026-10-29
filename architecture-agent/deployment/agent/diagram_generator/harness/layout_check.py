"""Harness 3, part 1: geometric checks of the Diagram coder's layout (no LLM), before any XML is built.

fit_groups first grows a group that a member box or child group pokes out of (never shrinks, never moves a box).
Errors are concrete and fixable, so they go back to the coder for a retry:
  missing nodes/groups, overlapping boxes, a box outside its boundary or inside a group it does not
  belong to, a group outside its parent or overlapping a sibling, content in the title band.
Edges are not checked here: render/edge_router.py routes them around boxes.
Warnings are softer readability hints (box too close to a group title strip, a group that was grown)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from diagram_generator.contracts import NODE_SIZE, FinalSpec, Layout
from diagram_generator.render.drawio_builder import TITLE_H

PAD = 10          # min gap between boxes / box and group edge
TITLE_STRIP = 30  # group title strip height
GROW_TOP = 40     # when a group grows: room above a member (title strip + gap), as the layout prompt asks
GROW_SIDE = 20    # and on the other sides


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def r(self) -> float:
        return self.x + self.w

    @property
    def b(self) -> float:
        return self.y + self.h

    def contains(self, o: "Rect", margin: float = 0) -> bool:
        return o.x >= self.x + margin and o.y >= self.y + margin and o.r <= self.r - margin and o.b <= self.b - margin

    def intersects(self, o: "Rect", gap: float = 0) -> bool:
        return not (o.x >= self.r + gap or o.r + gap <= self.x or o.y >= self.b + gap or o.b + gap <= self.y)


@dataclass
class LayoutReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _depth(bid: str, parent: dict[str, Optional[str]]) -> int:
    d, cur, seen = 0, parent.get(bid), set()
    while cur and cur not in seen:
        seen.add(cur)
        d, cur = d + 1, parent.get(cur)
    return d


def fit_groups(spec: FinalSpec, layout: Layout) -> tuple[Layout, list[str]]:
    """Grow each group around a member box or child group that partly sticks out of it, innermost group first.

    Only members that already overlap the group are fitted; a member placed wholly outside is a placement
    mistake that check_layout reports. A group never grows up into the title band. The grown layout is kept
    only when check_layout finds fewer errors in it than in the original (growing can also make a group
    cover a box that is not its member). Returns the layout to use and one note per grown group."""
    bounds = {b.id: b for b in spec.boundaries}
    parent = {b.id: b.parent for b in spec.boundaries}
    rects = {g.id: Rect(g.x, g.y, g.w, g.h) for g in layout.groups if g.id in bounds}
    boxes = {n.id: Rect(n.x, n.y, NODE_SIZE, NODE_SIZE) for n in layout.nodes}
    members: dict[str, list[str]] = {}
    for c in spec.components:
        if c.boundary in rects and c.id in boxes:
            members.setdefault(c.boundary, []).append(c.id)
    notes: list[str] = []
    for gid in sorted(rects, key=lambda g: _depth(g, parent), reverse=True):
        g = rects[gid]
        inner = [(f"component '{cid}'", boxes[cid]) for cid in members.get(gid, [])]
        inner += [(f"group '{cg}'", rects[cg]) for cg in rects if parent.get(cg) == gid]
        poking = [(label, r) for label, r in inner if g.intersects(r) and not g.contains(r, margin=PAD)]
        if not poking:
            continue
        for _, r in poking:
            x0, y0 = min(g.x, r.x - GROW_SIDE), min(g.y, max(r.y - GROW_TOP, TITLE_H + 10))
            x1, y1 = max(g.r, r.r + GROW_SIDE), max(g.b, r.b + GROW_SIDE)
            g.x, g.y, g.w, g.h = x0, y0, x1 - x0, y1 - y0
        notes.append(f"group '{gid}' grown to ({g.x:.0f}, {g.y:.0f}, {g.w:.0f}x{g.h:.0f}) to fit "
                     + ", ".join(label for label, _ in poking))
    if not notes:
        return layout, []
    groups = [g.model_copy(update={"x": int(rects[g.id].x), "y": int(rects[g.id].y), "w": int(rects[g.id].w),
                                   "h": int(rects[g.id].h)}) if g.id in rects else g for g in layout.groups]
    grown = layout.model_copy(update={"groups": groups})
    if len(check_layout(spec, grown).errors) >= len(check_layout(spec, layout).errors):
        return layout, []
    return grown, notes


def check_layout(spec: FinalSpec, layout: Layout) -> LayoutReport:
    rep = LayoutReport()
    comps = {c.id: c for c in spec.components}
    bounds = {b.id: b for b in spec.boundaries}
    parent = {b.id: b.parent for b in spec.boundaries}

    nodes = {n.id: Rect(n.x, n.y, NODE_SIZE, NODE_SIZE) for n in layout.nodes if n.id in comps}
    groups = {g.id: Rect(g.x, g.y, g.w, g.h) for g in layout.groups if g.id in bounds}

    for n in layout.nodes:
        if n.id not in comps:
            rep.warnings.append(f"layout node '{n.id}' is not a spec component (ignored)")
    for g in layout.groups:
        if g.id not in bounds:
            rep.warnings.append(f"layout group '{g.id}' is not a spec boundary (ignored)")
    rep.errors += [f"component '{c}' has no position in nodes" for c in comps if c not in nodes]
    rep.errors += [f"boundary '{b}' has no rectangle in groups" for b in bounds if b not in groups]

    # title band
    for cid, r in nodes.items():
        if r.y < TITLE_H + 20:
            rep.errors.append(f"component '{cid}' at y={r.y:.0f} overlaps the title area; use y >= {TITLE_H + 20}")
    for gid, r in groups.items():
        if r.y < TITLE_H + 10:
            rep.errors.append(f"group '{gid}' at y={r.y:.0f} overlaps the title area; use y >= {TITLE_H + 10}")

    # box overlaps
    ids = list(nodes)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if nodes[a].intersects(nodes[b], gap=PAD):
                rep.errors.append(f"components '{a}' and '{b}' overlap or are closer than {PAD}px")

    def ancestors(bid: Optional[str]) -> list[str]:
        out, cur, seen = [], bid, set()
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = parent.get(cur)
        return out

    # boxes vs groups
    for cid, r in nodes.items():
        c = comps[cid]
        allowed = set(ancestors(c.boundary))
        if c.boundary and c.boundary in groups and not groups[c.boundary].contains(r, margin=PAD):
            rep.errors.append(f"component '{cid}' must sit fully inside its boundary '{c.boundary}' "
                              f"(with {PAD}px padding)")
        if c.boundary and c.boundary in groups and groups[c.boundary].contains(r) and \
                r.y < groups[c.boundary].y + TITLE_STRIP + 10:
            rep.warnings.append(f"component '{cid}' touches the title strip of '{c.boundary}'")
        for gid, g in groups.items():
            if gid in allowed:
                continue
            # an AWS service with no boundary may sit at AWS Cloud / Region level
            if c.boundary is None and c.kind not in ("actor", "external") and bounds[gid].type in ("aws_cloud", "region"):
                continue
            if g.intersects(r):
                rep.errors.append(f"component '{cid}' (boundary: {c.boundary or 'none'}) overlaps group '{gid}' "
                                  "it does not belong to; move it out of that rectangle")

    # groups vs groups
    for gid, g in groups.items():
        p = parent.get(gid)
        if p and p in groups and not groups[p].contains(g, margin=PAD):
            rep.errors.append(f"group '{gid}' must sit fully inside its parent '{p}' (with {PAD}px padding)")
    gids = list(groups)
    for i, a in enumerate(gids):
        for b in gids[i + 1:]:
            if a in ancestors(b) or b in ancestors(a):
                continue
            ga, gb = groups[a], groups[b]
            if ga.intersects(gb) and not (ga.contains(gb) or gb.contains(ga)):
                rep.errors.append(f"groups '{a}' and '{b}' partly overlap; a group must either contain another "
                                  "completely or not touch it")

    rep.counts = {"nodes": len(nodes), "groups": len(groups), "layout_errors": len(rep.errors)}
    return rep


def drawn_overlaps(xml_text: str, spec: FinalSpec) -> list[str]:
    """Box overlaps in the FINISHED drawing (after the vendored fixers, which may move actor boxes).

    check_layout only sees the layout as planned; this reads the boxes actually written to the file."""
    import defusedxml.ElementTree as ET

    from diagram_generator.render.drawio_builder import endpoint_id

    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except Exception as e:  # ParseError etc.; the validator reports unreadable files
        return [f"cannot re-check the drawn layout: {e}"]
    want = {endpoint_id(c): c.id for c in spec.components}
    boxes: dict[str, Rect] = {}
    for cell in root.iter("mxCell"):
        cid = cell.get("id")
        geo = cell.find("mxGeometry")
        if cid in want and cell.get("parent") == "1" and geo is not None:
            boxes[want[cid]] = Rect(float(geo.get("x") or 0), float(geo.get("y") or 0),
                                    float(geo.get("width") or 0), float(geo.get("height") or 0))
    ids = list(boxes)
    return [f"components '{a}' and '{b}' overlap in the drawn diagram (after post-processing)"
            for i, a in enumerate(ids) for b in ids[i + 1:] if boxes[a].intersects(boxes[b])]
