import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from discovery_agent import tools
from discovery_agent.analysis import pipeline
from discovery_agent.runtime.orchestrator import run_orchestrator
from discovery_agent.runtime.state import STEPS, RunConfig, RunState
from tests.helpers import StubStages, make_state, seeded_storage

ALL = lambda state: [tools.run_step(state, s) for s in STEPS]  # noqa: E731


class ConfigTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        c = RunConfig.from_payload({"engagement": "Acme Corp"})
        self.assertEqual(c.input_prefix, "input/Acme_Corp/")
        self.assertEqual(c.output_prefix, "output/Acme_Corp/")
        self.assertTrue(c.narrative and not c.resume)
        for bad in ({}, {"engagement": "../x"}, {"engagement": "ok", "run_id": "a/b"}):
            with self.assertRaises(ValueError):
                RunConfig.from_payload(bad)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = seeded_storage(self.tmp.name)
        self.calls = []

    def state(self, **kw):
        stub = kw.pop("stages", None) or StubStages(self.calls)
        return make_state(self.storage, stub, **kw)

    def test_full_sequence_publishes_all_outputs(self):
        s = self.state()
        results = ALL(s)
        self.assertEqual([r["status"] for r in results], ["ok"] * 7)
        self.assertEqual(s.completed, STEPS)
        self.assertEqual([d["id"] for d in results[0]["documents"]], ["call_a.txt", "call_b.txt"])  # marker and .png skipped
        keys = results[-1]["output_keys"]
        self.assertEqual(sorted(k.rsplit("/", 1)[1] for k in keys),
                         ["README.md", "discovery.json", "discovery_report.docx", "narrative.json"])
        out = "output/Acme/r1/"
        self.assertTrue(self.storage.read_bytes(out + "README.md").decode().startswith("# Discovery Report"))
        self.assertTrue(self.storage.read_bytes(out + "discovery_report.docx").startswith(b"PK"))
        self.assertEqual(json.loads(self.storage.read_bytes(out + "discovery.json"))["schema_version"], "1.0")
        status = json.loads(self.storage.read_bytes(out + "status.json"))
        self.assertEqual(status["completed_steps"], STEPS)

    def test_out_of_order_call_is_rejected_without_failing_the_run(self):
        s = self.state()
        r = tools.run_step(s, "merge_and_rank")
        self.assertEqual((r["status"], r["kind"], r["fatal"]), ("error", "precondition", False))
        self.assertIsNone(s.failed)
        self.assertEqual(self.calls, [])

    def test_single_use_guard(self):
        s = self.state()
        tools.run_step(s, "load_documents")
        tools.run_step(s, "extract_explicit_needs")
        again = tools.run_step(s, "extract_explicit_needs")
        self.assertEqual(again["note"], "already done")
        self.assertEqual(self.calls.count("extract"), 1)

    def test_fatal_failure_stops_everything_and_marks_status(self):
        s = self.state(stages=StubStages(self.calls, extract_error="boom"))
        tools.run_step(s, "load_documents")
        r = tools.run_step(s, "extract_explicit_needs")
        self.assertEqual((r["status"], r["fatal"]), ("error", True))
        self.assertIsNotNone(s.failed)
        self.assertEqual(tools.run_step(s, "find_hidden_signals")["kind"], "run_failed")
        status = json.loads(self.storage.read_bytes("output/Acme/r1/status.json"))
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("hidden", self.calls)

    def test_failed_narrative_is_non_fatal_and_output_has_no_narrative_file(self):
        s = self.state(stages=StubStages(self.calls, narrative=None))
        results = ALL(s)
        self.assertEqual(results[4]["status"], "skipped")
        self.assertEqual(s.completed, STEPS)
        self.assertNotIn("narrative.json", [k.rsplit("/", 1)[1] for k in results[-1]["output_keys"]])

    def test_narrative_option_off_skips_the_model_call(self):
        s = self.state(narrative=False)
        ALL(s)
        self.assertNotIn("narrative", self.calls)

    def test_resume_restores_checkpoints_without_calling_stages_again(self):
        ALL(self.state())
        first_calls = list(self.calls)
        self.calls.clear()
        s2 = self.state(resume=True)
        results = ALL(s2)
        self.assertEqual(self.calls, [])  # nothing recomputed
        self.assertEqual(s2.completed, STEPS)
        self.assertTrue(all(r["status"] == "ok" for r in results))
        self.assertEqual(first_calls, ["extract", "hidden", "merge", "narrative"])

    def test_corpus_over_budget_is_fatal_at_load(self):
        s = self.state()
        with mock.patch.object(pipeline, "CORPUS_TOKEN_BUDGET", 10), \
                mock.patch.object(pipeline, "trim_documents", lambda docs: docs):  # no real model in tests
            r = tools.run_step(s, "load_documents")
        self.assertEqual((r["status"], r["fatal"]), ("error", True))
        self.assertIn("CorpusTooLarge", r["reason"])

    def test_no_documents_is_fatal(self):
        s = make_state(seeded_storage(tempfile.mkdtemp()), StubStages(self.calls))
        s.config.input_prefix = "input/Empty/"
        self.assertTrue(tools.run_step(s, "load_documents")["fatal"])

    def test_tool_results_carry_no_document_text(self):
        s = self.state()
        blob = json.dumps(ALL(s))
        self.assertNotIn("billing reconciliation", blob)
        self.assertNotIn("credit approvals", blob)

    def test_wrapper_requires_run_state(self):
        ctx = SimpleNamespace(invocation_state={})
        self.assertEqual(tools.run(ctx, "load_documents")["kind"], "no_run_state")

    def test_each_tool_function_runs_its_own_step(self):
        with mock.patch.object(tools.load_documents, "run", lambda ctx, step: {"step": step}), \
                mock.patch.object(tools.publish_outputs, "run", lambda ctx, step: {"step": step}):
            self.assertEqual(tools.load_documents.load_documents(SimpleNamespace(invocation_state={}))["step"], "load_documents")
            self.assertEqual(tools.publish_outputs.publish_outputs(SimpleNamespace(invocation_state={}))["step"], "publish_outputs")

    def test_every_step_has_a_registered_spec_in_order(self):
        self.assertEqual(list(tools.REGISTRY), STEPS)

    def test_tool_specs_take_no_arguments(self):
        for t in tools.ALL_TOOLS:
            props = t.tool_spec["inputSchema"]["json"].get("properties", {})
            self.assertEqual(props, {}, t.tool_name)
        self.assertEqual([t.tool_name for t in tools.ALL_TOOLS], STEPS)


