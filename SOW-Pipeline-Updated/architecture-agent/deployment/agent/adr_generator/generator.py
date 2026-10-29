"""The single LLM step: SRS text -> `submit_design` tool call -> Design.

The system prompt is assembled from four files in adr_generator/prompts/, in this order:
  system.md (role, procedure, hard rules) · skill.md (architecture practice) · services.md (service sheet) · example.md
"""
from __future__ import annotations

from pathlib import Path

from adr_generator.availability import AvailabilityPolicy
from adr_generator.config import Settings
from adr_generator.contracts import Design
from adr_generator.llm.bedrock import LLMClient, user_msg
from adr_generator.scope import Scope

TOOL_NAME = "submit_design"
PROMPT_FILES = ["system.md", "skill.md", "services.md", "example.md"]

_S = {"type": "string"}
_SL = {"type": "array", "items": {"type": "string"}}


def build_tool_spec() -> dict:
    """Field order follows the reasoning order the prompt asks for: requirements -> decisions -> boundaries ->
    components -> relationships."""
    requirement = {"type": "object", "properties": {
        "id": {"type": "string", "description": "REQ-01, REQ-02 ..."},
        "text": {"type": "string", "description": "The requirement in one sentence, close to the SRS wording."},
        "kind": {"type": "string", "enum": ["functional", "non_functional", "constraint"]},
    }, "required": ["id", "text", "kind"]}
    decision = {"type": "object", "properties": {
        "id": {"type": "string", "description": "ADR-001, ADR-002 ..."},
        "title": {"type": "string", "description": "Imperative title naming the choice, e.g. 'Use Amazon RDS for PostgreSQL for case data'."},
        "status": {"type": "string", "enum": ["Proposed", "Accepted"]},
        "context": {"type": "string", "description": "2-3 sentences: the forces and requirements behind this decision."},
        "decision": {"type": "string", "description": "2-3 sentences stating exactly what is chosen, including the SKU."},
        "consequences": {**_SL, "description": "2-4 short items, positive and negative."},
        "rejected_alternatives": {"type": "array", "items": {"type": "object", "properties": {
            "option": _S, "reason": _S}, "required": ["option", "reason"]}},
        "requirement_ids": _SL,
        "component_ids": _SL,
    }, "required": ["id", "title", "context", "decision", "consequences"]}
    boundary = {"type": "object", "properties": {
        "id": {"type": "string", "description": "snake_case, unique, e.g. main_vpc, private_subnet_a"},
        "type": {"type": "string", "enum": ["region", "vpc", "az", "subnet_public", "subnet_private",
                                            "on_prem", "external", "generic"]},
        "name": {"type": "string", "description": "Display name, e.g. 'us-east-1a' or 'Private subnet (us-east-1a)'."},
        "parent": {"type": ["string", "null"], "description": "Parent boundary id, or null for the outermost."},
    }, "required": ["id", "type", "name"]}
    component = {"type": "object", "properties": {
        "id": {"type": "string", "description": "C-01, C-02 ..."},
        "name": {"type": "string", "description": "Display name, 1-4 words, unique."},
        "service": {"type": "string", "description": "Exact AWS service name from the service sheet, or the external system / actor name."},
        "category": {"type": "string", "enum": ["actor", "external", "networking", "compute", "containers",
                                                "database", "storage", "integration", "ai_ml", "analytics",
                                                "security", "management"]},
        "role": {"type": "string", "description": "What it does in this solution, one short phrase."},
        "configuration": {"type": "object", "additionalProperties": {"type": "string"},
                          "description": "Exact provisioned configuration as key/value strings, e.g. "
                                         "instance_type, instance_family, vcpu, memory_gib, engine, engine_version, "
                                         "deployment, storage_type, storage_gb, iops, capacity, os, tier. Empty for actors/external."},
        "sku_summary": {"type": "string", "description": "One line, e.g. 'db.t4g.medium, PostgreSQL 16.4, gp3 100 GiB, Single-AZ'."},
        "quantity": {"type": "integer", "minimum": 1},
        "boundary": {"type": ["string", "null"], "description": "Innermost boundary id, or null for Region-level, global or external."},
        "purpose": {"type": "string", "description": "One sentence, why it exists."},
        "assumed": {"type": "boolean", "description": "true when the SRS does not state this component or its configuration."},
        "assumption_reason": {"type": "string", "description": "One line, required when assumed is true."},
        "requirement_ids": _SL,
        "decision_ids": _SL,
    }, "required": ["id", "name", "service", "category", "configuration", "sku_summary", "quantity", "purpose", "assumed"]}
    relationship = {"type": "object", "properties": {
        "id": {"type": "string", "description": "F-01, F-02 ... in request/data-flow order."},
        "source": {"type": "string", "description": "Component id."},
        "target": {"type": "string", "description": "Component id."},
        "label": {"type": "string", "description": "Verb phrase of at most 4 words, e.g. 'writes case records'."},
        "sentence": {"type": "string", "description": "One plain sentence that names both components by their display names."},
        "protocol": _S,
        "port": _S,
    }, "required": ["id", "source", "target", "label", "sentence"]}
    assumption = {"type": "object", "properties": {"id": _S, "text": _S, "reason": _S}, "required": ["id", "text", "reason"]}
    schema = {"type": "object", "properties": {
        "title": {"type": "string", "description": "Solution name, e.g. 'Billing Dispute Assistant'."},
        "customer": _S,
        "context": {"type": "string", "description": "One paragraph: business problem, scope and key constraints from the SRS."},
        "region": {"type": "string", "description": "AWS Region code, e.g. us-east-1."},
        "availability_zones": {**_SL, "description": "Every AZ the workload spans, in order, e.g. "
                                                     "['us-east-1a', 'us-east-1b']. Follow the availability "
                                                     "model in the system prompt. Empty only if no VPC is used."},
        "requirements": {"type": "array", "items": requirement},
        "decisions": {"type": "array", "items": decision},
        "boundaries": {"type": "array", "items": boundary},
        "components": {"type": "array", "items": component},
        "relationships": {"type": "array", "items": relationship},
        "security_operations": {**_SL, "description": "Plain sentences, each naming the components it applies to."},
        "assumptions": {"type": "array", "items": assumption},
        "open_questions": _SL,
    }, "required": ["title", "context", "region", "requirements", "decisions", "boundaries", "components",
                    "relationships", "security_operations", "assumptions"]}
    return {"toolSpec": {"name": TOOL_NAME,
                         "description": "Submit the complete low-level architecture design for the SRS.",
                         "inputSchema": {"json": schema}}}


