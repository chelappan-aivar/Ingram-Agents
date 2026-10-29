"""Strands LLM layer: temperature handling, usage capture, fallback rules (no AWS)."""
from __future__ import annotations

import pytest

from diagram_generator.config import OPUS_5_5, SONNET_4_6
from diagram_generator.llm.bedrock import FakeLLM, FallbackClient, LLMError, _client as make_client, make_llm, user_msg
from tests.diagram_generator.conftest import make_settings

TOOL = {"toolSpec": {"name": "submit", "description": "d", "inputSchema": {"json": {"type": "object"}}}}


class FakeBoto:
    def __init__(self, response=None, error: Exception | None = None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def tool_response(stop="tool_use", name="submit", inp=None):
    return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": name, "input": inp or {"ok": 1}}}]}},
            "stopReason": stop, "usage": {"inputTokens": 11, "outputTokens": 7}}


def client_with(settings, model_id, boto):
    c = make_client(settings, model_id)
    c._client = boto
    return c


def test_no_temperature_for_opus(tmp_path):
    s = make_settings(tmp_path)
    boto = FakeBoto(tool_response())
    c = client_with(s, OPUS_5_5, boto)
    assert c.converse_tool("sys", [user_msg("x")], TOOL, 1234) == {"ok": 1}
    cfg = boto.calls[0]["inferenceConfig"]
    assert cfg == {"maxTokens": 1234} and "temperature" not in cfg
    # Opus 5.5 rejects forced tool use: tool_choice "auto" plus an explicit instruction in the system prompt
    assert boto.calls[0]["toolConfig"]["toolChoice"] == {"auto": {}}
    assert "calling the `submit` tool" in boto.calls[0]["system"][0]["text"]
    assert c.last_call["input_tokens"] == 11 and c.last_call["output_tokens"] == 7
    assert c.last_call["stop_reason"] == "tool_use" and c.last_call["model_id"] == OPUS_5_5


def test_temperature_for_configured_model(tmp_path):
    s = make_settings(tmp_path)
    boto = FakeBoto({"output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}}, "stopReason": "end_turn", "usage": {}})
    c = client_with(s, SONNET_4_6, boto)
    assert c.converse_text("sys", [user_msg("x")], 500) == "hi"
    assert boto.calls[0]["inferenceConfig"] == {"maxTokens": 500, "temperature": 0.1}


def test_error_kinds(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(LLMError) as e:
        client_with(s, OPUS_5_5, FakeBoto(error=RuntimeError("boom"))).converse_text("s", [user_msg("x")], 10)
    assert e.value.kind == "call_failed"
    with pytest.raises(LLMError) as e:
        client_with(s, OPUS_5_5, FakeBoto(tool_response(stop="max_tokens"))).converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "truncated"
    with pytest.raises(LLMError) as e:
        client_with(s, OPUS_5_5, FakeBoto(tool_response(name="other"))).converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "no_tool_call"


def test_fallback_on_call_failed(tmp_path):
    s = make_settings(tmp_path)
    primary = client_with(s, OPUS_5_5, FakeBoto(error=RuntimeError("AccessDenied")))
    fallback = client_with(s, SONNET_4_6, FakeBoto(tool_response(inp={"from": "sonnet"})))
    fc = FallbackClient(primary, fallback)
    assert fc.model_id == OPUS_5_5
    assert fc.converse_tool("s", [user_msg("x")], TOOL, 99) == {"from": "sonnet"}
    lc = fc.last_call
    assert lc["fallback_used"] is True and lc["model_id"] == SONNET_4_6
    assert "AccessDenied" in lc["primary_error"] and lc["input_tokens"] == 11


def test_no_fallback_on_truncated(tmp_path):
    s = make_settings(tmp_path)
    primary = client_with(s, OPUS_5_5, FakeBoto(tool_response(stop="max_tokens", name="other")))
    fb_boto = FakeBoto(tool_response())
    fc = FallbackClient(primary, client_with(s, SONNET_4_6, fb_boto))
    with pytest.raises(LLMError) as e:
        fc.converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "truncated" and fb_boto.calls == []
    assert fc.last_call["fallback_used"] is False


class SeqBoto(FakeBoto):
    """Returns the queued responses in order."""
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)

    def converse(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def text_response(text="Here is my answer in prose."):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 5, "outputTokens": 3}}


def test_opus_text_answer_gets_one_follow_up_turn(tmp_path):
    s = make_settings(tmp_path)
    boto = SeqBoto([text_response(), tool_response(inp={"ok": 2})])
    c = client_with(s, OPUS_5_5, boto)
    assert c.converse_tool("sys", [user_msg("x")], TOOL, 100) == {"ok": 2}
    follow = boto.calls[1]["messages"]
    assert follow[1]["role"] == "assistant" and "Call the `submit` tool now" in follow[2]["content"][0]["text"]
    assert c.last_call["input_tokens"] == 16 and c.last_call["output_tokens"] == 10  # both turns counted


def test_forced_tool_for_sonnet(tmp_path):
    s = make_settings(tmp_path)
    boto = FakeBoto(tool_response())
    client_with(s, SONNET_4_6, boto).converse_tool("sys", [user_msg("x")], TOOL, 100)
    assert boto.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "submit"}}
    assert boto.calls[0]["system"][0]["text"] == "sys"


