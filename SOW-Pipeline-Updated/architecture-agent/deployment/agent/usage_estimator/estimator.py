"""The single LLM step: SRS text + Design -> `submit_usage` tool call (raw dict).

One forced tool call through the shared Strands layer (architecture_agent/llm.py), like the ADR Generator. The tool
schema is built from registry.py, so the model fills exactly the cost-calculator fields for each service. The system
prompt is usage_estimator/prompts/usage_system.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from adr_generator.config import Settings
from adr_generator.contracts import Design
from adr_generator.llm.bedrock import LLMClient, user_msg
from adr_generator.availability import AvailabilityPolicy
from usage_estimator.registry import AZ_FIELDS, DERIVED, SERVICES

TOOL_NAME = "submit_usage"
PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "usage_system.md"
NO_USAGE_CATEGORIES = {"actor", "external"}

_S = {"type": "string"}
_SL = {"type": "array", "items": {"type": "string"}}
_JSON_TYPES = {"integer": {"type": "integer"}, "number": {"type": "number"}, "boolean": {"type": "boolean"},
               "string": {"type": "string"}, "list": _SL}


def _entry_schema(service) -> dict:
    """One usage entry of `service`: component_id, the schema fields (minus the ones code sets), explanation."""
    props: dict = {"component_id": {"type": "string", "description": "The design component id, e.g. C-07."}}
    for f in service.fields:
        if f.name not in DERIVED and f.name not in AZ_FIELDS:
            props[f.name] = {**_JSON_TYPES[f.type], "description": f.hint}
    props["explanation"] = {"type": "string", "description": "1-3 sentences: how the usage figures were reached, "
                                                             "with the arithmetic and the profile drivers used."}
    props["assumed_fields"] = {**_SL, "description": "Fields whose value is an assumption, not from the SRS or "
                                                     "the design."}
    return {"type": "object", "properties": props, "required": list(props)}


def build_tool_spec() -> dict:
    driver = {"type": "object", "properties": {
        "name": {"type": "string", "description": "e.g. 'Monthly active users', 'Disputes per day'."},
        "value": {"type": "string", "description": "The number, e.g. '12000'."},
        "unit": {"type": "string", "description": "e.g. 'users', 'per day', 'tokens per call'."},
        "basis": {"type": "string", "enum": ["stated", "inferred", "assumed"]},
        "reason": {"type": "string", "description": "The SRS requirement or quote, the arithmetic, or why this "
                                                    "typical value was assumed."},
    }, "required": ["name", "value", "unit", "basis", "reason"]}
    usage = {"type": "object", "description": "Entries per service; include only services the design uses.",
             "properties": {s.key: {"type": "array", "description": s.name, "items": _entry_schema(s)}
                            for s in SERVICES}}
    schema = {"type": "object", "properties": {
        "workload_profile": {"type": "array", "items": driver},
        "usage": usage,
        "warnings": {**_SL, "description": "Plain sentences the reader must check; empty when nothing to flag."},
        "notes": {**_SL, "description": "Your reasoning in a few short sentences."},
    }, "required": ["workload_profile", "usage", "warnings", "notes"]}
    return {"toolSpec": {"name": TOOL_NAME,
                         "description": "Submit the expected monthly usage of every AWS component in the design.",
                         "inputSchema": {"json": schema}}}


def build_system_prompt(design: Design | None = None) -> str:
    """The static prompt, then the availability rule read off the design being estimated.

    Taking the policy from the design rather than from settings means a design that was revised
    to more or fewer zones is estimated against the zones it actually has.
    """
    parts = [PROMPT_FILE.read_text(encoding="utf-8").strip()]
    policy = AvailabilityPolicy.from_design(design) if design is not None else AvailabilityPolicy()
    parts.append(policy.usage_rule())
    return "\n\n---\n\n".join(parts)


def design_for_prompt(design: Design) -> dict:
    """The parts of the design that drive usage: volumes in requirements and assumptions, the components with
    their configuration, and the flows between them."""
    return {
        "title": design.title, "region": design.region, "availability_zones": design.availability_zones,
        "requirements": [{"id": r.id, "text": r.text, "kind": r.kind} for r in design.requirements],
        "components": [{"id": c.id, "name": c.name, "service": c.service, "category": c.category, "role": c.role,
                        "configuration": c.configuration, "sku_summary": c.sku_summary, "quantity": c.quantity,
                        "purpose": c.purpose} for c in design.components],
        "relationships": [{"id": r.id, "source": r.source, "target": r.target,
                           "sentence": r.sentence or r.label} for r in design.relationships],
        "assumptions": [{"id": a.id, "text": a.text} for a in design.assumptions],
    }


def build_user_message(srs_name: str, srs_text: str, design: Design) -> str:
    design_json = json.dumps(design_for_prompt(design), indent=1, ensure_ascii=False)
    return (f'<document file="{srs_name}">\n{srs_text}\n</document>\n\n'
            f"<design>\n{design_json}\n</design>\n\n"
            f"The document above is the Software Requirements Specification (SRS) and the design was made from "
            f"it. Estimate the expected monthly usage of every AWS component in the design and call the "
            f"`{TOOL_NAME}` tool exactly once.")


def estimate(llm: LLMClient, settings: Settings, design: Design, srs_name: str, srs_text: str) -> dict:
    """One forced tool call. Returns the raw tool input (saved as usage.raw.json)."""
    return llm.converse_tool(build_system_prompt(design),
                             [user_msg(build_user_message(srs_name, srs_text, design))],
                             build_tool_spec(), settings.max_tokens)