def build_system_prompt(settings: Settings, scope: Scope | None = None) -> str:
    """The static prompt files, then the policies that are configuration rather than prose.

    The availability model and the SOW scope are appended last so they win over anything the
    static files imply, and so neither has to be duplicated across the prompt sheets.
    """
    parts = []
    for name in PROMPT_FILES:
        path = Path(settings.prompts_dir) / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8").strip())
    parts.append(AvailabilityPolicy.from_settings(settings).design_rule())
    if scope:
        parts.append(scope.design_rule())
    return "\n\n---\n\n".join(parts)


def build_user_message(srs_name: str, srs_text: str, scope: Scope | None = None) -> str:
    scope_block = f"{scope.prompt_block()}\n\n" if scope else ""
    scope_note = (" The `<scope>` block is the in-scope / out-of-scope split agreed in the Statement of Work; "
                  "honour it exactly.") if scope else ""
    return (f'<document file="{srs_name}">\n{srs_text}\n</document>\n\n'
            f"{scope_block}"
            f"The document above is the Software Requirements Specification (SRS).{scope_note} Design the "
            f"solution it describes and call the `{TOOL_NAME}` tool exactly once with the complete design.")


def generate(llm: LLMClient, settings: Settings, srs_name: str, srs_text: str,
             scope: Scope | None = None) -> tuple[Design, dict]:
    """One forced tool call. Returns the validated Design and the raw tool input (saved as design.json)."""
    raw = llm.converse_tool(build_system_prompt(settings, scope),
                            [user_msg(build_user_message(srs_name, srs_text, scope))],
                            build_tool_spec(), settings.max_tokens)
    return Design.model_validate(raw), raw
