# ADR: diagram_generator, a 3-LLM architecture agent (Extractor → Assumer → Diagram coder)

Status: **accepted** (2026-10-08). Owner: Solution Architecture agent team (Aivar, for Ingram). This record is authoritative for the v2 build. Change it only with the owner's approval.

## Context

The first version worked this way: one LLM pass makes a spec, a second writes `diagrams` Python, and Graphviz renders PNG/SVG. After research the agent was reframed around three LLM steps with code "harnesses" between them, an editable draw.io output, and three explicit audit lists (contradictions, assumptions, unresolved). v2 is a **new sibling folder** that reuses v1's proven plumbing (copied, not imported) and replaces the spec/diagram core. The input is our assumed ADR: one or more documents that describe components and relationships. It is local-first with no frontend. v1 stays untouched, and **no Graphviz fallback is built in v2 unless the owner asks for it**.

## Decisions

- **Extractor (LLM 1):**
  - Lists components first, then relationships, separately.
  - Every item carries `source`/`target` (for relationships), the `file`, the exact `quote`, and a one-sentence `reasoning`.
  - It also emits contradictions, using these rules:
    - stated vs stated, or inferred vs inferred, of similar strength: record a contradiction and remove both items from the lists.
    - stated vs inferred: the stated one wins. No contradiction is recorded, but the resolution is logged.
- **Assumer (LLM 2):**
  - Adds an item only if three things hold:
    - without it, an existing part becomes meaningless;
    - it fits the business or problem context;
    - a signal or quote supports it.
  - Each addition becomes an assumption with reasoning and a quote.
  - A critical gap with no signal at all becomes an `unresolved` entry, with reasoning and a one-sentence suggestion.
- **Diagram coder (LLM 3):** a one-shot run using the AWS `aws-architecture-diagram` skill (awslabs/agent-plugins, Apache-2.0, vendored in `vendor/`). The skill is edited to fit a non-interactive pipeline, keeping its clean-draw.io know-how. Output is draw.io XML, using the AWS4 icons that draw.io draws itself.
- **Diagram coder revision (2026-10-08, after the first real runs):**
  - **Problem:** the coder used to write the complete draw.io XML in one answer. For a 35-component architecture that hit the output limit after 7 minutes, and Opus 5.5's always-on thinking also counts against that limit.
  - **What changed:** the coder now returns only a layout through the `submit_layout` tool: box positions, group rectangles, and edge sides and waypoints. Its prompt (`prompts/layout_system.md`) carries the skill's layout, spacing, routing and placement rules. Code then builds the XML with the skill's exact styles (`render/aws_styles.py`, `render/drawio_builder.py`).
  - **Result:** the output is roughly a tenth of the size. The XML can't be malformed or drop a spec item. Harness 3 now checks the layout geometry first: overlaps, boxes outside their boundary or inside a wrong group, group nesting, and edges through boxes. It sends concrete errors back for up to 2 retries.
  - **Why not the draw.io MCP:** every variant of the official draw.io MCP server only accepts a complete diagram (`create_diagram`, `open_drawio_xml`), so it would not shrink the output. Its `search_shapes` duplicates our local AWS4 shape registry, and the hosted endpoint would receive customer architecture.
- **Edge routing revision (2026-10-16, owner-approved):**
  - **Problem:** the model placed line sides and waypoints itself. On the 11 saved real runs, its lines went through boxes 155 times and overlapped each other for 34,587 px in total.
  - **What changed:** the model returns only box and group positions. The vendored libavoid router (from jgraph/drawio-mcp, the same router the draw.io editor uses; `vendor/libavoid/`, LGPL-2.1, run as a separate Node.js process) routes every line on the finished file. `EdgeEnd`, `LayoutEdge` and `Layout.edges` are removed from the contract, and the "edge runs through a box" layout check is gone. `fit_groups` grows a group around a member that pokes out of it, before the layout checks, instead of spending a retry on it.
  - **Result on the same 11 runs:** 2 lines through boxes (both from one layout with overlapping boxes), 208 crossings instead of 484, 790 px of overlap.
  - **Not adopted from drawio-mcp:** the MCP server itself (one function call needs no protocol; the hosted endpoint would receive customer architecture), its ELK auto-layout (CDN download at run time; it would discard the boundary placement), its XML repair (our XML is built by code) and its shape index (duplicates the catalog).
  - **New requirement:** Node.js 18+ wherever the pipeline runs (installed in the Dockerfile). Without it the diagram is still produced with draw.io's own routing, and Harness 3 records a warning.
  - **Open:** edge labels sit at each line's midpoint and are not yet moved off boxes or other labels.
