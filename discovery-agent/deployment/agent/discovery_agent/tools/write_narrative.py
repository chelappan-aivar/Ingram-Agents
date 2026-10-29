"""Step 5. Plain-language summaries (Haiku). Optional: a failure here never stops the run."""
from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "write_narrative"


def compute(state: RunState) -> dict:
    if not state.config.narrative:
        return {"narrative": None, "outcome": "skipped"}
    narrative = state.stages.write_narrative(state.report, state.config.instructions)
    return {"narrative": narrative, "outcome": "ok" if narrative else "failed"}


def apply(state: RunState, payload: dict) -> None:
    state.narrative = payload["narrative"]
    state.step_info[STEP] = {"status": payload["outcome"]}


def summarize(state: RunState) -> dict:
    return {"outcome": state.step_info[STEP]["status"]}


register(StepSpec(STEP, compute, apply, summarize, fatal=False))


@tool(context=True)
def write_narrative(tool_context: ToolContext) -> dict:
    """Step 5. Write the plain-language summaries. May be skipped or fail without stopping the run."""
    return run(tool_context, STEP)
