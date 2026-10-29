"""The orchestrator's seven tools: one file per tool, like prompts/ has one file per prompt.

    load_documents -> extract_explicit_needs -> find_hidden_signals -> merge_and_rank
        -> write_narrative -> render_outputs -> publish_outputs

Every tool is a thin `@tool(context=True)` function; the shared safety logic lives in `runner.py`.
"""
# imported in step order, which is also the order the tools register themselves in
from . import load_documents  # noqa: I001
from . import extract_explicit_needs
from . import find_hidden_signals
from . import merge_and_rank
from . import write_narrative
from . import render_outputs
from . import publish_outputs
from .runner import REGISTRY, StepSpec, run, run_step

# the order the orchestrator must call them in (enforced in code by state.PREREQS, not only by the prompt)
ALL_TOOLS = [
    load_documents.load_documents,
    extract_explicit_needs.extract_explicit_needs,
    find_hidden_signals.find_hidden_signals,
    merge_and_rank.merge_and_rank,
    write_narrative.write_narrative,
    render_outputs.render_outputs,
    publish_outputs.publish_outputs,
]

__all__ = ["ALL_TOOLS", "REGISTRY", "StepSpec", "run", "run_step"]
