"""write_narrative: parallel calls, repair in code (no content retries), fallback to validated report text.

Run (from deployment/agent):  python -m unittest tests.test_narrative -v
"""
import copy
import json
import threading
import unittest
from unittest import mock

from discovery_agent.analysis import pipeline
from discovery_agent.analysis.validate import numbers_in
from tests.helpers import DOCS, GOOD_MERGE


def report(n_use_cases=2):
    """The synthetic report, extended with extra explicit use cases so the chunking is exercised."""
    r = copy.deepcopy({"schema_version": "1.0", "meta": pipeline.build_meta(DOCS, "T"), **GOOD_MERGE})
    for i in range(3, n_use_cases + 1):
        uc = copy.deepcopy(r["use_cases"][0])
        uc.update(id=f"UC-{i}", rank=i, title=f"Extra {i}", problem=f"Problem text {i}")
        r["use_cases"].append(uc)
    return r


class ScriptedModel:
    """Replaces pipeline._narrative_call: answers from a function of the task, records every task."""

    def __init__(self, answer):
        self.answer, self.tasks, self.lock = answer, [], threading.Lock()

    def __call__(self, content, task, instructions, label):
        with self.lock:
            self.tasks.append(task)
        out = self.answer(task)
        if isinstance(out, Exception):
            raise out
        return out


def well_behaved(task):
    out = {}
    if task["executive_summary"]:
        out["executive_summary"] = ["Customers want less manual work."]
    if task["opportunity_ids"]:
        out["opportunity_narratives"] = {i: f"why {i}" for i in task["opportunity_ids"]}
    if task["use_case_ids"]:
        out["use_case_summaries"] = {i: f"about {i}" for i in task["use_case_ids"]}
    return out


class NarrativeTests(unittest.TestCase):
    def run_narrative(self, answer, n=2):
        model = ScriptedModel(answer)
        with mock.patch.object(pipeline, "_narrative_call", model):
            return pipeline.write_narrative(report(n)), model

    def test_one_head_call_plus_one_call_per_chunk(self):
        out, model = self.run_narrative(well_behaved, n=13)
        self.assertEqual(len(model.tasks), 1 + 3)  # 13 use cases / 6 per call = 3 chunks
        self.assertEqual(sorted(i for t in model.tasks for i in t["use_case_ids"]), sorted(f"UC-{i}" for i in range(1, 14)))
        self.assertEqual(set(out["use_case_summaries"]), {f"UC-{i}" for i in range(1, 14)})
        self.assertEqual(out["opportunity_narratives"], {"UC-2": "why UC-2"})
        self.assertEqual(out["executive_summary"], ["Customers want less manual work."])

    def test_ungrounded_number_drops_only_that_sentence(self):
        def answer(task):
            out = well_behaved(task)
            if task["use_case_ids"]:  # 3 is in a quote; 40 is not
                out["use_case_summaries"]["UC-1"] = "Reconciliation takes 3 days. It could save 40 hours."
            return out
        out, _ = self.run_narrative(answer)
        self.assertEqual(out["use_case_summaries"]["UC-1"], "Reconciliation takes 3 days.")

    def test_clients_is_rewritten_not_retried(self):
        def answer(task):
            out = well_behaved(task)
            out["executive_summary"] = ["The clients want less manual work."] if task["executive_summary"] else None
            return {k: v for k, v in out.items() if v}
        out, model = self.run_narrative(answer)
        self.assertEqual(out["executive_summary"], ["The customers want less manual work."])
        self.assertEqual(len(model.tasks), 2)  # no extra call

    def test_report_ids_in_prose_are_not_numbers(self):
        self.assertEqual(numbers_in("See UC-3 and H-12; takes 3 days"), {"3"})
        def answer(task):
            out = well_behaved(task)
            if task["use_case_ids"]:
                out["use_case_summaries"]["UC-1"] = "Related to UC-2 and UC-14."
            return out
        out, _ = self.run_narrative(answer)
        self.assertEqual(out["use_case_summaries"]["UC-1"], "Related to UC-2 and UC-14.")

    def test_missing_entries_fall_back_to_validated_report_text(self):
        def answer(task):
            out = well_behaved(task)
            out.pop("use_case_summaries", None)
            out.pop("opportunity_narratives", None)
            return out
        out, _ = self.run_narrative(answer)
        r = report()
        self.assertEqual(out["use_case_summaries"], {u["id"]: u["problem"] for u in r["use_cases"]})
        self.assertEqual(out["opportunity_narratives"], {"UC-2": "Repeated across both calls"})

    def test_failed_chunk_keeps_the_rest(self):
        def answer(task):
            return RuntimeError("stage failed") if task["use_case_ids"] else well_behaved(task)
        out, _ = self.run_narrative(answer)
        self.assertEqual(out["use_case_summaries"]["UC-2"], "Approvals are chased by email")  # fallback text
        self.assertTrue(out["executive_summary"])

    def test_no_usable_executive_summary_skips_the_narrative(self):
        for bad in (RuntimeError("stage failed"), {"executive_summary": []}, {"executive_summary": "text"},
                    {"executive_summary": ["It saves 99 hours."]}):  # last: every sentence ungrounded
            out, _ = self.run_narrative(lambda t, b=bad: b if t["executive_summary"] else well_behaved(t))
            self.assertIsNone(out, bad)

    def test_shape_matches_what_the_renderer_reads(self):
        from discovery_agent.rendering import readme
        out, _ = self.run_narrative(well_behaved)
        md = readme.render_markdown(report(), out)
        self.assertIn("Customers want less manual work.", md)
        self.assertIn("about UC-1", md)
        self.assertIn("why UC-2", md)

    def test_narrative_call_sends_the_task_and_retries_only_unusable_output(self):
        prompts = []

        class Agent:
            def __init__(self):
                self.replies = ["not json at all", json.dumps({"executive_summary": ["ok"]})]

            def __call__(self, prompt):
                prompts.append(prompt)
                return self.replies.pop(0)

        with mock.patch.object(pipeline, "make_agent", lambda *a, **k: Agent()):
            out = pipeline._narrative_call("REPORT", {"executive_summary": True}, "", "narrative")
        self.assertEqual(out, {"executive_summary": ["ok"]})
        self.assertIn("<task>", prompts[0])
        self.assertEqual(len(prompts), 2)
        self.assertIn("not valid JSON", prompts[1])


if __name__ == "__main__":
    unittest.main()
