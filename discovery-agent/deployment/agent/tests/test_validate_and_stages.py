import copy
import json
import unittest
from unittest import mock

from discovery_agent.analysis import pipeline
from discovery_agent.analysis.validate import validate_report
from tests.helpers import DOCS, EXPLICIT, GOOD_MERGE, HIDDEN, FakeAgent

BY_ID = {d.id: d for d in DOCS}


def report(**overrides):
    r = {"schema_version": "1.0", "meta": pipeline.build_meta(DOCS, "T"), **copy.deepcopy(GOOD_MERGE)}
    r.update(overrides)
    return r


class ValidateTests(unittest.TestCase):
    def test_valid_report_passes(self):
        self.assertEqual(validate_report(report(), BY_ID), [])

    def test_quote_cited_to_wrong_document_is_rejected(self):
        r = report()
        r["use_cases"][0]["evidence"][0]["source"] = "call_b"
        self.assertTrue(any("quote not found" in e for e in validate_report(r, BY_ID)))

    def test_fillers_and_repeats_in_source_are_tolerated(self):
        r = report()
        r["use_cases"][0]["evidence"][0]["quote"] = "Our billing reconciliation takes 3 days every month"
        self.assertEqual(validate_report(r, BY_ID), [])

    def test_invented_number_is_rejected_but_customer_metric_is_kept(self):
        r = report()
        r["use_cases"][0]["problem"] = "Takes 7777 days and costs 9999"
        errors = validate_report(r, BY_ID)
        self.assertTrue(any("7777" in e and "9999" in e for e in errors))
        self.assertFalse(any("'3'" in e for e in errors))  # 3 appears in the cited quote

    def test_clients_wording_is_rejected(self):
        r = report()
        r["use_cases"][0]["customer_value"] = "Our clients benefit"
        self.assertTrue(any("customers" in e for e in validate_report(r, BY_ID)))

    def test_hypothesis_label_rules(self):
        r = report()
        del r["use_cases"][1]["hypothesis_label"]
        self.assertTrue(validate_report(r, BY_ID))
        r = report()
        r["use_cases"][0]["hypothesis_label"] = "hypothesis, not confirmed"  # forbidden on EXPLICIT
        self.assertTrue(validate_report(r, BY_ID))

    def test_unknown_speaker_and_bad_references(self):
        r = report()
        r["use_cases"][0]["evidence"][0]["speaker"] = "Nobody"
        self.assertTrue(any("speaker" in e for e in validate_report(r, BY_ID)))
        r = report()
        r["top_hidden_opportunities"][0]["use_case_id"] = "UC-1"  # EXPLICIT, not hidden
        self.assertTrue(any("non-hidden" in e for e in validate_report(r, BY_ID)))
        r = report()
        r["use_cases"][1]["rank"] = 1
        self.assertTrue(any("ranks" in e for e in validate_report(r, BY_ID)))


class SpecificsTests(unittest.TestCase):
    def with_specifics(self, **fields):
        r = report()
        r["use_cases"][0].update(fields)
        return r

    def test_grounded_specifics_are_accepted(self):
        r = self.with_specifics(systems_involved=["spreadsheets"], current_process=["Reconcile billing every month"],
                                owners=[{"name": "Ana Lee", "role": "billing"}], stated_metrics=["3 days every month"],
                                dependencies_and_blockers=["Manual spreadsheets"], data_sources_and_apis=["Spreadsheets"],
                                status="unknown")
        self.assertEqual(validate_report(r, BY_ID), [])

    def test_system_not_in_any_document_is_rejected(self):
        errors = validate_report(self.with_specifics(systems_involved=["SAP"]), BY_ID)
        self.assertTrue(any("does not appear in any source document" in e for e in errors))

    def test_system_check_covers_pass_outputs_too(self):
        from discovery_agent.analysis.validate import check_content
        data = copy.deepcopy(EXPLICIT)
        data["explicit_use_cases"][0]["systems_involved"] = ["Salesforce"]
        self.assertTrue(any("Salesforce" in e for e in check_content(data, BY_ID)))

    def test_number_in_process_step_must_come_from_the_quote(self):
        errors = validate_report(self.with_specifics(current_process=["Wait 14 days for approval"]), BY_ID)
        self.assertTrue(any("14" in e for e in errors))

    def test_invalid_status_and_owner_shape_fail_the_schema(self):
        self.assertTrue(validate_report(self.with_specifics(status="done"), BY_ID))
        self.assertTrue(validate_report(self.with_specifics(owners=[{"role": "x"}]), BY_ID))

    def test_problem_kind_for_logs_hides_the_system_name(self):
        kinds = pipeline.problem_kinds(["system 'SAP' at /x does not appear in any source document"])
        self.assertEqual(kinds, {"unknown_system": 1})


