# ADR Generator

One node of the Architecture agent. It reads a Software Requirements Specification (SRS) and produces a low-level Architecture Decision Record (`adr.md`), with every AWS service named exactly (instance family and type, engine and version, storage), plus `spec.json` for the cost-calculator agent. `adr.md` is the input to the diagram node (`../diagram_Generator`).

```
SRS ─► one Claude Sonnet 4.6 call on Amazon Bedrock (forced tool `submit_design`) ─► Design ─► adr.md + spec.json
```

The model is guided by four prompt files in `adr_generator/prompts/`, assembled in this order:

| File | What it holds |
|---|---|
| `system.md` | Role, procedure and hard rules (single Region and AZ, exact configurations, defaults marked as assumed, no usage figures) |
| `skill.md` | Architecture practice: requirements to decisions, service selection, security baseline, single-AZ rules, right-sizing, ADR writing (~1,700 tokens) |
| `services.md` | Service sheet: about 40 services with exact names, default and step-up configurations (~1,800 tokens) |
| `example.md` | A worked example of the expected precision |

Where the guidance comes from: `SOURCES.md`. The output formats: `HANDOFF.md`.

## Run

From the repository root (see the root `README.md` for setup):

```bash
.venv/bin/python -m pytest                     # no AWS needed
aws sso login --profile <your-profile>
AWS_PROFILE=<your-profile> .venv/bin/python -m adr_generator.cli docs/adr_generator/samples/srs_billing_dispute_assistant.md --out ./data
```

The output goes to `data/runs/<run_id>/`: `adr.md`, `spec.json`, `design.json` (the raw tool input), `metadata.json` (model, tokens, timing, counts), and `input/`.

Then draw the diagram with the diagram pipeline:

```bash
.venv/bin/python -m diagram_generator.cli data/runs/<run_id>/adr.md --out ./data
```

## Settings

Environment variables, or a `.env` file in the repository root:

| Variable | Default |
|---|---|
| `AWS_REGION` | `us-east-1` |
| `AWS_PROFILE` | none (default credential chain) |
| `MODEL_ID` | `us.anthropic.claude-sonnet-4-6` |
| `FALLBACK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` (same as primary, so no fallback) |
| `MAX_TOKENS` | `24000` |
| `MAX_TEXT_CHARS` | `150000` |
| `DATA_DIR` | `./data` (repository root) |

## Layout

| Path | What |
|---|---|
| `adr_generator/generator.py` | Prompt assembly, the `submit_design` tool schema, the single call |
| `adr_generator/contracts.py` | `Design` and its parts; lenient, so a sparse answer still renders |
| `adr_generator/render/` | `adr_md.py`, `spec_json.py` (deterministic, never raise) |
| `adr_generator/llm/bedrock.py` | `make_llm`; the Strands-based LLM layer is `architecture_agent/llm.py` |
| `adr_generator/ingest.py` | Reads the SRS (.md, .txt, .docx, .pdf with a text layer) |
| `adr_generator/cli.py` | CLI with live status |
| `docs/adr_generator/samples/` | Sample SRS files |
