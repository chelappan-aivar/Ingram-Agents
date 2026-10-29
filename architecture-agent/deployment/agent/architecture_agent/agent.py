"""AgentCore Runtime entrypoint. Every model call goes through the Strands Agents SDK (architecture_agent/llm.py);
the pipelines are in adr_generator/, usage_estimator/ and diagram_generator/, the actions in actions.py.

Request payload (JSON). `action` picks what to do; without it the whole pipeline runs (SRS in, ADR and diagram out).

  {"action": "extract_srs",      "srs_base64": "...", "srs_filename": "requirements.pdf"}     (or "srs_text")
        -> {"status", "srs_text", "filename", "truncated"}
  {"action": "generate_adr",     "srs_text": "...", "srs_filename": "srs.md", "scope": {...}}
        -> {"status", "run_id", "design", "adr_md", "spec_json", "scope", "scope_warnings", "counts", "tokens", ...}
  {"action": "revise_adr",       "run_id": "...", "design": {...}, "srs_text": "...", "instruction": "...",
                                 "scope": {...}}
        -> same shape as generate_adr
  {"action": "estimate_usage",   "run_id": "...", "design": {...}, "srs_text": "...", "srs_filename": "srs.md"}
        -> {"status", "run_id", "usage_json", "usage_assumptions_md", "counts", "tokens", "seconds", ...}
        The UI calls it silently after generate_adr and after every revise_adr.
  {"action": "generate_diagram", "adr_md": "...", "stream": false}
        -> {"status", "job_id", "diagram": {png_base64, svg_base64, drawio_xml, report, assumptions, unresolved,
            contradictions}, "diagram_metadata", "report_status"}
        With "stream": true the reply is server-sent events: {"event": "progress", stage, kind, message, counts,
        progress} for each pipeline event, then {"event": "result", ...} with the body above.
  {"srs_text": "..." | "srs_base64": "...", "srs_filename": "srs.md", "scope": {...}}
        (no action: ADR, usage and diagram in one call)
        -> {"status", "run_id", "adr": {adr_md, spec_json, design}, "adr_metadata", "scope", "scope_warnings",
            "usage": {status, usage_json, usage_assumptions_md, usage_metadata}, "job_id", "diagram", ...}

`scope` is the agreed in-scope / out-of-scope split from the signed SOW:
  {"in_scope": ["..."], "out_of_scope": ["..."], "source": "sow:OPP-2026-0517"}
It is optional but expected in the SOW-first pipeline, because the architect settles the service
decisions and the scope split while writing the SOW. It binds the design (see adr_generator/scope.py)
and the reply's "scope_warnings" lists any component that still looks out of scope. Bare "in_scope" /
"out_of_scope" keys are accepted instead of the nested object.

How many Availability Zones the design spans is set by AVAILABILITY_ZONES (default 2, the
Well-Architected Reliability baseline) and flows into the design, usage.json and the reports
together, so the diagram and the price always agree. See adr_generator/availability.py.

Every reply has "status": "completed" or "failed"; a failure names the "stage" (request, ingest, adr, usage, diagram) and
gives "error" and, for pipeline runs, a "log_tail". Nothing persists between invocations.
"""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from architecture_agent.actions import dispatch

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict):
    return dispatch(payload or {})


if __name__ == "__main__":
    app.run()
