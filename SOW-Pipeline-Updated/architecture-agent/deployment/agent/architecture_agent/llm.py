"""LLM layer on the Strands Agents SDK, shared by adr_generator and diagram_generator.

Every pipeline step makes one forced tool call (or one plain-text call) and needs the tool input back. Each call
runs a fresh Strands `Agent` (no memory between calls) on a `BedrockModel`, with ONE custom tool that carries the
caller's exact `toolSpec` (name, description and schema unchanged) and stops the agent loop as soon as the model
calls it, so there is never a second model round-trip. Strands' own `structured_output_model` is not used: it would
rename the tool after a pydantic class and rewrite its description.

Behaviour kept from the previous boto3 wrapper:
  - Models that support it are forced to call the tool (toolChoice `tool`). Models listed in
    `auto_tool_choice_models` (Opus 5.5) get `auto` plus an explicit instruction; if they still answer in text,
    one short follow-up turn asks for the tool call.
  - `converse` (not streaming), read timeout 1800 s, botocore adaptive retries (max 8). Strands' own retry is off,
    so a failed call is never retried twice and paid for twice.
  - temperature only for models that have one; `output_config.effort` only when an effort is set.
  - FallbackClient repeats a call on the fallback model after a call failure or a missing tool call, never after
    truncation.
  - `last_call` records model, fallback, tokens, stop reason and seconds for metadata.json.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional, Protocol, runtime_checkable

import boto3
from botocore.config import Config
from strands import Agent
from strands.models import BedrockModel
from strands.types.exceptions import EventLoopException, MaxTokensReachedException
from strands.types.tools import AgentTool, ToolGenerator, ToolResult, ToolSpec, ToolUse
from strands.types._events import ToolResultEvent

log = logging.getLogger(__name__)

CALL_FAILED = "call_failed"
TRUNCATED = "truncated"
NO_TOOL_CALL = "no_tool_call"


class LLMError(RuntimeError):
    """kind: "call_failed" (API/credentials/network), "truncated" (max_tokens), "no_tool_call"."""

    def __init__(self, message: str, kind: str = CALL_FAILED):
        super().__init__(message)
        self.kind = kind


@runtime_checkable
class LLMClient(Protocol):
    model_id: str
    last_call: dict

    def converse_tool(self, system: str, messages: list[dict], tool_spec: dict, max_tokens: int) -> dict:
        """Force a tool call; return the tool input (parsed JSON dict)."""

    def converse_text(self, system: str, messages: list[dict], max_tokens: int) -> str:
        """Plain text completion."""


def user_msg(text: str) -> dict:
    return {"role": "user", "content": [{"text": text}]}


def assistant_msg(text: str) -> dict:
    return {"role": "assistant", "content": [{"text": text}]}


def _empty_call(model_id: str) -> dict:
    return {"model_id": model_id, "fallback_used": False, "primary_error": None,
            "input_tokens": 0, "output_tokens": 0, "stop_reason": None, "seconds": 0.0}


class _CaptureTool(AgentTool):
    """Presents the caller's `toolSpec` to the model unchanged, keeps the model's tool input, ends the agent loop."""

    def __init__(self, spec: dict):
        super().__init__()
        self._spec: ToolSpec = spec
        self.captured: Optional[dict] = None

    @property
    def tool_name(self) -> str:
        return self._spec["name"]

    @property
    def tool_spec(self) -> ToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "capture"

    async def stream(self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any) -> ToolGenerator:
        self.captured = tool_use["input"]
        invocation_state.setdefault("request_state", {})["stop_event_loop"] = True
        result: ToolResult = {"toolUseId": tool_use["toolUseId"], "status": "success", "content": [{"text": "received"}]}
        yield ToolResultEvent(result)


class _SecondModelCall(Exception):
    """A run reached a second model call: the model called something other than our tool."""


class _ForcedToolModel(BedrockModel):
    """BedrockModel that sends toolChoice `tool` for one named tool (Strands' Agent only sends `auto`).

    One run is one model call: the capture tool ends the loop, and a text answer ends it too. A second call only
    happens when the model called an unknown tool, which Strands would answer with an error and loop on."""

    forced_tool: Optional[str] = None
    calls_in_run: int = 0

    async def stream(self, messages, tool_specs=None, system_prompt=None, *, tool_choice=None, **kwargs):  # type: ignore[override]
        self.calls_in_run += 1
        if self.calls_in_run > 1:
            raise _SecondModelCall()
        if self.forced_tool and tool_specs:
            tool_choice = {"tool": {"name": self.forced_tool}}
        async for event in super().stream(messages, tool_specs, system_prompt, tool_choice=tool_choice, **kwargs):
            yield event


class StrandsClient:
    """One model on Bedrock, through Strands."""

    def __init__(self, model_id: str, region: str, profile: Optional[str] = None, temperature: Optional[float] = None,
                 forced_tool: bool = True, effort: Optional[str] = None):
        self.model_id = model_id
        self.region = region
        self._profile = profile
        self._temperature = temperature  # None -> never sent (Opus 5.5 rejects it)
        self._forced_tool = forced_tool  # False -> tool_choice "auto" + prompt instruction
        self._effort = effort            # Opus 5.5 thinking depth (output_config.effort); None -> not sent
        self._client = None              # boto3 bedrock-runtime client; injectable for tests
        self.last_call: dict = _empty_call(model_id)

    @property
    def client(self):
        if self._client is None:
            session = boto3.Session(profile_name=self._profile, region_name=self.region) if self._profile \
                else boto3.Session(region_name=self.region)
            self._client = session.client(
                "bedrock-runtime",
                # read_timeout well above any expected call: a timed-out request would be retried by boto3,
                # i.e. silently paid for twice
                config=Config(retries={"mode": "adaptive", "max_attempts": 8}, read_timeout=1800, connect_timeout=15),
            )
        return self._client

    def _model(self, max_tokens: int, forced_name: Optional[str]) -> _ForcedToolModel:
        cfg: dict[str, Any] = {"model_id": self.model_id, "max_tokens": max_tokens, "streaming": False}
        if self._temperature is not None:
            cfg["temperature"] = self._temperature
        if self._effort:
            cfg["additional_request_fields"] = {"output_config": {"effort": self._effort}}
        model = _ForcedToolModel(boto_session=boto3.Session(region_name=self.region), **cfg)
        model.client = self.client
        model.forced_tool = forced_name
        return model

    def _run(self, agent: Agent, content: list[dict], max_tokens: int, what: str) -> Any:
        t0 = time.monotonic()
        agent.model.calls_in_run = 0
        try:
            return agent(content)
        except Exception as e:  # botocore errors, NoCredentialsError, etc.
            cause = getattr(e, "original_exception", None) if isinstance(e, EventLoopException) else e
            if isinstance(cause, _SecondModelCall):
                return None
            if isinstance(cause, MaxTokensReachedException):
                raise LLMError(f"Model output was truncated (max_tokens={max_tokens} reached) {what}", TRUNCATED) from e
            raise LLMError(f"{type(cause).__name__}: {cause}", CALL_FAILED) from e
        finally:
            self.last_call["seconds"] = round(self.last_call["seconds"] + time.monotonic() - t0, 3)

    def _record(self, agent: Agent, result: Any) -> None:
        # the agent's metrics are cumulative over its runs, so a follow-up turn is counted once
        usage = agent.event_loop_metrics.accumulated_usage or {}
        self.last_call["input_tokens"] = int(usage.get("inputTokens") or 0)
        self.last_call["output_tokens"] = int(usage.get("outputTokens") or 0)
        if result is not None:
            self.last_call["stop_reason"] = result.stop_reason

    def converse_tool(self, system: str, messages: list[dict], tool_spec: dict, max_tokens: int) -> dict:
        """Return the input of the model's call to `tool_spec`.

        Models that support it are forced to call the tool. Models that reject forced tool use (Opus 5.5:
        adaptive thinking is always on, so only tool_choice "auto" is accepted) get "auto" plus an explicit
        instruction; if they still answer in text, one short follow-up turn asks for the tool call."""
        spec = tool_spec["toolSpec"]
        name = spec["name"]
        if not self._forced_tool:
            system = (f"{system}\n\nRespond by calling the `{name}` tool exactly once with the complete result. "
                      "Do not answer in plain text.")
        self.last_call = _empty_call(self.model_id)
        tool = _CaptureTool(spec)
        agent = Agent(model=self._model(max_tokens, name if self._forced_tool else None), system_prompt=system,
                      messages=list(messages[:-1]), tools=[tool], callback_handler=None, retry_strategy=None)
        result = self._run(agent, messages[-1]["content"], max_tokens, f"while calling {name}")
        self._record(agent, result)
        if tool.captured is not None:
            return tool.captured
        if not self._forced_tool:  # answered in text: ask once more, keeping the model's own turn
            result = self._run(agent, user_msg(f"Call the `{name}` tool now with the complete result.")["content"],
                               max_tokens, f"while calling {name}")
            self._record(agent, result)
            if tool.captured is not None:
                return tool.captured
        raise LLMError(f"Model did not call the {name} tool", NO_TOOL_CALL)

    def converse_text(self, system: str, messages: list[dict], max_tokens: int) -> str:
        self.last_call = _empty_call(self.model_id)
        agent = Agent(model=self._model(max_tokens, None), system_prompt=system, messages=list(messages[:-1]),
                      tools=[], callback_handler=None, retry_strategy=None)
        result = self._run(agent, messages[-1]["content"], max_tokens, "")
        self._record(agent, result)
        if result is None:
            raise LLMError("Model asked for a tool in a plain-text call", NO_TOOL_CALL)
        return "".join(b["text"] for b in result.message["content"] if "text" in b)


class FallbackClient:
    """Run on `primary`; on LLMError kind "call_failed" or "no_tool_call" repeat the same call on `fallback`
    (if any). Truncation is never retried elsewhere (the caller decides how to shorten the request)."""

    def __init__(self, primary: LLMClient, fallback: Optional[LLMClient]):
        self.primary = primary
        self.fallback = fallback
        self.model_id = primary.model_id
        self.last_call: dict = _empty_call(primary.model_id)

    def _run(self, method: str, *args: Any) -> Any:
        t0 = time.monotonic()
        try:
            out = getattr(self.primary, method)(*args)
        except LLMError as e:
            if e.kind not in (CALL_FAILED, NO_TOOL_CALL) or self.fallback is None:
                self.last_call = {**self.primary.last_call, "primary_error": None}
                raise
            log.warning("Primary model %s failed (%s); falling back to %s", self.primary.model_id, e, self.fallback.model_id)
            primary_error = f"{self.primary.model_id}: {e}"
            try:
                out = getattr(self.fallback, method)(*args)
            except LLMError:
                self.last_call = {**self.fallback.last_call, "fallback_used": True, "primary_error": primary_error,
                                  "seconds": round(time.monotonic() - t0, 3)}
                raise
            self.last_call = {**self.fallback.last_call, "fallback_used": True, "primary_error": primary_error,
                              "seconds": round(time.monotonic() - t0, 3)}
            return out
        self.last_call = {**self.primary.last_call, "fallback_used": False, "primary_error": None}
        return out

    def converse_tool(self, system: str, messages: list[dict], tool_spec: dict, max_tokens: int) -> dict:
        return self._run("converse_tool", system, messages, tool_spec, max_tokens)

    def converse_text(self, system: str, messages: list[dict], max_tokens: int) -> str:
        return self._run("converse_text", system, messages, max_tokens)


class FakeLLM:
    """Test double. `tool_responses` / `text_responses` are consumed in order; items may be
    dicts/strs, Exceptions (raised) or callables(system, messages) -> value. The last item repeats if exhausted."""

    region = "us-test-1"

    def __init__(self, tool_responses: list | None = None, text_responses: list | None = None,
                 model_id: str = "fake-model"):
        self.model_id = model_id
        self.tool_responses = list(tool_responses or [])
        self.text_responses = list(text_responses or [])
        self.tool_calls: list[dict] = []
        self.text_calls: list[dict] = []
        self.last_call: dict = _empty_call(model_id)

    @staticmethod
    def _next(queue: list, idx: int, system: str, messages: list[dict]):
        if not queue:
            raise LLMError("FakeLLM: no response configured", CALL_FAILED)
        item = queue[min(idx, len(queue) - 1)]
        return item(system, messages) if callable(item) else item

    def converse_tool(self, system, messages, tool_spec, max_tokens=0):
        out = self._next(self.tool_responses, len(self.tool_calls), system, messages)
        self.tool_calls.append({"system": system, "messages": messages, "tool_spec": tool_spec, "max_tokens": max_tokens})
        self.last_call = {**_empty_call(self.model_id), "stop_reason": "tool_use"}
        if isinstance(out, Exception):
            raise out
        return out

    def converse_text(self, system, messages, max_tokens=0):
        out = self._next(self.text_responses, len(self.text_calls), system, messages)
        self.text_calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
        self.last_call = {**_empty_call(self.model_id), "stop_reason": "end_turn"}
        if isinstance(out, Exception):
            raise out
        return out
