"""The orchestrator agent: one Strands Agent that calls the seven tools strictly in order."""
import logging
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor

from .. import settings
from .state import STEPS, RunState
from ..tools import ALL_TOOLS, run_step

log = logging.getLogger("discovery_agent")

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "orchestrator.md"
# Fixed sequencing needs little capability, so a small model (settings.MODEL_ORCHESTRATOR); a Sonnet id is the safer option.

# The clarification feature splits the sequence at merge_and_rank: phase 1 stops there to ask the reviewer; phase 2
# resumes with the answers and finishes. These are the steps on each side of the pause.
_PHASE1_LAST = "merge_and_rank"
_AFTER_MERGE = STEPS[STEPS.index("merge_and_rank") + 1:]  # write_narrative, render_outputs, publish_outputs


def build_orchestrator(model=None) -> Agent:
    if model is None:
        model = BedrockModel(model_id=settings.MODEL_ORCHESTRATOR, boto_session=settings.aws_session(), max_tokens=1024)
    return Agent(
        model=model,
        system_prompt=PROMPT.read_text(),
        tools=ALL_TOOLS,
        tool_executor=SequentialToolExecutor(),  # one tool at a time, never in parallel
        callback_handler=None,
        name="discovery-orchestrator",
    )


def _run_steps(state: RunState, steps: list[str]) -> bool:
    """Run the given steps in order, in code. Returns False as soon as one fails fatally."""
    for step in steps:
        result = run_step(state, step)
        if result.get("status") == "error" and result.get("fatal"):
            return False
        if state.failed:
            return False
    return True


def _finalize(state: RunState) -> dict:
    if state.failed:
        return state.write_status("failed", f"{state.failed[0]}: {state.failed[1]}")
    missing = [s for s in STEPS if s not in state.completed]
    if missing:
        reason = "orchestrator ended before completing: " + ", ".join(missing)
        state.fail("orchestrator", reason)
        return state.write_status("failed", reason)
    return state.write_status("succeeded")


def _phase1_pause(state: RunState) -> dict:
    """Clarify ON, first invocation: run through merge, then pause for the reviewer instead of rendering."""
    steps_through_merge = STEPS[: STEPS.index(_PHASE1_LAST) + 1]
    if not _run_steps(state, steps_through_merge):
        return _finalize(state)
    questions = (state.report or {}).get("gaps_and_open_questions", [])
    for i, q in enumerate(questions):  # ensure every question has a stable id for the answer round
        q.setdefault("id", f"Q-{i + 1}")
        q.setdefault("type", "gap")
    state.write_clarifications(questions)
    log.info("phase1 paused for clarification engagement=%s questions=%d", state.config.engagement, len(questions))
    return state.write_status("awaiting_clarification", questions=questions)


def _phase2_resume(state: RunState) -> dict:
    """Clarify resume: restore through merge, resolve with the reviewer's answers (Opus), then finish."""
    state.config.resume = True  # reuse the checkpointed phase-1 work instead of recomputing/re-paying
    steps_through_merge = STEPS[: STEPS.index(_PHASE1_LAST) + 1]
    if not _run_steps(state, steps_through_merge):
        return _finalize(state)

    questions = state.read_clarifications()
    answers = state.read_answers()
    try:
        resolved = state.stages.resolve_clarifications(
            state.corpus, state.report, questions, answers, state.docs, state.config.instructions)
        state.report = resolved
        state.checkpoint("merge_and_rank", resolved)  # so render/publish use the resolved report
    except Exception as e:  # noqa: BLE001
        state.fail("resolve_clarifications", f"{type(e).__name__}")
        log.error("resolve_clarifications failed error_type=%s", type(e).__name__)
        return _finalize(state)

    _run_steps(state, _AFTER_MERGE)
    return _finalize(state)


def run_orchestrator(state: RunState, agent: Agent | None = None) -> dict:
    """Run the orchestrator, then decide success from the run state, never from the model's reply.

    Three paths: a normal run (model-driven, unchanged); a clarify phase-1 run that pauses after merge; and a
    clarify phase-2 run that resumes with the reviewer's answers. The clarify paths are driven in code (the sequence
    is deterministic) so the pause/resume cannot be mishandled by the model.
    """
    state.write_status("running")

    if state.config.resume_with_answers:
        return _phase2_resume(state)
    if state.config.clarify:
        return _phase1_pause(state)

    agent = agent or build_orchestrator()
    task = f"Run the discovery pipeline for the engagement named '{state.config.engagement}'."
    try:
        agent(task, invocation_state={"run": state})
    except Exception as e:  # noqa: BLE001
        state.fail("orchestrator", f"{type(e).__name__}")
        log.error("orchestrator raised error_type=%s", type(e).__name__)
    return _finalize(state)
