# `discovery_agent/prompts/` — model prompts

The prompt for each model stage, one Markdown file per stage. Keeping them as separate files (rather than inline
strings) makes them easy to review and tune without touching code. `analysis/pipeline.py` loads them by name.

## Files

| File | Used by | Purpose |
|---|---|---|
| `extraction.md` | `extract_explicit_needs` (Sonnet) | Extract what the customers explicitly stated, with verbatim evidence. |
| `hidden_signals.md` | `find_hidden_signals` (Opus) | Surface what the documents imply but do not say — marked as hypotheses. |
| `narrative.md` | `write_narrative` (Haiku) | Produce short plain-language summaries of the findings. |
| `trim.md` | corpus trimming (Haiku) | Decide which transcript turns are small talk and can be dropped when over budget. |
| `orchestrator.md` | the orchestrator (Haiku) | System prompt that keeps the agent calling the seven tools in order. |
