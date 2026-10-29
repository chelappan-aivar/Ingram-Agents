# `schema/` — the report contract

The JSON Schema the discovery report must satisfy. It is the contract between the analysis code and every consumer
(the renderer, downstream agents, reviewers), and it ships inside the runtime package so the agent can validate its own
output before publishing.

## Files

| File | Purpose |
|---|---|
| `report.schema.json` | JSON Schema for `discovery.json`: the shape of use cases, evidence/quotes, confidence levels, hidden opportunities, and open questions. `discovery_agent/analysis/validate.py` checks every report against it; `scripts/package_agent.sh` copies it next to the agent code in the zip. |
