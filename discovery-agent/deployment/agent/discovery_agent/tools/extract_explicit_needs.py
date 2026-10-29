"""Step 2. Pass 1: what the customers explicitly stated, across all documents."""
from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "extract_explicit_needs"


def compute(state: RunState) -> dict:
    return state.stages.extract_explicit(state.corpus, state.docs, state.config.instructions)


def apply(state: RunState, payload: dict) -> None:
    state.explicit = payload


def summarize(state: RunState) -> dict:
    heard = state.explicit.get("what_we_heard", {})
    return {"counts": {"explicit_use_cases": len(state.explicit.get("explicit_use_cases", [])),
                       "heard_items": sum(len(v) for v in heard.values()),
                       "open_questions": len(state.explicit.get("open_questions", []))}}


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def extract_explicit_needs(tool_context: ToolContext) -> dict:
    """Step 2. Extract what the customers explicitly stated, across all documents. Takes no arguments."""
    return run(tool_context, STEP)
