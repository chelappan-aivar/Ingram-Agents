# `discovery_agent/runtime/` — orchestration and run state

Drives the pipeline and holds all per-run state. This is what both the CLI (`__main__.py`) and the AgentCore entry
point (`main.py`) call into.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package. |
| `orchestrator.py` | The orchestrator agent. Calls the seven tools strictly in order and writes `status.json` after each step. |
| `state.py` | `RunConfig` (parses/validates the invocation payload — engagement name, run id, S3 prefixes), `RunState` (shared state, step ordering/prerequisites, checkpoints to `work/`, and the `status.json` document), and `Stages` (the swappable set of analysis callables, stubbed in tests). The step order and prerequisites live here. |
