"""Project Catalyst pipeline orchestrator: SOW first, then the diagram, then the price.

    SOW draft -> Solutions Architect review -> Well-Architected review -> architecture diagram
              -> cost estimate -> final SOW review

Before this package the stages were separate web apps with their own run tables, and a
human pasted JSON between them. The ordering is not cosmetic: the architect settles the service
decisions and the scope split while writing the SOW, so the SOW has to be first; the
Well-Architected review runs against the SOW *before* the diagram so a bad decision costs one
edit instead of a loop through all three downstream stages; and architecture cannot run first
because it needs the SOW's final in-scope / out-of-scope split as input.

Layout:
  contracts.py   the stages, their order, and one run's state
  scope.py       SOW markdown -> the in-scope / out-of-scope split architecture consumes
  adapters.py    translations between the three agents' native shapes
  agents.py      the one seam to the agent runtimes, so the pipeline is testable without AWS
  pipeline.py    the sequencer
"""

from orchestrator.contracts import (STAGE_AGENTS, STAGE_LABELS, STAGES, PipelineState, Stage,
                                    StageRun, Status)
from orchestrator.pipeline import Pipeline, stage_order

__all__ = [
    "Pipeline",
    "PipelineState",
    "STAGES",
    "STAGE_AGENTS",
    "STAGE_LABELS",
    "Stage",
    "StageRun",
    "Status",
    "stage_order",
]
