"""Shared guard logic used by every tool: ordering, single use, checkpoint/resume, timing, status, short summaries.

Tools take no model-supplied arguments. All configuration and data live in the `RunState` carried in
`invocation_state["run"]`, so customer content never enters the orchestrator's context: the dict returned by
`run_step` is the ONLY thing the orchestrator model sees.
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable

from strands.types.tools import ToolContext

from ..runtime.state import PreconditionError, RunState

log = logging.getLogger("discovery_agent.tools")


@dataclass(frozen=True)
class StepSpec:
    """What one tool does. Each tool file builds one of these and registers it."""

    name: str
    compute: Callable[[RunState], dict]  # does the work; the result is checkpointed
    apply: Callable[[RunState, dict], None]  # puts a result (fresh or restored from a checkpoint) into the run state
    summarize: Callable[[RunState], dict]  # the short summary the orchestrator model sees
    fatal: bool = True  # False: a failure is recorded but the sequence continues


REGISTRY: dict[str, StepSpec] = {}


def register(spec: StepSpec) -> StepSpec:
    REGISTRY[spec.name] = spec
    return spec


def error(step: str, kind: str, reason: str, fatal: bool) -> dict:
    return {"status": "error", "step": step, "kind": kind, "fatal": fatal, "reason": reason}


def run_step(state: RunState, step: str) -> dict:
    """Execute one step with all guards."""
    spec = REGISTRY[step]
    if state.failed:
        return error(step, "run_failed", f"run already failed at {state.failed[0]}; stop and report", True)
    if step in state.completed:
        return {"status": "ok", "step": step, "note": "already done"}
    try:
        state.require(step)
    except PreconditionError as e:
        return error(step, "precondition", str(e), False)  # the model can correct itself by calling the named step

    started = time.monotonic()
    try:
        cached = state.restore(step) if state.config.resume else None
        payload = cached if cached is not None else spec.compute(state)
        if cached is None:
            state.checkpoint(step, payload)
        spec.apply(state, payload)
    except Exception as e:  # noqa: BLE001  (message text only; no document content is included in these errors)
        reason = f"{type(e).__name__}: {e}"
        log.error("step=%s failed fatal=%s error_type=%s", step, spec.fatal, type(e).__name__)
        if spec.fatal:
            state.fail(step, reason)
            state.write_status("failed", reason)
            return error(step, "step_failed", reason, True)
        state.mark_done(step, status="failed", reason=reason)  # non-fatal: the sequence continues
        state.write_status("running")
        return {"status": "skipped", "step": step, "note": "non-fatal failure; continue with the next step"}

    seconds = round(time.monotonic() - started, 1)
    status = state.step_info.get(step, {}).get("status", "done")
    state.mark_done(step, seconds=seconds, **({"status": status} if status not in ("done",) else {}),
                    restored=cached is not None)
    state.write_status("running")
    summary = spec.summarize(state)
    log.info("step=%s status=ok seconds=%s restored=%s summary=%s", step, seconds, cached is not None,
             {k: v for k, v in summary.items() if k in ("counts", "est_tokens", "outcome")})
    return {"status": "skipped" if status in ("skipped", "failed") else "ok", "step": step, **summary}


def run(tool_context: ToolContext, step: str) -> dict:
    """What every @tool function calls: find the run state, then run the step."""
    state = tool_context.invocation_state.get("run")
    if not isinstance(state, RunState):
        return error(step, "no_run_state", "run state missing from invocation_state", True)
    return run_step(state, step)
