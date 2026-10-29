"""Step 7. Copy the finished files to the output location."""
from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "publish_outputs"


def compute(state: RunState) -> dict:
    keys = []
    for name in state.files:
        key = f"{state.config.output_dir}{name}"
        state.storage.write_bytes(key, state.read_work_file(name))
        keys.append(key)
    return {"output_keys": keys}


def apply(state: RunState, payload: dict) -> None:
    state.output_keys = payload["output_keys"]


def summarize(state: RunState) -> dict:
    return {"output_keys": state.output_keys}


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def publish_outputs(tool_context: ToolContext) -> dict:
    """Step 7. Publish the finished files to the output location. Takes no arguments. Call this last."""
    return run(tool_context, STEP)
