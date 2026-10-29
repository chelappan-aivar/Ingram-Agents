"""Deterministic layout with ELK (Eclipse Layout Kernel, vendor/elkjs): FinalSpec -> Layout, no LLM.

ELK's layered algorithm places every box and sizes every group from the graph itself: boundaries become nested
compound nodes, relationships become edges, and the flow runs left to right. Because the geometry is computed,
boxes cannot overlap, every box sits inside its boundary and sibling groups never intersect, so the layout
needs no review-and-retry loop. The same spec always gives the same layout.

The result is the same `Layout` contract the LLM Diagram coder returned (absolute pixels), so drawio_builder,
the geometry checks and the libavoid edge router are unchanged. ELK runs in a Node subprocess
(render/layout_elk.mjs), like the edge router.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from diagram_generator.contracts import NODE_SIZE, AuxGroup, FinalSpec, Layout, LayoutNode, LayoutRect, SpecComponent
from diagram_generator.render.drawio_builder import ACTOR_KINDS, TITLE_H
from diagram_generator.render.edge_router import find_node

SIDECAR = Path(__file__).resolve().with_name("layout_elk.mjs")
ROOT = "root"
AUX_ID = "aux-group"
AUX_SHAPES = {"cloudwatch", "cloudwatch_2", "cloudtrail", "xray", "identity_and_access_management"}
ORIGIN_X, ORIGIN_Y = 40, TITLE_H + 20          # top-left of the drawing, below the title block
GROUP_PADDING = "[top=50,left=30,bottom=30,right=30]"  # 30px title strip + 20px gap above member boxes
AUX_PADDING = "[top=45,left=25,bottom=25,right=25]"
CLOUD_TYPES = ("aws_cloud", "region")

# Applied to the root and repeated on every compound node, so spacing is the same at every nesting level.
SPACING = {
    "elk.spacing.nodeNode": "60",
    "elk.layered.spacing.nodeNodeBetweenLayers": "110",
    "elk.spacing.edgeNode": "30",
    "elk.spacing.edgeEdge": "15",
    "elk.layered.spacing.edgeNodeBetweenLayers": "30",
    "elk.spacing.componentComponent": "80",
}
ROOT_OPTIONS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.edgeRouting": "ORTHOGONAL",
    "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.randomSeed": "1",
    "elk.padding": "[top=0,left=0,bottom=0,right=0]",
    **SPACING,
}


@dataclass
class ElkOutcome:
    layout: Optional[Layout] = None
    error: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def is_auxiliary(c: SpecComponent) -> bool:
    return c.icon.shape in AUX_SHAPES


def _group_min(name: str) -> str:
    # wide enough for the group title (icon + text) and one box with padding
    return f"({max(NODE_SIZE + 60, len(name) * 8 + 60)},{NODE_SIZE + 80})"


def build_graph(spec: FinalSpec) -> tuple[dict, list[str]]:
    """The ELK input graph and notes on choices made (where unbounded services were placed, etc.)."""
    notes: list[str] = []
    bounds = {b.id: b for b in spec.boundaries}

    def parent_of(bid: str) -> str:
        p = bounds[bid].parent
        return p if p in bounds and p != bid else ROOT

    # break parent cycles: walk up; a boundary that reaches itself is re-hung on the root
    parent: dict[str, str] = {}
    for bid in bounds:
        seen, cur = {bid}, parent_of(bid)
        while cur != ROOT and cur not in seen:
            seen.add(cur)
            cur = parent_of(cur)
        parent[bid] = ROOT if cur != ROOT else parent_of(bid)

    # An AWS service with no boundary belongs to the cloud: the single top-level Region, else AWS Cloud.
    regions = [b.id for b in spec.boundaries if b.type == "region"]
    clouds = [b.id for b in spec.boundaries if b.type == "aws_cloud"]
    home = regions[0] if len(regions) == 1 else (clouds[0] if clouds else ROOT)

    def container_of(c: SpecComponent) -> str:
        if c.boundary in bounds:
            return c.boundary
        if c.kind in ACTOR_KINDS:
            return ROOT
        return home

    aux = [c for c in spec.components if is_auxiliary(c) and container_of(c) == home and home != ROOT]
    use_aux = bool(aux)
    aux_ids = {c.id for c in aux} if use_aux else set()

    def new_compound(cid: str, name: str, padding: str) -> dict:
        return {"id": cid, "children": [], "layoutOptions": {
            **SPACING, "elk.padding": padding,
            "elk.nodeSize.constraints": "MINIMUM_SIZE", "elk.nodeSize.minimum": _group_min(name)}}

    nodes: dict[str, dict] = {ROOT: {"id": ROOT, "layoutOptions": dict(ROOT_OPTIONS), "children": [], "edges": []}}
    for b in spec.boundaries:
        nodes[b.id] = new_compound(b.id, b.name, GROUP_PADDING)
    if use_aux:
        nodes[AUX_ID] = new_compound(AUX_ID, "Auxiliary Services", AUX_PADDING)
        nodes[AUX_ID]["layoutOptions"]["elk.layered.layering.layerConstraint"] = "LAST"
        nodes[home]["children"].append(nodes[AUX_ID])

    # boundaries in spec order (model order keeps the layout stable and close to the author's intent)
    for b in spec.boundaries:
        nodes[parent[b.id]]["children"].append(nodes[b.id])

    for c in spec.components:
        leaf = {"id": c.id, "width": NODE_SIZE, "height": NODE_SIZE}
        if c.kind == "actor":
            leaf["layoutOptions"] = {"elk.layered.layering.layerConstraint": "FIRST"}
        target = AUX_ID if c.id in aux_ids else container_of(c)
        nodes[target]["children"].append(leaf)
        if c.boundary is None and c.kind not in ACTOR_KINDS and home != ROOT and c.id not in aux_ids:
            notes.append(f"'{c.id}' has no boundary; placed in '{home}'")

    comp_ids = {c.id for c in spec.components}
    for r in spec.relationships:
        if r.source == r.target or r.source not in comp_ids or r.target not in comp_ids:
            continue
        if r.source in aux_ids or r.target in aux_ids:   # monitoring lines must not pull boxes out of the flow
            continue
        # No ELK edge labels: ELK turns each label into a dummy node and an extra layer, which made a 17-box
        # diagram ~70% wider. The 110px layer gap already fits a 4-word label, which the builder places on the
        # routed line.
        nodes[ROOT]["edges"].append({"id": f"e:{r.id}", "sources": [r.source], "targets": [r.target]})
    return nodes[ROOT], notes


def to_layout(result: dict, spec: FinalSpec, subtitle: str = "") -> Layout:
    """ELK result (coordinates relative to each parent) -> absolute Layout below the title block."""
    comp_ids = {c.id for c in spec.components}
    bound_ids = {b.id for b in spec.boundaries}
    groups: list[LayoutRect] = []
    nodes: list[LayoutNode] = []
    aux: Optional[AuxGroup] = None

    def walk(n: dict, ox: float, oy: float) -> None:
        nonlocal aux
        for ch in n.get("children") or []:
            x, y = ox + float(ch.get("x", 0)), oy + float(ch.get("y", 0))
            cid = ch["id"]
            if cid in comp_ids:
                nodes.append(LayoutNode(id=cid, x=round(x), y=round(y)))
            elif cid in bound_ids:
                groups.append(LayoutRect(id=cid, x=round(x), y=round(y), w=round(ch["width"]), h=round(ch["height"])))
            elif cid == AUX_ID:
                aux = AuxGroup(x=round(x), y=round(y), w=round(ch["width"]), h=round(ch["height"]))
            walk(ch, x, y)

    walk(result, ORIGIN_X, ORIGIN_Y)
    order = {b.id: i for i, b in enumerate(spec.boundaries)}
    groups.sort(key=lambda g: order.get(g.id, 0))
    return Layout(subtitle=subtitle, groups=groups, nodes=nodes, aux_group=aux)


def elk_layout(spec: FinalSpec, settings, subtitle: str = "") -> ElkOutcome:
    """Lay out the spec with ELK. Never raises: problems come back in .error (the caller may fall back)."""
    node = find_node(settings)
    if not node:
        return ElkOutcome(error="Node.js not found (set NODE_BIN or install Node 18+)")
    elk_dir = Path(settings.vendor_dir) / "elkjs"
    if not (elk_dir / "elk.bundled.js").is_file():
        return ElkOutcome(error=f"ELK bundle missing: {elk_dir / 'elk.bundled.js'}")
    graph, notes = build_graph(spec)
    try:
        proc = subprocess.run([node, str(SIDECAR), str(elk_dir)], input=json.dumps(graph),
                              capture_output=True, text=True, timeout=settings.tool_timeout_sec)
    except subprocess.TimeoutExpired:
        return ElkOutcome(error=f"ELK layout timed out after {settings.tool_timeout_sec}s", notes=notes)
    except OSError as e:
        return ElkOutcome(error=f"cannot run node: {e}", notes=notes)
    if proc.returncode != 0:
        return ElkOutcome(error=f"ELK exit code {proc.returncode}: {proc.stderr.strip()[-500:]}", notes=notes)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ElkOutcome(error=f"ELK returned no JSON: {proc.stdout.strip()[:300]}", notes=notes)
    return ElkOutcome(layout=to_layout(result, spec, subtitle), notes=notes)
