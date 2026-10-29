"""The pipeline's stages and run state.

Ordering is the point of this package. Before it, each agent had its own UI and its own run
table, and a human carried JSON between them; the only declared order ran the wrong way, with
the SOW generator consuming an architecture and cost handoff as "Stage 4".

The order here is the order the team actually works in:

    SOW  ->  Solutions Architect review  ->  Well-Architected review  ->  architecture diagram
         ->  cost estimate  ->  final SOW review

and each arrow is a real data dependency, not a preference:

  - The architect settles the service decisions (ECS vs EKS vs Fargate) and the in-scope /
    out-of-scope split while writing the SOW, so the SOW is first.
  - The Solutions Architect review is a human checkpoint on the draft, before anything is built
    on it. The SA edits the SOW and/or leaves comments, exactly as they do today, then signs off.
  - The Well-Architected review runs against the SOW, before the diagram, because a service
    decision caught here costs one SOW edit instead of a loop through the diagram and the price.
  - Architecture cannot run first: it needs the SOW's final scope as its input.
  - Cost follows architecture because it prices what the diagram shows.
  - The final SOW review runs last, when the architecture and cost blocks finally exist and the
    SOW's own cross-checks (service coverage, the cost estimate link) have something to check. It
    also flags services named in the SOW that the diagram and the estimate do not cover (KMS, a
    security agent, QuickSight and the like), because those are the ones that quietly slip through.

A stage is skippable but never reorderable: `STAGES` is the sequence and `Pipeline` walks it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Stage(str, Enum):
    SOW_DRAFT = "sow_draft"
    # The stored value stays "combined_review" so runs saved before the rename still load; only
    # the human-facing label changed to "Final SOW review".
    SA_REVIEW = "sa_review"
    WA_REVIEW = "wa_review"
    ARCHITECTURE = "architecture"
    COST = "cost"
    COMBINED_REVIEW = "combined_review"


# The sequence. Index order is execution order; nothing else defines it.
STAGES: tuple[Stage, ...] = (
    Stage.SOW_DRAFT,
    Stage.SA_REVIEW,
    Stage.WA_REVIEW,
    Stage.ARCHITECTURE,
    Stage.COST,
    Stage.COMBINED_REVIEW,
)

STAGE_LABELS: dict[Stage, str] = {
    Stage.SOW_DRAFT: "SOW draft",
    Stage.SA_REVIEW: "Solutions Architect review",
    Stage.WA_REVIEW: "Well-Architected review",
    Stage.ARCHITECTURE: "Architecture diagram",
    Stage.COST: "Cost estimate",
    Stage.COMBINED_REVIEW: "Final SOW review",
}

# Which agent runs each stage, for the UI and the logs. The SA review is a human step, with no
# agent behind it.
STAGE_AGENTS: dict[Stage, str] = {
    Stage.SOW_DRAFT: "sow",
    Stage.SA_REVIEW: "architect",
    Stage.WA_REVIEW: "sow",
    Stage.ARCHITECTURE: "architecture",
    Stage.COST: "cost",
    Stage.COMBINED_REVIEW: "sow",
}


# What a person can ask the AI to change after (or while stopped partway through) a run, and the
# stage whose output the change rewrites. Everything after that stage is stale once it changes.
REVISION_TARGETS: dict[str, Stage] = {
    "sow": Stage.SOW_DRAFT,
    "architecture": Stage.ARCHITECTURE,
    "cost": Stage.COST,
}
REVISION_LABELS: dict[str, str] = {
    "sow": "SOW",
    "architecture": "architecture design",
    "cost": "cost estimate",
}
MAX_REVISIONS_PER_RUN = 25
MAX_REVISIONS_SHOWN = 25


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    # The stage ran and returned findings serious enough to stop for a human. Not a failure:
    # nothing is broken, a person has to decide. This is what makes the early review worth
    # running -- it halts before the diagram and the price are built on a bad decision.
    BLOCKED = "blocked"


TERMINAL = (Status.COMPLETED, Status.SKIPPED)


@dataclass
class StageRun:
    """One stage's outcome. `output` is the agent's reply, trimmed to what later stages need."""

    stage: Stage
    status: Status = Status.PENDING
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    detail: str = ""              # why it blocked or was skipped, in one sentence
    seconds: float = 0.0

    @property
    def label(self) -> str:
        return STAGE_LABELS[self.stage]

    @property
    def agent(self) -> str:
        return STAGE_AGENTS[self.stage]

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "label": self.label,
            "agent": self.agent,
            "status": self.status.value,
            "error": self.error,
            "detail": self.detail,
            "seconds": self.seconds,
        }


@dataclass
class PipelineState:
    """Everything one run accumulates. Serialisable so a worker can resume it."""

    run_id: str = ""
    # Inputs for the first stage.
    template_id: str = ""
    upstream: dict[str, Any] = field(default_factory=dict)      # UpstreamInputs v1 (intake, discovery)
    project_details: str = ""
    # Set as stages complete.
    sow_markdown: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    runs: dict[Stage, StageRun] = field(default_factory=dict)
    # The Solutions Architect build checkpoint. `sa_comments` are the SA's notes of record, each
    # {"text", "by", "at"}; `sa_signed_off` is the per-run latch that lets the run past the
    # checkpoint. Once set it stays set, so a later AI change does not re-pause a finished run.
    sa_comments: list[dict[str, Any]] = field(default_factory=list)
    sa_signed_off: bool = False
    # Policy
    stop_on_blocked: bool = True
    # A change a person asked the AI to make, waiting for the worker: {"target", "instruction",
    # "requested_at", "requested_by"}. Empty when nothing is pending.
    revision: dict[str, Any] = field(default_factory=dict)
    # Every change already attempted, oldest first, including the ones that failed or changed nothing.
    revisions: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for stage in STAGES:
            self.runs.setdefault(stage, StageRun(stage))

    # ---------------------------------------------------------------- revisions
    def invalidate_after(self, stage: Stage) -> None:
        """Forget every stage after `stage`, because its input just changed.

        Their outputs described the old SOW, design or estimate, so keeping them would show (and
        export) a diagram or price that no longer matches. They go back to pending and the normal
        sequence re-runs them. A stage that had failed or blocked is cleared too: the change may
        well be the fix.
        """
        later = STAGES[STAGES.index(stage) + 1:]
        for s in later:
            self.runs[s] = StageRun(s)

    # ---------------------------------------------------------------- navigation
    def run(self, stage: Stage) -> StageRun:
        return self.runs[stage]

    def next_stage(self) -> Optional[Stage]:
        """The first stage not yet finished, or None when the pipeline is done or stopped."""
        for stage in STAGES:
            run = self.runs[stage]
            if run.status in TERMINAL:
                continue
            if run.status in (Status.FAILED, Status.BLOCKED):
                return None            # a stopped pipeline needs a human, not the next stage
            return stage
        return None

    @property
    def finished(self) -> bool:
        return self.next_stage() is None

    @property
    def status(self) -> Status:
        """The run's overall status: the first thing that stopped it, else the aggregate."""
        for stage in STAGES:
            run = self.runs[stage]
            if run.status == Status.FAILED:
                return Status.FAILED
            if run.status == Status.BLOCKED:
                return Status.BLOCKED
        if all(self.runs[s].status in TERMINAL for s in STAGES):
            return Status.COMPLETED
        if any(self.runs[s].status == Status.RUNNING for s in STAGES):
            return Status.RUNNING
        return Status.PENDING

    @property
    def stage_of_record(self) -> Stage:
        """The stage the run is sitting on, for a one-line progress display."""
        return self.next_stage() or STAGES[-1]

    def progress(self) -> int:
        done = sum(1 for s in STAGES if self.runs[s].status in TERMINAL)
        return int(round(100 * done / len(STAGES)))

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "stage": self.stage_of_record.value,
            "progress": self.progress(),
            "stages": [self.runs[s].to_json() for s in STAGES],
            "scope": self.scope or None,
            "has_sow": bool(self.sow_markdown),
            "sa_comments": self.sa_comments[-50:],
            "sa_signed_off": self.sa_signed_off,
            "revision": ({k: self.revision.get(k) for k in ("target", "instruction", "requested_at")}
                         if self.revision else None),
            "revisions": self.revisions[-MAX_REVISIONS_SHOWN:],
        }
