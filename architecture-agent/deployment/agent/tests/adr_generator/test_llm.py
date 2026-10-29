"""Strands LLM layer: temperature handling, forced tool choice, fallback rules, FakeLLM (no AWS)."""
from __future__ import annotations

import pytest

from adr_generator.config import SONNET_4_6
from adr_generator.llm.bedrock import FakeLLM, FallbackClient, LLMError, _client as make_client, make_llm, user_msg
from tests.adr_generator.conftest import make_settings

TOOL = {"toolSpec": {"name": "submit", "description": "d", "inputSchema": {"json": {"type": "object"}}}}
OTHER_MODEL = "us.anthropic.claude-other-1"


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


def test_forced_tool_choice_and_temperature_for_sonnet(tmp_path):
    s = make_settings(tmp_path)
    boto = FakeBoto(tool_response())
    c = client_with(s, SONNET_4_6, boto)
    assert c.converse_tool("sys", [user_msg("x")], TOOL, 1234) == {"ok": 1}
    call = boto.calls[0]
    assert call["toolConfig"]["toolChoice"] == {"tool": {"name": "submit"}}
    assert call["inferenceConfig"] == {"maxTokens": 1234, "temperature": 0.2}
    assert "additionalModelRequestFields" not in call
    assert c.last_call["input_tokens"] == 11 and c.last_call["output_tokens"] == 7
    assert c.last_call["stop_reason"] == "tool_use" and c.last_call["model_id"] == SONNET_4_6


def test_no_temperature_when_model_has_none(tmp_path):
    s = make_settings(tmp_path)
    boto = FakeBoto(tool_response())
    c = client_with(s, OTHER_MODEL, boto)
    c.converse_tool("sys", [user_msg("x")], TOOL, 500)
    cfg = boto.calls[0]["inferenceConfig"]
    assert cfg == {"maxTokens": 500} and "temperature" not in cfg
    assert boto.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "submit"}}


def test_auto_tool_choice_model_gets_prompt_instruction(tmp_path):
    s = make_settings(tmp_path)
    model = s.auto_tool_choice_models[0]
    boto = FakeBoto(tool_response())
    client_with(s, model, boto).converse_tool("sys", [user_msg("x")], TOOL, 10)
    assert boto.calls[0]["toolConfig"]["toolChoice"] == {"auto": {}}
    assert "calling the `submit` tool" in boto.calls[0]["system"][0]["text"]


def test_error_kinds(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(LLMError) as e:
        client_with(s, SONNET_4_6, FakeBoto(error=RuntimeError("boom"))).converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "call_failed"
    with pytest.raises(LLMError) as e:
        client_with(s, SONNET_4_6, FakeBoto(tool_response(stop="max_tokens"))).converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "truncated"
    with pytest.raises(LLMError) as e:
        client_with(s, SONNET_4_6, FakeBoto(tool_response(name="other"))).converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "no_tool_call"


def test_fallback_on_call_failed(tmp_path):
    s = make_settings(tmp_path)
    primary = client_with(s, SONNET_4_6, FakeBoto(error=RuntimeError("AccessDenied")))
    fallback = client_with(s, OTHER_MODEL, FakeBoto(tool_response(inp={"from": "fallback"})))
    fc = FallbackClient(primary, fallback)
    assert fc.model_id == SONNET_4_6
    assert fc.converse_tool("s", [user_msg("x")], TOOL, 99) == {"from": "fallback"}
    lc = fc.last_call
    assert lc["fallback_used"] is True and lc["model_id"] == OTHER_MODEL
    assert "AccessDenied" in lc["primary_error"]


def test_no_fallback_on_truncated(tmp_path):
    s = make_settings(tmp_path)
    primary = client_with(s, SONNET_4_6, FakeBoto(tool_response(stop="max_tokens")))
    fb_boto = FakeBoto(tool_response())
    fc = FallbackClient(primary, client_with(s, OTHER_MODEL, fb_boto))
    with pytest.raises(LLMError) as e:
        fc.converse_tool("s", [user_msg("x")], TOOL, 10)
    assert e.value.kind == "truncated"
    assert fb_boto.calls == [] and fc.last_call["fallback_used"] is False


def test_make_llm_fallback_only_when_different(tmp_path):
    same = make_llm(make_settings(tmp_path, FALLBACK_MODEL_ID=SONNET_4_6))
    assert same.fallback is None and same.model_id == SONNET_4_6
    diff = make_llm(make_settings(tmp_path, FALLBACK_MODEL_ID=OTHER_MODEL))
    assert diff.fallback is not None and diff.fallback.model_id == OTHER_MODEL


def test_fake_llm_queue_in_order_and_repeats_last():
    llm = FakeLLM(tool_responses=[{"a": 1}, LLMError("boom"), {"c": 3}])
    assert llm.converse_tool("s", [user_msg("x")], TOOL, 1) == {"a": 1}
    with pytest.raises(LLMError):
        llm.converse_tool("s", [user_msg("x")], TOOL, 1)
    assert llm.converse_tool("s", [user_msg("x")], TOOL, 1) == {"c": 3}
    assert llm.converse_tool("s", [user_msg("x")], TOOL, 1) == {"c": 3}  # exhausted: last item repeats
    assert len(llm.tool_calls) == 4


def test_fake_llm_callable_and_empty_queue():
    llm = FakeLLM(tool_responses=[lambda system, messages: {"echo": system}])
    assert llm.converse_tool("hello", [], TOOL, 1) == {"echo": "hello"}
    with pytest.raises(LLMError):
        FakeLLM().converse_tool("s", [user_msg("x")], TOOL, 1)