def test_fallback_on_no_tool_call(tmp_path):
    s = make_settings(tmp_path)
    primary = client_with(s, OPUS_5_5, SeqBoto([text_response(), text_response()]))
    fc = FallbackClient(primary, client_with(s, SONNET_4_6, FakeBoto(tool_response(inp={"from": "sonnet"}))))
    assert fc.converse_tool("s", [user_msg("x")], TOOL, 10) == {"from": "sonnet"}
    assert fc.last_call["fallback_used"] is True and "did not call" in fc.last_call["primary_error"]


def test_fallback_none_and_both_fail(tmp_path):
    s = make_settings(tmp_path)
    p = client_with(s, OPUS_5_5, FakeBoto(error=RuntimeError("down")))
    with pytest.raises(LLMError):
        FallbackClient(p, None).converse_text("s", [user_msg("x")], 10)
    with pytest.raises(LLMError):
        FallbackClient(p, client_with(s, SONNET_4_6, FakeBoto(error=RuntimeError("also down")))).converse_text("s", [user_msg("x")], 10)


def test_primary_success_does_not_touch_fallback(tmp_path):
    s = make_settings(tmp_path)
    fb = FakeBoto(tool_response())
    fc = FallbackClient(client_with(s, OPUS_5_5, FakeBoto(tool_response())), client_with(s, SONNET_4_6, fb))
    fc.converse_tool("s", [user_msg("x")], TOOL, 10)
    assert fb.calls == [] and fc.last_call["fallback_used"] is False and fc.last_call["primary_error"] is None


def test_make_llm_uses_step_model_and_fallback(tmp_path):
    s = make_settings(tmp_path, EXTRACTOR_MODEL_ID="m-extract", FALLBACK_MODEL_ID="m-fb")
    llm = make_llm(s, "extractor")
    assert isinstance(llm, FallbackClient)
    assert llm.model_id == "m-extract" and llm.primary.model_id == "m-extract" and llm.fallback.model_id == "m-fb"
    assert make_llm(make_settings(tmp_path, FALLBACK_MODEL_ID=""), "coder").fallback is None


def test_fake_llm_basics():
    f = FakeLLM(tool_responses=[{"a": 1}, LLMError("x", "truncated")], text_responses=["t"])
    assert f.converse_tool("s", [user_msg("x")], TOOL, 5) == {"a": 1}
    assert f.tool_calls[0]["max_tokens"] == 5 and f.last_call["model_id"] == "fake-model"
    with pytest.raises(LLMError):
        f.converse_tool("s", [user_msg("x")], TOOL, 5)
    assert f.converse_text("s", [], 5) == "t"
