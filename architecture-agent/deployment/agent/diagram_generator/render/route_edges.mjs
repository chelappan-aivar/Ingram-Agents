// Edge router sidecar: obstacle-avoiding orthogonal routes from libavoid (vendor/libavoid, see SOURCE.md).
// Called by diagram_generator/render/edge_router.py:  node route_edges.mjs <vendor/libavoid dir>
// stdin : {"vertices": [{id, x, y, w, h}], "edges": [{id, source, target, sourceSides, targetSides}],
//          "options": {shapeBufferDistance, idealNudgingDistance, segmentPenalty, crossingPenalty}}  (absolute px)
// stdout: {"routes": {edgeId: [[x, y], ...]}, "unrouted": [edgeId, ...]}   full routes, endpoints included
//
// Uses draw.io's routing core (libavoid-routing.js) for its helpers and defaults, but runs the solve here:
// AvoidRouting.computeRoutes drops each route's endpoints, and we need them to pin the exact attach point
// on each box (exitX/entryX) so draw.io draws the route as computed.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// The wasm loader may print; stdout carries only the JSON answer.
console.log = console.info = (...a) => process.stderr.write(a.join(" ") + "\n");

const dir = process.argv[2];
if (!dir) {
  process.stderr.write("usage: node route_edges.mjs <libavoid vendor dir>\n");
  process.exit(2);
}

const input = JSON.parse(readFileSync(0, "utf8"));
const { AvoidLib } = await import(pathToFileURL(join(dir, "libavoid-node.mjs")).href);
await import(pathToFileURL(join(dir, "libavoid-routing.js")).href);  // sets globalThis.AvoidRouting
await AvoidLib.load(join(dir, "libavoid.wasm"));
const Avoid = AvoidLib.getInstance();
const R = globalThis.AvoidRouting;

const buffer = input.options?.shapeBufferDistance ?? 16;  // computeRoutes' defaults
const nudge = input.options?.idealNudgingDistance ?? 14;
const edges = (input.edges || []).filter((e) => e.source !== e.target);
// Groups that hold an edge's endpoint are where the edge lives, not obstacles.
const vertices = R.filterEnclosing(input.vertices || [], edges);
const bounds = Object.fromEntries(vertices.map((v) => [v.id, v]));

// displayRoute()/at() hand out owned copies in this build (see the libavoid-routing.js header); free them.
function readRoute(conn) {
  const route = conn.displayRoute();
  const pts = [];
  for (let k = 0; k < route.size(); k++) {
    const p = route.at(k);
    pts.push({ x: p.x, y: p.y });
    p.delete();
  }
  route.delete();
  return pts;
}

function solve(batch, clearance) {
  const router = new Avoid.Router(Avoid.RouterFlag.OrthogonalRouting.value);
  router.setRoutingParameter(Avoid.RoutingParameter.shapeBufferDistance, clearance);
  router.setRoutingParameter(Avoid.RoutingParameter.idealNudgingDistance, nudge);
  for (const name of ["segmentPenalty", "crossingPenalty"]) {
    if (input.options?.[name] != null) router.setRoutingParameter(Avoid.RoutingParameter[name], input.options[name]);
  }
  const shapes = {};
  for (const v of vertices) {
    const p1 = new Avoid.Point(v.x, v.y), p2 = new Avoid.Point(v.x + v.w, v.y + v.h);
    const rect = new Avoid.Rectangle(p1, p2);
    shapes[v.id] = new Avoid.ShapeRef(router, rect);
    rect.delete(); p1.delete(); p2.delete();
  }
  // One pin class per (shape, pin set): two classes with a pin at the same point break the visibility sweep.
  // Pins are exclusive (one connector each), unlike draw.io's snap points: two edges meeting one box get
  // different attach points instead of sharing a final segment.
  const classes = {};
  let nextClass = 0;
  function connEnd(vid, sides) {
    const b = bounds[vid];
    const pins = R.endPins(b, null, sides, null);
    const key = vid + "\n" + pins.map((p) => `${p.x},${p.y},${p.dir}`).sort().join(" ");
    if (!classes[key]) {
      classes[key] = { id: ++nextClass, abs: pins.map((p) => ({ x: b.x + p.x * b.w, y: b.y + p.y * b.h })) };
      for (const p of pins) {
        new Avoid.ShapeConnectionPin(shapes[vid], classes[key].id, p.x, p.y, true, 0, p.dir).setExclusive(true);
      }
    }
    return { end: new Avoid.ConnEnd(shapes[vid], classes[key].id), abs: classes[key].abs };
  }
  const conns = [];
  for (const e of batch) {
    if (!bounds[e.source] || !bounds[e.target]) continue;
    const s = connEnd(e.source, e.sourceSides), t = connEnd(e.target, e.targetSides);
    conns.push({ e, conn: new Avoid.ConnRef(router, s.end, t.end), sPins: s.abs, tPins: t.abs });
    s.end.delete(); t.end.delete();
  }
  router.processTransaction();
  const routes = {}, failed = [];
  for (const c of conns) {
    const pts = readRoute(c.conn);
    if (R.isFallbackRoute(pts, c.sPins, c.tPins)) failed.push(c.e);
    else routes[c.e.id] = pts;
  }
  router.delete();
  return { routes, failed };
}

function clean(pts) {
  const r = pts.map((p) => [Math.round(p.x), Math.round(p.y)]);
  return r.filter((p, k) => k === 0 || k === r.length - 1 ||
    (p[0] - r[k - 1][0]) * (r[k + 1][1] - r[k - 1][1]) !== (r[k + 1][0] - r[k - 1][0]) * (p[1] - r[k - 1][1]));
}

// As computeRoutes: a connector with no route is retried alone with the clearance halved, down to 2px.
const out = { routes: {}, unrouted: [] };
let { routes, failed } = solve(edges, buffer);
Object.assign(out.routes, routes);
for (let c = Math.floor(buffer / 2); failed.length && c >= 2; c = Math.floor(c / 2)) {
  ({ routes, failed } = solve(failed, c));
  Object.assign(out.routes, routes);
}
for (const id in out.routes) out.routes[id] = clean(out.routes[id]);
out.unrouted = (input.edges || []).map((e) => e.id).filter((id) => !out.routes[id]);
process.stdout.write(JSON.stringify(out));
