# `discovery_agent/` — the agent package

The whole Discovery Agent: it reads one engagement's documents, runs two model passes over them, strips anything the
documents don't support, ranks the findings, and renders the report files. Pure Python; the only AWS-SDK-specific file
lives one level up (`main.py`, the AgentCore Runtime entry point).

The code is split so the **analysis core** (what to extract and how to rank) is independent of **where files come from**
(local disk vs S3) and **how the agent is driven** (CLI vs AgentCore Runtime).

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package. Empty. |
| `__main__.py` | Command-line entry point: `python -m discovery_agent <files> --engagement X --out DIR`. Runs the full pipeline locally without AWS. |
| `settings.py` | **The one place all configuration lives**, read from environment variables (`AWS_REGION`, `DATA_BUCKET`, `KMS_KEY_ID`, `MODEL_*`, optional `AWS_PROFILE` / `USE_S3_LOCALLY` / `LOCAL_ROOT` for development) plus fixed token limits. The runtime gets the variables from `../template.yaml`; nothing account-specific is hard-coded. |

## Subfolders

| Folder | What it holds |
|---|---|
| `analysis/` | The analysis core: the model passes (`pipeline.py`), result repair + ranking (`assemble.py`), schema validation, small-talk trimming, and transcript cleaning. |
| `data/` | Input and storage: reading/classifying documents (`documents.py`) and the local-disk/S3 storage abstraction (`storage.py`). |
| `rendering/` | Turning the report JSON into the `README.md` document and then the Word `.docx`. |
| `runtime/` | The orchestrator agent (`orchestrator.py`) and the shared run state with checkpoints and `status.json` (`state.py`). |
| `tools/` | The orchestrator's seven tools, one file per tool, plus `runner.py` (shared safety/ordering logic). |
| `prompts/` | The model prompts, one Markdown file per stage. |
