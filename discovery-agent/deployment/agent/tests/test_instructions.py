"""Tests for user-driven custom instructions (#1): injected into the model prompt and recorded for traceability.

Offline: no AWS, no real models.
"""
import unittest

from discovery_agent.analysis import pipeline
from discovery_agent.data.documents import Document
from discovery_agent.runtime.state import RunConfig


class InstructionInjectionTests(unittest.TestCase):
    def test_make_agent_appends_instruction_to_system_prompt(self):
        captured = {}

        class FakeBedrockModel:
            def __init__(self, *a, **k):
                pass

        class FakeAgent:
            def __init__(self, model=None, system_prompt="", tools=None, callback_handler=None):
                captured["system_prompt"] = system_prompt

        import unittest.mock as mock
        with mock.patch.object(pipeline, "BedrockModel", FakeBedrockModel), \
             mock.patch.object(pipeline, "Agent", FakeAgent):
            pipeline.make_agent("extraction", "extraction.md", "Focus on security use cases only.")
        self.assertIn("Additional reviewer instructions", captured["system_prompt"])
        self.assertIn("Focus on security use cases only.", captured["system_prompt"])

    def test_make_agent_without_instruction_is_unchanged(self):
        captured = {}

        class FakeBedrockModel:
            def __init__(self, *a, **k):
                pass

        class FakeAgent:
            def __init__(self, model=None, system_prompt="", tools=None, callback_handler=None):
                captured["system_prompt"] = system_prompt

        import unittest.mock as mock
        with mock.patch.object(pipeline, "BedrockModel", FakeBedrockModel), \
             mock.patch.object(pipeline, "Agent", FakeAgent):
            pipeline.make_agent("extraction", "extraction.md", "")
        self.assertNotIn("Additional reviewer instructions", captured["system_prompt"])


class InstructionTraceabilityTests(unittest.TestCase):
    DOCS = [Document("a", "document", "hello", set(), "a")]

    def test_build_meta_records_instruction(self):
        meta = pipeline.build_meta(self.DOCS, "Acme", "Write for a CFO audience.")
        self.assertEqual(meta["reviewer_instructions"], "Write for a CFO audience.")

    def test_build_meta_omits_when_empty(self):
        meta = pipeline.build_meta(self.DOCS, "Acme", "")
        self.assertNotIn("reviewer_instructions", meta)


class RunConfigInstructionTests(unittest.TestCase):
    def test_parsed_from_options(self):
        cfg = RunConfig.from_payload({"engagement": "Acme", "options": {"instructions": "  focus on cost  "}})
        self.assertEqual(cfg.instructions, "focus on cost")  # trimmed

    def test_parsed_from_top_level(self):
        cfg = RunConfig.from_payload({"engagement": "Acme", "instructions": "be concise"})
        self.assertEqual(cfg.instructions, "be concise")

    def test_default_empty(self):
        cfg = RunConfig.from_payload({"engagement": "Acme"})
        self.assertEqual(cfg.instructions, "")

    def test_too_long_rejected(self):
        with self.assertRaises(ValueError):
            RunConfig.from_payload({"engagement": "Acme", "options": {"instructions": "x" * 4001}})

    def test_status_doc_includes_instructions(self):
        import tempfile
        from discovery_agent.data.storage import LocalStorage
        from discovery_agent.runtime.state import RunState
        with tempfile.TemporaryDirectory() as tmp:
            cfg = RunConfig.from_payload({"engagement": "Acme", "run_id": "r1", "options": {"instructions": "trace me"}})
            state = RunState(cfg, LocalStorage(tmp))
            doc = state.status_doc("running")
            self.assertEqual(doc["instructions"], "trace me")


if __name__ == "__main__":
    unittest.main()
