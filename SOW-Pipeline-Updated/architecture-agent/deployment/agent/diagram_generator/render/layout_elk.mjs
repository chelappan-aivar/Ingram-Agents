// Layout sidecar: deterministic hierarchical layout with ELK (vendor/elkjs, see vendor/SOURCE.md).
// Called by diagram_generator/render/elk_layout.py:  node layout_elk.mjs <vendor/elkjs dir>
// stdin : one ELK JSON graph (https://eclipse.dev/elk/documentation/tooldevelopers/graphdatastructure/jsonformat.html)
// stdout: the same graph with x/y (relative to the parent) and width/height filled in
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

console.log = console.info = console.warn = (...a) => process.stderr.write(a.join(" ") + "\n");

const dir = process.argv[2];
if (!dir) {
  process.stderr.write("usage: node layout_elk.mjs <elkjs vendor dir>\n");
  process.exit(2);
}

const require = createRequire(import.meta.url);
const ELK = require(join(dir, "elk.bundled.js"));
const graph = JSON.parse(readFileSync(0, "utf8"));

try {
  const out = await new ELK().layout(graph);
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stderr.write(`ELK layout failed: ${e && e.message ? e.message : e}\n`);
  process.exit(1);
}
