"""Step 4. Combine both passes into the final, validated report (plain code, no model call)."""
from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "merge_and_rank"


def compute(state: RunState) -> dict:
    return state.stages.merge_report(state.explicit, state.hidden, state.docs, state.config.engagement,
                                     state.config.instructions)


def apply(state: RunState, payload: dict) -> None:
    state.report = payload


def summarize(state: RunState) -> dict:
    r = state.report
    return {"counts": {"use_cases": len(r["use_cases"]), "top_hidden_opportunities": len(r["top_hidden_opportunities"]),
                       "open_questions": len(r["gaps_and_open_questions"])}}


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def merge_and_rank(tool_context: ToolContext) -> dict:
    """Step 4. Merge, rank and validate the discovery report. Takes no arguments."""
    return run(tool_context, STEP)