class OrchestratorCompletionTests(unittest.TestCase):
    """The run is judged from the run state, never from what the model says."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = make_state(seeded_storage(self.tmp.name), StubStages([]))

    def test_success_when_every_step_completed(self):
        agent = lambda task, invocation_state: ALL(invocation_state["run"])  # noqa: E731
        self.assertEqual(run_orchestrator(self.state, agent)["state"], "succeeded")

    def test_model_stopping_early_is_a_failure(self):
        def agent(task, invocation_state):
            tools.run_step(invocation_state["run"], "load_documents")
            return "All done!"  # claims success, did not finish
        status = run_orchestrator(self.state, agent)
        self.assertEqual(status["state"], "failed")
        self.assertIn("extract_explicit_needs", status["error"])

    def test_agent_exception_is_a_failure_without_message_text(self):
        def agent(task, invocation_state):
            raise ValueError("contains customer text")
        status = run_orchestrator(self.state, agent)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("customer text", json.dumps(status))

    def test_fatal_tool_error_is_reported_as_failed(self):
        state = make_state(seeded_storage(self.tmp.name), StubStages([], extract_error="boom"))
        agent = lambda task, invocation_state: ALL(invocation_state["run"])  # noqa: E731
        self.assertEqual(run_orchestrator(state, agent)["state"], "failed")


if __name__ == "__main__":
    unittest.main()
