"""Structured Claude calls through Strands Agents.

Each call is a single-turn Strands `Agent` (BedrockModel, temperature 0, no tools) with a Pydantic
output model built from the JSON schema; Strands offers it as a tool (forced from the first turn,
see ForcedToolModel) and validates the reply, asking the model to fix it when validation fails. The run's bedrock-runtime client is shared by
every model instance (boto3 clients are thread-safe), so retries and timeouts stay as configured.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, create_model
from strands import Agent
from strands.models import BedrockModel

_SCALARS = {"string": str, "number": float, "integer": int, "boolean": bool}


def _type(schema: dict[str, Any], name: str) -> Any:
    kind = schema.get("type")
    if isinstance(kind, list):
        nullable, rest = "null" in kind, [k for k in kind if k != "null"]
        inner = _type({**schema, "type": rest[0]}, name)
        return Optional[inner] if nullable else inner
    if "enum" in schema:
        return Literal[tuple(schema["enum"])]
    if kind == "object":
        return output_model(name, schema)
    if kind == "array":
        return list[_type(schema.get("items", {"type": "string"}), f"{name}_item")]
    return _SCALARS.get(kind, Any)


def output_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """JSON schema (the subset used by the extractor) -> Pydantic model; the model name is the tool name."""
    required = set(schema.get("required", []))
    fields = {}
    for key, sub in schema.get("properties", {}).items():
        typ = _type(sub, f"{name}_{key}")
        fields[key] = (typ, ...) if key in required else (Optional[typ], None)
    return create_model(name, __config__=ConfigDict(extra="ignore"),
                        __doc__=f"Record the {name.removeprefix('record_')} result.", **fields)


class ForcedToolModel(BedrockModel):
    """BedrockModel that forces the structured-output tool from the first turn.

    Strands offers the output tool with toolChoice auto and only forces it after a text-only reply;
    forcing it up front keeps one model call per item and stops a text preamble from eating the
    small token budgets of triage and match (as the previous direct Converse calls did).
    """

    def stream(self, messages, tool_specs=None, system_prompt=None, *, tool_choice=None, **kwargs):
        if tool_choice is None and tool_specs and len(tool_specs) == 1:
            tool_choice = {"tool": {"name": tool_specs[0]["name"]}}
        return super().stream(messages, tool_specs, system_prompt, tool_choice=tool_choice, **kwargs)


_MODELS: dict[str, type[BaseModel]] = {}


def structured(bedrock, model_id: str, system: str, text: str, tool: str, schema: dict[str, Any],
               max_tokens: int) -> dict[str, Any]:
    """-> the tool input as a dict (optional fields the model left out are absent)."""
    output = _MODELS.get(tool) or _MODELS.setdefault(tool, output_model(tool, schema))
    # streaming=False -> Converse (bedrock:InvokeModel), the permission the Runtime role already has.
    llm = ForcedToolModel(model_id=model_id, max_tokens=max_tokens, temperature=0, streaming=False)
    llm.client = bedrock
    agent = Agent(model=llm, system_prompt=system, callback_handler=None, name=f"intake_{tool}")
    result = agent(text, structured_output_model=output)   # MaxTokensReachedException when cut off
    if result.structured_output is None:
        raise ValueError(f"model did not call {tool} (stop_reason={result.stop_reason})")
    data = result.structured_output.model_dump(exclude_none=True)
    missing = [k for k in schema["required"] if k not in data]
    if missing:
        raise ValueError(f"model output is missing {missing}")
    return data