- **Current model setting (owner decision, 2026-10-08):** all three steps run on **Sonnet 4.6** until the owner says to switch back. Opus 5.5 spent more than 7 minutes thinking on the 36-component layout at its default effort. The Opus 5.5 support stays in place and is tested: auto tool choice, and per-step effort through `output_config.effort` (coder "low"). Switching back means changing `EXTRACTOR_MODEL_ID`, `ASSUMER_MODEL_ID` and `CODER_MODEL_ID` to `us.anthropic.claude-opus-5-5`.
- **Opus 5.5 tool use:** Opus 5.5 rejects forced tool use (`tool_choice` "tool"/"any") because adaptive thinking is always on. For models listed in `auto_tool_choice_models`, the client sends `tool_choice: auto` and tells the model in the system prompt to call the tool. If the model answers in text, one follow-up turn asks for the call. If it still doesn't call the tool, the Sonnet 4.6 fallback (which supports forced tool use) handles that call.
- **Harness:** runs after each LLM step (not before the Extractor). It counts what went in and what came out, and logs it.
- **Live status:** shows which step is running, plus basic counts.
- **Vocabulary cross-check:** a one-time Sonnet subagent checks the component/relationship keyword library against the AWS4 draw.io shapes, once everything is in place.
- **Altitude:** high-level components only (see the altitude line below).
- **Outputs:** PNG, SVG, `.drawio`, assumptions, unresolved items, contradictions.
- **Inputs:** multiple files per run (PDF, DOCX, TXT, MD).
- **Model:**
  - Default is Opus 5.5 on Bedrock (`us.anthropic.claude-opus-5-5`, us-east-1) for all three steps, configurable per step. It sends **no `temperature`**, because the model rejects it.
  - **Fallback is Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`). It is used when an Opus call fails with a model, access or availability error, or still fails after retries. Each fallback is recorded in the metadata and logs.
  - Credentials come from `AWS_PROFILE=jatin` (SSO).
- **SVG:** forced to light mode (`color-scheme: light` and a white background injected). The coder uses plain light colours, not the skill's `light-dark()` variants, since nothing in the pipeline needs them. Icon and container colours come from the catalog and match the vendored `fix_icon_colors` tables, so the fixer never recolours them.
- **Diagram content:** only what makes a clean, readable one-shot diagram. That is a title, groups, icons and short edge labels. No numbered step badges and no sidebar legend.
- **Git:** local commits on branch `mvp-v2` in this worktree, no push until the owner says so.
- **Future, not built:** a different colour for assumed items (`Further_possibilities.md`).

## Pipeline

```
files (1..N: PDF/DOCX/TXT/MD)
  │ INGEST + TEXT (code, reused from v1)          → sources.json  {file, text, chars}
  ▼
[LLM 1 EXTRACTOR] forced tool `submit_extraction`
  ▼ HARNESS 1 (code): schema valid · ids unique · every quote found in its file · relationship ends exist
  │                   · contradiction rule enforced (contradicted ids absent from lists) · counts logged
  │                   → extraction.json, contradictions.json
  ▼
[LLM 2 ASSUMER] forced tool `submit_assumptions` (gets docs + extraction; may only ADD)
  ▼ HARNESS 2 (code): no extracted item changed/removed · each assumption names the existing ids it
  │                   supports · quote verified · merged spec counts = extracted + assumed · cap warning
  │                   → assumptions.json, unresolved.json, spec.json (final, every item tagged
  │                     origin = stated | inferred | assumed)
  ▼
[LLM 3 DIAGRAM CODER] text → one .drawio XML (edited skill = system prompt; spec + icon map = user msg)
  ▼ HARNESS 3 (code): vendored post_process_drawio.py fixers → vendored validate_drawio.py
  │                   → parse cells: every spec component id present as a cell id, every relationship
  │                     present as an edge (source/target ids) · extras listed (decor excluded)
  │                   · on failure retry LLM 3 with the errors (max 2 retries) · counts logged
  ▼
EXPORT (code): drawio CLI → diagram.png, diagram.svg (inject color-scheme: light) ; keep diagram.drawio
  ▼
