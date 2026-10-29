"""Edge routing with libavoid (vendor/libavoid), the obstacle-avoiding orthogonal router draw.io itself uses.

Runs on the finished .drawio file, after the vendored fixers (which may move actor boxes), so routes match the
geometry that is actually drawn. Every top-level vertex is an obstacle; groups that hold an edge's endpoint are
dropped by the routing core itself. The model never decides edge paths. Failure-tolerant like drawio_tools:
problems come back as data and the file keeps draw.io's own (unrouted) orthogonal edges.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as StdET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import defusedxml.ElementTree as ET

SIDECAR = Path(__file__).resolve().with_name("route_edges.mjs")
# Pins along all four sides (one per ~20px) instead of a floating centre: libavoid picks the side and position,
# so edges sharing a box get separate attach points and nudging can pull their corridors apart.
ALL_SIDES = ["N", "E", "S", "W"]
ROUTED_STYLE = "libavoidRouting=1;"  # draw.io builds with libavoid re-route the edge when boxes move; others ignore it


@dataclass
class RouteOutcome:
    routed: list[str] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def find_node(settings) -> Optional[str]:
    configured = getattr(settings, "node_bin", None)
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        return shutil.which(configured)
    return shutil.which("node")


def _num(geo, key: str) -> float:
    return float(geo.get(key) or 0)


def routing_input(root) -> tuple[list[dict], list[dict], dict[str, object]]:
    """Top-level vertices (absolute coordinates) and the edges between two of them."""
    vertices: list[dict] = []
    edges: list[dict] = []
    edge_cells: dict[str, object] = {}
    top = set()
    for cell in root.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("parent") != "1":
            continue
        geo = cell.find("mxGeometry")
        if geo is None or geo.get("relative") == "1":
            continue
        w, h = _num(geo, "width"), _num(geo, "height")
        if w > 0 and h > 0:
            vertices.append({"id": cell.get("id"), "x": _num(geo, "x"), "y": _num(geo, "y"), "w": w, "h": h})
            top.add(cell.get("id"))
    for cell in root.iter("mxCell"):
        src, tgt = cell.get("source"), cell.get("target")
        if cell.get("edge") == "1" and cell.get("parent") == "1" and src in top and tgt in top:
            edges.append({"id": cell.get("id"), "source": src, "target": tgt,
                          "sourceSides": ALL_SIDES, "targetSides": ALL_SIDES})
            edge_cells[cell.get("id")] = cell
    return vertices, edges, edge_cells


def _run_sidecar(node: str, payload: dict, settings) -> tuple[Optional[dict], Optional[str]]:
    libavoid_dir = Path(settings.vendor_dir) / "libavoid"
    try:
        proc = subprocess.run([node, str(SIDECAR), str(libavoid_dir)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=settings.tool_timeout_sec)
    except subprocess.TimeoutExpired:
        return None, f"edge router timed out after {settings.tool_timeout_sec}s"
    except OSError as e:
        return None, f"cannot run node: {e}"
    if proc.returncode != 0:
        return None, f"edge router exit code {proc.returncode}: {proc.stderr.strip()[-500:]}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError:
        return None, f"edge router returned no JSON: {proc.stdout.strip()[:300]}"


def _apply_route(cell, route: list[list[int]], src: dict, tgt: dict) -> None:
    """Fixed attach points from the route's ends (exitX/entryX) plus its bends as waypoints."""
    geo = cell.find("mxGeometry")
    if geo is None:
        geo = StdET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    for old in [c for c in geo if c.tag == "Array" and c.get("as") == "points"]:
        geo.remove(old)
    bends = route[1:-1]
    if bends:
        arr = StdET.Element("Array", {"as": "points"})
        for x, y in bends:
            StdET.SubElement(arr, "mxPoint", {"x": str(x), "y": str(y)})
        geo.insert(0, arr)
    managed = ("exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy", "libavoidRouting")
    kept = [p for p in cell.get("style", "").split(";") if p and p.split("=")[0] not in managed]
    (sx, sy), (tx, ty) = route[0], route[-1]
    kept += [f"exitX={round((sx - src['x']) / src['w'], 4)}", f"exitY={round((sy - src['y']) / src['h'], 4)}",
             "exitDx=0", "exitDy=0",
             f"entryX={round((tx - tgt['x']) / tgt['w'], 4)}", f"entryY={round((ty - tgt['y']) / tgt['h'], 4)}",
             "entryDx=0", "entryDy=0", ROUTED_STYLE.rstrip(";")]
    cell.set("style", ";".join(kept) + ";")


def route_drawio(drawio_path: Path, settings) -> RouteOutcome:
    """Route every edge of the file in place. On any problem the file is left as it was."""
    out = RouteOutcome()
    node = find_node(settings)
    if not node:
        out.errors.append("Node.js not found (set NODE_BIN or install Node 18+); edges keep draw.io's default routing")
        return out
    path = Path(drawio_path)
    try:
        tree = ET.parse(path)
    except Exception as e:  # ParseError, DefusedXmlException, OSError
        out.errors.append(f"cannot parse {path.name} for routing: {e}")
        return out
    vertices, edges, cells = routing_input(tree.getroot())
    if not edges:
        return out
    options = {"crossingPenalty": settings.routing_crossing_penalty,
               "shapeBufferDistance": settings.routing_shape_buffer,
               "idealNudgingDistance": settings.routing_nudge_distance}
    result, err = _run_sidecar(node, {"vertices": vertices, "edges": edges, "options": options}, settings)
    if err:
        out.errors.append(err)
        return out
    routes = result.get("routes") or {}
    box = {v["id"]: v for v in vertices}
    for eid, cell in cells.items():
        if len(routes.get(eid) or []) >= 2:
            _apply_route(cell, routes[eid], box[cell.get("source")], box[cell.get("target")])
            out.routed.append(eid)
        else:
            out.unrouted.append(eid)
    tree.write(path, encoding="unicode")
    return out
