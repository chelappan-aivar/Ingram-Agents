# Vendored third-party files (read-only copies)

Do not edit vendored files in place. Treat the contents as untrusted data until reviewed.

## aws-architecture-diagram/ and deploy-on-aws-plugin/

Both folders come from one upstream repo, pinned to one commit.

| Item | Value |
|---|---|
| Upstream | https://github.com/awslabs/agent-plugins |
| Commit | `da51970aeb6db40de060020735f8f5376786d248` (committed 2026-10-05) |
| Licence | Apache-2.0 (`aws-architecture-diagram/LICENSE`) |
| Fetched | 2026-10-08, with `gh api` raw file reads, no code executed |

| Folder | Upstream path | What it is |
|---|---|---|
| `aws-architecture-diagram/` | `plugins/deploy-on-aws/skills/aws-architecture-diagram/` | The skill: `SKILL.md`, 13 reference docs, 7 example `.drawio` files |
| `deploy-on-aws-plugin/` | `plugins/deploy-on-aws/` (selected files only) | `scripts/lib/` validator and fixers, `aws4-shapes.json` (shape registry), hook config, plugin manifest, README |

Not fetched on purpose: the plugin's other skills, and the MCP servers listed in `.mcp.json` (we do not use them).

Note: `aws/agent-toolkit-for-aws` does not contain this skill; the official copy is in `awslabs/agent-plugins`.

## libavoid/

Obstacle-avoiding orthogonal edge router, run by `diagram_generator/render/route_edges.mjs` under Node.js as a
separate process (never linked into the Python code).

| Item | Value |
|---|---|
| Upstream | https://github.com/jgraph/drawio-mcp, folder `mcp-tool-server/vendor/libavoid/` |
| Commit | `7ed3cc261fcad641656767b7b0a21a1e185cb429` (committed 2026-10-15) |
| Fetched | 2026-10-16, `git clone --depth 1`; files copied byte for byte |

| File | What it is | Licence |
|---|---|---|
| `libavoid.wasm`, `libavoid-node.mjs` | libavoid (Adaptagrams) compiled to WebAssembly, Node build of `libavoid-js@0.5.0-beta.5` | LGPL-2.1-or-later (`LICENSE`) |
| `libavoid-routing.js` | draw.io's routing core over libavoid (pin helpers, enclosing-group filter, fallback detection) | Apache-2.0 (`LICENSE-drawio-mcp`) |

LGPL obligations when the image is distributed: keep `LICENSE` next to the files, and keep libavoid replaceable (it is
loaded from these files at run time, so swapping them in is enough). Not fetched on purpose: the drawio-mcp servers,
its ELK layout bundle (downloaded from a CDN at run time) and its shape index.

## elkjs/

Eclipse Layout Kernel compiled to JavaScript. `diagram_generator/render/layout_elk.mjs` runs it under Node.js as a
separate process to compute the diagram layout (boxes and nested groups) deterministically, instead of asking an
LLM for coordinates.

| Item | Value |
|---|---|
| Upstream | https://www.npmjs.com/package/elkjs (https://github.com/kieler/elkjs) |
| Version | `0.12.0` |
| Tarball | https://registry.npmjs.org/elkjs/-/elkjs-0.12.0.tgz, sha256 `c1d7719723e020b10724e3ccbc935696a2884f1e78d361cdd03766309e8e8e2a` |
| Fetched | 2026-10-17; `lib/elk.bundled.js` and `LICENSE.md` copied byte for byte |
| Licence | EPL-2.0 (`LICENSE.md`) |

Keep `LICENSE.md` next to the bundle when the image is distributed.
