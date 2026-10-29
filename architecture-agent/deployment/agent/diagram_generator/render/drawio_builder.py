"""Builds the draw.io XML from the FinalSpec plus the Diagram coder's Layout (no LLM).

The model decides WHERE boxes and groups go; this module decides HOW they look, using the AWS skill's exact
styles (diagram_generator/render/aws_styles.py). Every spec item is drawn exactly once with the id contract that
Harness 3 checks: service icon id = component id inside box `<id>__box`; actor/external container id = component
id with icon `<id>__icon`; group id = boundary id; edge id = relationship id with label `<id>__label`.
Edges attach to the 120x120 boxes with floating ends and no waypoints; render/edge_router.py adds the routes.
All cells use absolute coordinates under the default parent.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from diagram_generator.contracts import NODE_SIZE, FinalSpec, Layout, SpecComponent
from diagram_generator.render import aws_styles as S

ACTOR_KINDS = {"actor", "external"}
MARGIN = 40
TITLE_H = 110  # title, subtitle and separator occupy y < TITLE_H


def _a(v: str) -> str:
    return escape(v, {'"': "&quot;"})


def endpoint_id(c: SpecComponent) -> str:
    """The cell an edge attaches to: actor/external container, otherwise the category box."""
    return c.id if c.kind in ACTOR_KINDS else f"{c.id}__box"


def _depth(bid: str, parent: dict[str, str | None]) -> int:
    d, cur, seen = 0, parent.get(bid), set()
    while cur and cur not in seen:
        seen.add(cur)
        d, cur = d + 1, parent.get(cur)
    return d


def bounds(layout: Layout) -> tuple[int, int]:
    xs = [g.x + g.w for g in layout.groups] + [n.x + NODE_SIZE for n in layout.nodes]
    ys = [g.y + g.h for g in layout.groups] + [n.y + NODE_SIZE for n in layout.nodes]
    if layout.aux_group:
        xs.append(layout.aux_group.x + layout.aux_group.w)
        ys.append(layout.aux_group.y + layout.aux_group.h)
    return (max(xs, default=800) + MARGIN, max(ys, default=600) + MARGIN)


def build_xml(spec: FinalSpec, layout: Layout) -> str:
    cells: list[str] = []

    def vertex(cid: str, value: str, style: str, x, y, w, h, parent: str = "1", extra: str = "") -> None:
        cells.append(f'<mxCell id="{_a(cid)}" value="{_a(value)}" style="{_a(style)}" vertex="1" parent="{_a(parent)}"'
                     f'{extra}><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')

    width, height = bounds(layout)

    # title block
    vertex("title-text", _a(spec.title), S.TITLE, 40, 20, max(400, width - 80), 40)
    if layout.subtitle:
        vertex("subtitle-text", _a(layout.subtitle), S.SUBTITLE, 42, 60, max(400, width - 80), 26)
    vertex("title-separator", "", S.SEPARATOR, 42, 92, max(400, width - 82), 10)

    # groups, outermost first
    btype = {b.id: b for b in spec.boundaries}
    parent = {b.id: b.parent for b in spec.boundaries}
    for g in sorted((g for g in layout.groups if g.id in btype), key=lambda g: _depth(g.id, parent)):
        b = btype[g.id]
        vertex(b.id, _a(b.name), S.group_style(b.type), g.x, g.y, g.w, g.h)
    if layout.aux_group:
        ag = layout.aux_group
        vertex("aux-group", "Auxiliary Services", S.AUX_GROUP, ag.x, ag.y, ag.w, ag.h)

    # components
    pos = {n.id: n for n in layout.nodes}
    for c in spec.components:
        n = pos.get(c.id)
        if n is None:
            continue
        ic = c.icon
        if c.kind in ACTOR_KINDS:
            vertex(c.id, _a(c.name), f"{S.ACTOR_BASE}fillColor={ic.tint_color};strokeColor={ic.stroke_color};",
                   n.x, n.y, NODE_SIZE, NODE_SIZE)
            vertex(f"{c.id}__icon", "", S.icon_style(ic), 36, 44, 48, 48, parent=c.id)
        else:
            label = ic.category_label or "Service"
            vertex(f"{c.id}__box", _a(label),
                   f"{S.BOX_BASE}fillColor={ic.tint_color};strokeColor={ic.stroke_color};fontColor={ic.stroke_color};",
                   n.x, n.y, NODE_SIZE, NODE_SIZE)
            text = _a(c.name)
            if c.service and c.service.strip().lower() != c.name.strip().lower():
                text += f"<br><i>{_a(c.service)}</i>"
            vertex(c.id, text, S.icon_style(ic), 36, 28, 48, 48, parent=f"{c.id}__box")

    # edges (after vertices so they draw on top)
    comp = {c.id: c for c in spec.components}
    for r in spec.relationships:
        if r.source not in pos or r.target not in pos:
            continue
        style = S.EDGE_BASE + ("startArrow=block;startFill=1;" if r.direction == "bidirectional" else "startArrow=none;")
        cells.append(f'<mxCell id="{_a(r.id)}" style="{_a(style)}" edge="1" parent="1" '
                     f'source="{_a(endpoint_id(comp[r.source]))}" target="{_a(endpoint_id(comp[r.target]))}">'
                     '<mxGeometry relative="1" as="geometry" /></mxCell>')
        if r.label:
            cells.append(f'<mxCell id="{_a(r.id)}__label" value="{_a(_a(r.label))}" style="{_a(S.LABEL_BASE)}" '
                         f'connectable="0" vertex="1" parent="{_a(r.id)}"><mxGeometry relative="1" '
                         'x="0" y="0" as="geometry"><mxPoint as="offset" /></mxGeometry></mxCell>')

    return ('<mxfile host="Electron" version="29.6.1"><diagram name="Architecture" id="architecture">'
            f'<mxGraphModel dx="{width}" dy="{height}" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" '
            f'arrows="1" fold="1" page="0" pageScale="1" pageWidth="{width}" pageHeight="{height}" math="0" shadow="0">'
            '<root><mxCell id="0" /><mxCell id="1" parent="0" />' + "".join(cells) +
            "</root></mxGraphModel></diagram></mxfile>\n")
