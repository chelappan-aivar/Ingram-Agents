"""A minimal scripted Strands Model implementation for tests.

Real Bedrock calls aren't available in this environment (no AWS credentials), and
we don't want unit tests for our orchestration logic to depend on live model
behavior anyway. This fake model lets a test script a fixed sequence of tool calls
and/or a final text response, independent of what the real model would decide.

Only implements enough of the Model interface for strands.Agent to run a single
turn (optionally invoking tools) to completion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from strands.models.model import Model


@dataclass
class ScriptedToolCall:
    name: str
    input: dict
    tool_use_id: str = "tool_call_1"


@dataclass
class FakeModel(Model):
    """Replays a scripted response once, then falls back to plain text.

    tool_calls: tool calls to emit on the FIRST stream() call for a given test.
    final_text: text to emit as the assistant's final message.
    """

    tool_calls: list[ScriptedToolCall] = field(default_factory=list)
    final_text: str = "Acknowledged."
    _config: dict = field(default_factory=dict)
    _tool_calls_emitted: bool = field(default=False, init=False, repr=False)

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("FakeModel does not support structured_output")

    async def stream(
        self,
        messages,
        tool_specs=None,
        system_prompt=None,
        **kwargs,
    ) -> AsyncGenerator[dict, None]:
        yield {"messageStart": {"role": "assistant"}}

        # Only emit the scripted tool calls on the first stream() call for this
        # instance. The agent loop re-invokes stream() after each tool result, so
        # replaying them unconditionally would call the same tool forever.
        pending_tool_calls = [] if self._tool_calls_emitted else self.tool_calls
        self._tool_calls_emitted = True

        block_index = 0
        for call in pending_tool_calls:
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": block_index,
                    "start": {
                        "toolUse": {"toolUseId": call.tool_use_id, "name": call.name}
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": block_index,
                    "delta": {"toolUse": {"input": json.dumps(call.input)}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": block_index}}
            block_index += 1

        yield {
            "contentBlockStart": {"contentBlockIndex": block_index, "start": {}},
        }
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": block_index,
                "delta": {"text": self.final_text},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": block_index}}

        stop_reason = "tool_use" if pending_tool_calls else "end_turn"
        yield {"messageStop": {"stopReason": stop_reason}}
