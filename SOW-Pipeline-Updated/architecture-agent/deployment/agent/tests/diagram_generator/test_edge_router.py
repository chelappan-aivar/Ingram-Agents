"""libavoid edge routing on the built sample diagram (needs Node.js; set NODE_BIN or put node on PATH)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagram_generator.contracts import FinalSpec, Layout
from diagram_generator.harness.h3_drawio import check_drawio
from diagram_generator.render.drawio_builder import build_xml
from diagram_generator.render.drawio_tools import validate
from diagram_generator.render.edge_router import find_node, route_drawio
from tests.diagram_generator.conftest import make_settings

FIX = Path(__file__).resolve().parent / "fixtures"
needs_node = pytest.mark.skipif(find_node(make_settings(Path("/tmp"))) is None, reason="Node.js not installed")


def built(tmp_path: Path) -> tuple[Path, FinalSpec]:
    spec = FinalSpec.model_validate_json((FIX / "coder_sample_spec.json").read_text())
    layout = Layout.model_validate_json((FIX / "coder_sample_layout.json").read_text())
    p = tmp_path / "diagram.drawio"
    p.write_text(build_xml(spec, layout))
    return p, spec


def geometry(path: Path):
    import defusedxml.ElementTree as ET
    root = ET.parse(path).getroot()
    boxes, edges = {}, []
    for c in root.iter("mxCell"):
        g = c.find("mxGeometry")
        if c.get("vertex") == "1" and c.get("parent") == "1" and g.get("width") == "120":
            boxes[c.get("id")] = tuple(float(g.get(k)) for k in ("x", "y", "width", "height"))
        if c.get("edge") == "1":
            st = dict(p.split("=", 1) for p in c.get("style").split(";") if "=" in p)
            bends = [(float(p.get("x")), float(p.get("y"))) for p in c.iter("mxPoint")]
            edges.append((c.get("id"), c.get("source"), c.get("target"), st, bends))
    return boxes, edges


@needs_node
def test_every_edge_is_routed_around_the_boxes(tmp_path):
    path, spec = built(tmp_path)
    out = route_drawio(path, make_settings(tmp_path))
    assert out.errors == [] and out.unrouted == [] and sorted(out.routed) == sorted(r.id for r in spec.relationships)

    boxes, edges = geometry(path)
    exits, entries = set(), set()
    for eid, src, tgt, st, bends in edges:
        assert st["libavoidRouting"] == "1"
        s, t = boxes[src], boxes[tgt]
        a = (s[0] + float(st["exitX"]) * s[2], s[1] + float(st["exitY"]) * s[3])
        z = (t[0] + float(st["entryX"]) * t[2], t[1] + float(st["entryY"]) * t[3])
        assert float(st["exitX"]) in (0, 1) or float(st["exitY"]) in (0, 1)    # attached on the border
        assert float(st["entryX"]) in (0, 1) or float(st["entryY"]) in (0, 1)
        exits.add((src, a))
        entries.add((tgt, z))
        pts = [a] + bends + [z]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            assert abs(x1 - x2) < 1 or abs(y1 - y2) < 1, f"{eid} has a diagonal segment"
            for bid, (bx, by, bw, bh) in boxes.items():
                if bid in (src, tgt):
                    continue
                if abs(x1 - x2) < 1:
                    hit = bx < x1 < bx + bw and min(y1, y2) < by + bh and max(y1, y2) > by
                else:
                    hit = by < y1 < by + bh and min(x1, x2) < bx + bw and max(x1, x2) > bx
                assert not hit, f"{eid} runs through {bid}"
    assert len(exits) + len(entries) == 2 * len(edges)  # no two edges share an attach point

    v = validate(path, make_settings(tmp_path))
    assert v.ok, v.errors
    assert check_drawio(path.read_text(), spec, v, 1).ok


def test_without_node_the_file_is_left_alone(tmp_path):
    path, _ = built(tmp_path)
    before = path.read_text()
    out = route_drawio(path, make_settings(tmp_path, NODE_BIN=str(tmp_path / "no-node")))
    assert out.routed == [] and out.errors and "Node.js not found" in out.errors[0]
    assert path.read_text() == before


@needs_node
def test_sidecar_reports_edges_it_cannot_route(tmp_path):
    import subprocess

    from diagram_generator.render.edge_router import SIDECAR
    s = make_settings(tmp_path)
    payload = {"vertices": [{"id": "a", "x": 0, "y": 0, "w": 120, "h": 120}],
               "edges": [{"id": "loop", "source": "a", "target": "a"}]}
    proc = subprocess.run([find_node(s), str(SIDECAR), str(Path(s.vendor_dir) / "libavoid")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"routes": {}, "unrouted": ["loop"]}