class ClientConfigTests(unittest.TestCase):
    def test_stage_agents_use_a_long_read_timeout(self):
        # Regression: a 117k-token corpus exceeded botocore's default 60 s read timeout before streaming began.
        with mock.patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x"}, clear=False):
            agent = pipeline.make_agent("extraction", "extraction.md")
        cfg = agent.model.client.meta.config
        self.assertGreaterEqual(cfg.read_timeout, 600)


class MaxTokensTests(unittest.TestCase):
    def test_cut_off_output_is_retried_with_a_size_instruction(self):
        from strands.types.exceptions import MaxTokensReachedException

        calls = []

        class Agent:
            def __call__(self, prompt):
                calls.append(prompt)
                if len(calls) == 1:
                    raise MaxTokensReachedException("too long")
                return json.dumps({"ok": True})

        self.assertEqual(pipeline.run_json(Agent(), "go", stage="t"), {"ok": True})
        self.assertIn("too long", calls[1])
        self.assertEqual(pipeline.problem_kinds(["output was cut off because it was too long: x"]), {"output_too_long": 1})


class StageTests(unittest.TestCase):
    def run_stages(self, extraction=None, hidden=None):
        calls = []
        scripted = {"extraction": list(extraction or [EXPLICIT]), "hidden_signals": list(hidden or [HIDDEN])}
        with mock.patch.object(pipeline, "make_agent", lambda stage, f, instructions="": FakeAgent(stage, scripted[stage], calls)):
            return pipeline.run(DOCS, "T"), calls

    def test_run_makes_two_model_calls_and_assembles_in_code(self):
        r, calls = self.run_stages()
        self.assertEqual([c[0] for c in calls], ["extraction", "hidden_signals"])  # no merge model call
        self.assertEqual([u["id"] for u in r["use_cases"]], ["UC-1", "UC-2"])
        self.assertEqual(validate_report(r, BY_ID), [])
        self.assertEqual(sorted(r["meta"]["models"]), ["extraction", "hidden_signals"])

    def test_unsupported_item_is_removed_in_code_not_retried(self):
        bad = copy.deepcopy(EXPLICIT)
        bad["explicit_use_cases"].append({**bad["explicit_use_cases"][0], "title": "Invented thing",
                                          "evidence": [{"source": "call_a", "quote": "a sentence nobody said"}]})
        r, calls = self.run_stages(extraction=[bad])
        self.assertEqual(len(calls), 2)  # one call per pass, no retry
        self.assertNotIn("Invented thing", [u["title"] for u in r["use_cases"]])

    def test_invalid_json_is_still_retried(self):
        calls = []

        class Agent:
            def __call__(self, prompt):
                calls.append(prompt)
                return "not json at all" if len(calls) == 1 else json.dumps(EXPLICIT)

        with mock.patch.object(pipeline, "make_agent", lambda stage, f, instructions="": Agent()):
            out = pipeline.extract_explicit("corpus", DOCS)
        self.assertEqual(len(calls), 2)
        self.assertIn("not valid JSON", calls[1])
        self.assertEqual(len(out["explicit_use_cases"]), 1)

    def test_failure_after_retries_names_only_problem_kinds(self):
        class Agent:
            def __call__(self, prompt):
                return "garbage"

        with mock.patch.object(pipeline, "make_agent", lambda stage, f, instructions="": Agent()):
            with self.assertRaises(RuntimeError) as cm:
                pipeline.extract_explicit("corpus", DOCS)
        self.assertIn("invalid_json", str(cm.exception))
        self.assertNotIn("garbage", str(cm.exception))

    def test_problem_kinds_hide_quote_text(self):
        self.assertEqual(pipeline.problem_kinds(["quote not found verbatim in x: 'secret words'"]),
                         {"quote_not_verbatim": 1})

    def test_corpus_over_budget_is_trimmed_then_raises_if_still_too_large(self):
        with mock.patch.object(pipeline, "CORPUS_TOKEN_BUDGET", 10), \
                mock.patch.object(pipeline, "trim_documents", lambda docs: docs):
            with self.assertRaises(pipeline.CorpusTooLarge) as cm:
                pipeline.prepare_corpus(DOCS)
        self.assertIn("after removing small talk", str(cm.exception))

    def test_corpus_that_fits_after_trimming_is_accepted(self):
        smaller = [type(d)(d.id, d.kind, "x", d.speakers, d.label, 0.01) for d in DOCS]
        with mock.patch.object(pipeline, "CORPUS_TOKEN_BUDGET", 60), \
                mock.patch.object(pipeline, "trim_documents", lambda docs: smaller):
            corpus, docs = pipeline.prepare_corpus(DOCS)
        self.assertEqual(docs, smaller)
        self.assertIn("<document id=", corpus)


if __name__ == "__main__":
    unittest.main()
