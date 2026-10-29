"""Tests for the clarification chat (#2): phase-1 pause, phase-2 resume, and the resolution bookkeeping.

Offline: stub stages, local storage, no AWS/models.
"""
import tempfile
import unittest

from discovery_agent.analysis import pipeline
from discovery_agent.runtime.orchestrator import run_orchestrator
from discovery_agent.runtime.state import RunConfig, RunState
from tests.helpers import StubStages, seeded_storage


def _state(storage, stages, **options):
    cfg = RunConfig.from_payload({"engagement": "Acme", "run_id": "r1", "options": options})
    return RunState(cfg, storage, stages)


class ClarificationOutcomeUnitTests(unittest.TestCase):
    QS = [
        {"id": "Q-1", "question": "Same person?", "reason": "r", "type": "gap"},
        {"id": "Q-2", "question": "Which figure is right?", "reason": "r", "type": "conflict"},
        {"id": "Q-3", "question": "Owner?", "reason": "r", "type": "gap"},
    ]

    def test_reviewer_answer_marks_resolved(self):
        out = pipeline.clarification_outcomes(self.QS, {"Q-1": "yes, same person"})
        q1 = next(q for q in out if q["id"] == "Q-1")
        self.assertEqual(q1["status"], "resolved")
        self.assertEqual(q1["resolved_by"], "reviewer")
        self.assertEqual(q1["resolution"], "yes, same person")

    def test_skipped_gap_stays_open(self):
        out = pipeline.clarification_outcomes(self.QS, {"Q-3": "skip"})
        q3 = next(q for q in out if q["id"] == "Q-3")
        self.assertEqual(q3["status"], "open")
        self.assertNotIn("resolved_by", q3)

    def test_skipped_conflict_needs_confirmation_agent_assumed(self):
        out = pipeline.clarification_outcomes(self.QS, {"Q-2": "unknown"})
        q2 = next(q for q in out if q["id"] == "Q-2")
        self.assertEqual(q2["status"], "needs_confirmation")
        self.assertEqual(q2["resolved_by"], "agent_assumed")

    def test_unanswered_conflict_also_assumed(self):
        # a conflict with no entry at all in answers is treated as skipped -> assumed stand
        out = pipeline.clarification_outcomes(self.QS, {})
        q2 = next(q for q in out if q["id"] == "Q-2")
        self.assertEqual(q2["status"], "needs_confirmation")

    def test_meta_records_answers_verbatim_and_skips(self):
        meta = pipeline.clarifications_meta(self.QS, {"Q-1": "same", "Q-2": "skip"})
        items = {i["id"]: i for i in meta["items"]}
        self.assertEqual(items["Q-1"]["answer"], "same")
        self.assertEqual(items["Q-1"]["resolved_by"], "reviewer")
        self.assertEqual(items["Q-2"]["answer"], "skipped")
        self.assertEqual(items["Q-2"]["resolved_by"], "agent_assumed")
        self.assertEqual(items["Q-3"]["answer"], "skipped")
        self.assertNotIn("resolved_by", items["Q-3"])  # skipped gap has no resolver


class TwoPhaseFlowTests(unittest.TestCase):
    def test_default_run_unchanged_no_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            st = _state(seeded_storage(tmp), StubStages(calls))  # clarify off
            doc = run_orchestrator(st, agent=_noop_agent(st))
            self.assertEqual(doc["state"], "succeeded")
            self.assertNotIn("resolve", calls)

    def test_phase1_pauses_awaiting_clarification(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            st = _state(seeded_storage(tmp), StubStages(calls), clarify=True)
            doc = run_orchestrator(st)
            self.assertEqual(doc["state"], "awaiting_clarification")
            self.assertIn("merge", calls)
            self.assertNotIn("resolve", calls)        # not yet
            self.assertNotIn("render_outputs", st.completed)  # stopped before render
            self.assertIn("clarification", doc)
            # clarifications.json was written
            self.assertTrue(st.read_clarifications() is not None)

    def test_phase2_resumes_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = seeded_storage(tmp)
            # phase 1
            st1 = _state(storage, StubStages([]), clarify=True)
            run_orchestrator(st1)
            # reviewer answers, written where the API would write them
            st1.write_answers({"Q-1": "They are the same person."})
            # phase 2 (new state, same run id + storage, resume_with_answers)
            calls2 = []
            st2 = _state(storage, StubStages(calls2), resume_with_answers=True)
            doc = run_orchestrator(st2)
            self.assertEqual(doc["state"], "succeeded")
            self.assertIn("resolve", calls2)
            self.assertEqual(set(st2.completed) >= {"render_outputs", "publish_outputs"}, True)


def _noop_agent(state):
    # drives the model-path default run by executing every step in code (stand-in for the LLM orchestrator)
    from discovery_agent.tools import run_step
    from discovery_agent.runtime.state import STEPS

    def agent(task, invocation_state):
        for s in STEPS:
            run_step(invocation_state["run"], s)
    return agent


if __name__ == "__main__":
    unittest.main()