PACKAGE: report.json (all harness counts + status), metadata.json, harness.log.jsonl
```

**Status display:** this reuses v1's event bus. Each step emits `started`/`completed` with counts, for example "Extractor: 14 components, 19 relationships, 2 contradictions". The CLI prints these live, and the API streams them over SSE.

## Data contracts (`diagram_generator/contracts.py`, pydantic)

- **`Evidence`:** `{file, quote, reasoning}`.
- **`Component`:**
  - `{id, name, service, kind (aws_service|external|actor|generic|data_store), boundary?, strength (stated|inferred), evidence[]}`
  - `service` is a canonical name from the catalog, or null.
- **`Boundary`:** `{id, type (aws_cloud|region|vpc|az|subnet_public|subnet_private|account|on_prem|external|generic), name, parent?, evidence[]}`.
- **`Relationship`:** `{id, source, target, label (≤4 words), direction, strength, evidence[]}`.
- **`Contradiction`:** `{subject, claim_a{evidence,strength}, claim_b{evidence,strength}, removed_ids[], note}`.
- **`Extraction`:** `{title, components[], relationships[], boundaries[], contradictions[], resolutions[]}`. `resolutions` logs each stated-beats-inferred case.
- **`Assumption`:** `{item (Component|Relationship|Boundary), supports_ids[], reasoning, evidence[]}`.
- **`Unresolved`:** `{about, reasoning, suggestion}`.
- **`FinalSpec`:** the components, relationships and boundaries, each with `origin`, plus a title. This is what LLM 3 receives.

## Altitude line (in prompts 1–3, enforced in Harness 1)

| In scope | Out of scope |
|---|---|
| A component is an AWS service or a named external system, at service level ("RDS PostgreSQL", "Application Load Balancer") | Instance types, sizes, counts, ports, CIDRs, IAM policies, settings, versions |
| Boundaries only when the doc names them (VPC, subnet, AZ, region, account, on-prem) | Inventing AZs or subnets for "best practice" |
| Relationships are data or control flow, with a ≤4-word verb label | Protocol detail unless stated |
| The Assumer adds only structural necessities | "Good-practice" extras (WAF, KMS, CloudWatch…) unless the doc signals them |

Harness 1 warns when the count exceeds 40 components. The skill's "Auxiliary services" group holds monitoring and IAM items.

## Folder layout

```
adr_generator/        ADR pipeline (SRS -> adr.md + spec.json)
diagram_generator/    this pipeline
  config.py  contracts.py  pipeline.py  jobs.py  stages.py  cli.py
  ingest/      validate.py, extractors.py (multi-file)
  llm/         bedrock.py (make_llm), extractor.py, assumer.py, layout_coder.py
  harness/     h1_extraction.py, h2_assumptions.py, h3_drawio.py, layout_check.py, quotes.py, log.py
  catalog/     components.yaml, relationships.yaml, loader.py
  render/      drawio_builder.py, aws_styles.py, drawio_tools.py (fixers + validator wrappers, CLI export, light-mode SVG)
  prompts/     extractor_system.md, assumer_system.md, layout_system.md
architecture_agent/   shared Strands LLM layer (llm.py) and the AgentCore entrypoint (agent.py)
vendor/               skill + plugin scripts, read-only, SOURCE.md
tests/diagram_generator/   unit + FakeLLM pipeline tests, fixtures
```

## Verification

- **Tests:** `python -m pytest` (repository root) must be green. That covers harness rules, FakeLLM end-to-end and retry and fallback paths, and the parser on the vendored `.drawio` examples.
- **Toolchain:** the vendored validator passes on the generated `.drawio`. `drawio -x` produces a non-empty PNG and SVG. The SVG has `color-scheme: light` and no local file references.
- **Real runs:** the fleet-telemetry PDF and the 5 fixtures.
  - **Check:** all six outputs exist, Harness 3 covers 100% of spec components, and the counts in `harness.log.jsonl` reconcile.
  - **Review:** `e_scanned` should fail cleanly, and `d_vague` should produce unresolved items.
- **Live status:** visible in the CLI during a real run.

## Risks

- **LLM-written layout** may overlap. The skill's rules and fixers are the only mitigation, since no fallback renderer is built by decision.
- **Cost:** 3 Opus calls per run with about 40 KB of skill prompt. Bedrock prompt caching is a later option.
- **Forced tool calls and large `maxTokens` on Opus 5.5** are untested, so they are verified first in WP6. The Sonnet 4.6 fallback covers availability failures.
- **Vendored fixers and validator run unmodified,** as subprocesses in the venv. If one misbehaves, we wrap it rather than edit `vendor/`.
