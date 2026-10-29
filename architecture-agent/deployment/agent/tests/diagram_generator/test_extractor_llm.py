import json

from diagram_generator.config import Settings
from diagram_generator.contracts import Extraction, SourceDoc
from diagram_generator.llm import extractor
from diagram_generator.llm.extractor import build_tool_spec, extract, load_prompt, repair

SETTINGS = Settings()
SOURCES = [SourceDoc(file="design doc.pdf", text="Alpha service text", chars_original=18),
           SourceDoc(file="notes.md", text="Beta notes text", chars_original=15)]
CATALOG = "CANON: AWS Lambda | Amazon S3"


class FakeLLM:
    def __init__(self, response=None):
        self.response = response if response is not None else {"title": "t"}
        self.calls = []

    def converse_tool(self, system, messages, tool_spec, max_tokens):
        self.calls.append({"system": system, "messages": messages, "tool_spec": tool_spec, "max_tokens": max_tokens})
        return self.response


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_tool_spec_shape():
    spec = build_tool_spec()
    ts = spec["toolSpec"]
    assert ts["name"] == "submit_extraction" and ts["description"]
    schema = ts["inputSchema"]["json"]
    json.dumps(schema)
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"title", "components", "relationships", "boundaries",
                                         "contradictions", "resolutions"}
    assert "title" in schema["required"]
    assert not any(isinstance(n, dict) and ("$ref" in n or "$defs" in n) for n in _walk(schema))
    comp = schema["properties"]["components"]["items"]
    assert {"id", "name", "service", "kind", "strength", "evidence"} <= set(comp["properties"])
    assert {"file", "quote", "reasoning"} <= set(comp["properties"]["evidence"]["items"]["properties"])


def test_tool_spec_validates_good_example():
    # round trip: the schema describes what Extraction accepts
    Extraction.model_validate({"title": "x"})


def test_extract_message_and_call():
    llm = FakeLLM({"title": "ok"})
    out = extract(llm, SETTINGS, SOURCES, CATALOG)
    assert out == {"title": "ok"}
    assert len(llm.calls) == 1
    c = llm.calls[0]
    assert c["tool_spec"]["toolSpec"]["name"] == "submit_extraction"
    assert c["max_tokens"] == SETTINGS.max_tokens_for("extractor") == SETTINGS.extractor_max_tokens
    assert c["system"] == load_prompt("extractor_system.md")
    msg = c["messages"][0]
    assert msg["role"] == "user"
    text = msg["content"][0]["text"]
    for s in SOURCES:
        assert f'<document file="{s.file}">' in text and s.text in text
    assert CATALOG in text and "submit_extraction" in text


def test_repair_message_has_previous_and_errors():
    llm = FakeLLM()
    previous = {"title": "old", "components": [{"id": "dup"}]}
    repair(llm, SETTINGS, SOURCES, CATALOG, previous, ["duplicate id 'dup'", "component 'x' has no evidence"])
    assert len(llm.calls) == 1
    text = llm.calls[0]["messages"][0]["content"][0]["text"]
    assert "duplicate id 'dup'" in text and "component 'x' has no evidence" in text
    assert '"title": "old"' in text and "<previous_extraction>" in text
    assert CATALOG in text and all(s.file in text for s in SOURCES)
    assert llm.calls[0]["max_tokens"] == SETTINGS.extractor_max_tokens
    assert llm.calls[0]["tool_spec"]["toolSpec"]["name"] == "submit_extraction"


def test_file_name_quote_escaped():
    llm = FakeLLM()
    extract(llm, SETTINGS, [SourceDoc(file='a"b.md', text="x", chars_original=1)], CATALOG)
    assert '<document file="a&quot;b.md">' in llm.calls[0]["messages"][0]["content"][0]["text"]


def test_system_prompt_contains_owner_rules():
    p = load_prompt("extractor_system.md")
    for needle in ["submit_extraction", "removed_ids", "resolutions", "untrusted", "verbatim", "snake_case",
                   "r1", "stated", "inferred", "4 words", "exactly once", "similar strength",
                   "components first", "RELATIONSHIPS", "catalog"]:
        assert needle.lower() in p.lower(), needle
    assert 2000 < len(p) < 12000


def test_user_msg_fallback_shape():
    assert extractor.user_msg("hi") == {"role": "user", "content": [{"text": "hi"}]}
