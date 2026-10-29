# `discovery_agent/tools/` — the orchestrator's seven tools

One file per tool. The orchestrator calls them **strictly in this order**; each checks its prerequisites, does its work,
writes a checkpoint, and updates `status.json`. Each tool is a thin wrapper — the real logic lives in `analysis/`.

Order: `load_documents → extract_explicit_needs → find_hidden_signals → merge_and_rank → write_narrative → render_outputs → publish_outputs`.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package and registers the tools. |
| `runner.py` | Shared safety + ordering logic every tool uses: prerequisite checks, checkpoint restore/save, status updates, error capture. |
| `load_documents.py` | **1.** Lists the input prefix, downloads and classifies each document, builds the corpus (trimming if over budget). |
| `extract_explicit_needs.py` | **2.** Sonnet pass — what the customers explicitly stated. |
| `find_hidden_signals.py` | **3.** Opus pass — what the documents imply but do not say. |
| `merge_and_rank.py` | **4.** Plain-code merge, evidence-check, de-dup, and ranking of all findings. |
| `write_narrative.py` | **5.** Haiku pass — short summaries (non-fatal: a failure here doesn't fail the run). |
| `render_outputs.py` | **6.** Renders `discovery.json`, `README.md`, `discovery_report.docx`, `narrative.json` into the work area. |
| `publish_outputs.py` | **7.** Publishes the rendered files to the `output/` prefix and finalises `status.json`. |
