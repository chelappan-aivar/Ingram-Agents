"""Step 3. Pass 2: implied, latent and adjacent use cases across all documents."""
from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "find_hidden_signals"


def compute(state: RunState) -> dict:
    return state.stages.find_hidden(state.corpus, state.explicit, state.docs, state.config.instructions)


def apply(state: RunState, payload: dict) -> None:
    state.hidden = payload


def summarize(state: RunState) -> dict:
    return {"counts": {"hidden_use_cases": len(state.hidden.get("hidden_use_cases", [])),
                       "open_questions": len(state.hidden.get("open_questions", []))}}


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def find_hidden_signals(tool_context: ToolContext) -> dict:
    """Step 3. Find implied, latent and adjacent use cases across all documents. Takes no arguments."""
    return run(tool_context, STEP)
