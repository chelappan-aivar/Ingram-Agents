import json

from diagram_generator.config import Settings
from diagram_generator.contracts import Extraction, SourceDoc
from diagram_generator.llm.assumer import assume, build_tool_spec
from diagram_generator.llm.extractor import load_prompt

SETTINGS = Settings()
SOURCES = [SourceDoc(file="adr one.pdf", text="Alpha service text", chars_original=18),
           SourceDoc(file="adr two.docx", text="Beta notes text", chars_original=15)]
CATALOG = "CANON: AWS Lambda | Amazon SQS"
EXTRACTION = Extraction.model_validate({
    "title": "T",
    "components": [{"id": "orders_fn", "name": "Orders", "service": "AWS Lambda", "strength": "stated",
                    "evidence": [{"file": "adr one.pdf", "quote": "Alpha", "reasoning": "r"}]}],
})


class FakeLLM:
    def __init__(self):
        self.calls = []

    def converse_tool(self, system, messages, tool_spec, max_tokens):
        self.calls.append({"system": system, "messages": messages, "tool_spec": tool_spec, "max_tokens": max_tokens})
        return {"components": []}


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_tool_spec_shape():
    ts = build_tool_spec()["toolSpec"]
    assert ts["name"] == "submit_assumptions"
    schema = ts["inputSchema"]["json"]
    json.dumps(schema)
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"components", "relationships", "boundaries", "unresolved"}
    assert not any(isinstance(n, dict) and ("$ref" in n or "$defs" in n) for n in _walk(schema))
    comp = schema["properties"]["components"]["items"]
    assert {"supports_ids", "reasoning", "evidence", "id", "name"} <= set(comp["properties"])
    assert "supports_ids" in comp["required"] and "reasoning" in comp["required"]
    unresolved = schema["properties"]["unresolved"]["items"]
    assert {"about", "reasoning", "suggestion", "related_ids"} <= set(unresolved["properties"])


def test_assume_message_and_call():
    llm = FakeLLM()
    out = assume(llm, SETTINGS, SOURCES, EXTRACTION, CATALOG)
    assert out == {"components": []}
    assert len(llm.calls) == 1
    c = llm.calls[0]
    assert c["tool_spec"]["toolSpec"]["name"] == "submit_assumptions"
    assert c["max_tokens"] == SETTINGS.max_tokens_for("assumer") == SETTINGS.assumer_max_tokens
    assert c["system"] == load_prompt("assumer_system.md")
    text = c["messages"][0]["content"][0]["text"]
    for s in SOURCES:
        assert f'<document file="{s.file}">' in text and s.text in text
    assert CATALOG in text
    assert "<extraction>" in text and '"orders_fn"' in text
    assert "submit_assumptions" in text


def test_system_prompt_contains_owner_rules():
    p = load_prompt("assumer_system.md").lower()
    for needle in ["add-only", "meaningless", "supports_ids", "unresolved", "suggestion", "related_ids",
                   "untrusted", "a_", "ar1", "exactly once", "signal", "good-practice", "few"]:
        assert needle in p, needle
    assert 1500 < len(p) < 12000
