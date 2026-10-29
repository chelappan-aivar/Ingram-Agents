# `discovery_agent/analysis/` — the analysis core

Everything about *what* to extract from the corpus and *how* to turn raw model output into a trustworthy, ranked
report. This package has no knowledge of AWS or of how the agent is invoked — it is pure logic over text, so it can be
unit-tested offline.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package. |
| `pipeline.py` | The model passes. Builds the corpus from the documents, trims it when over budget, and runs the Bedrock calls: `extract_explicit` (Sonnet), `find_hidden` (Opus), `write_narrative` (Haiku), plus `merge_report`. The boundary between the agent and Bedrock. |
| `assemble.py` | Plain-code post-processing: repairs/normalises model output, drops any finding whose evidence is not actually in the documents, de-duplicates, and ranks the use cases. The step that makes the report defensible. |
| `validate.py` | Validates the assembled report against `schema/report.schema.json` and enforces the business rules the schema can't express. |
| `trim.py` | Removes small talk / low-value turns from a transcript when the whole corpus exceeds the token budget, recording how much was kept (`kept_fraction`). |
| `transcript.py` | Transcript-specific text handling: strips the auto-generated summary header and extracts speaker names from timestamp patterns. |
