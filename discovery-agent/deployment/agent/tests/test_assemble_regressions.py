"""Regressions from the first cloud run: the assembled report failed validation on two numbers."""
import unittest

from discovery_agent.analysis.assemble import assemble_report, repair_explicit, repair_hidden
from discovery_agent.analysis.pipeline import build_meta
from discovery_agent.data.documents import Document

TEXT = ("Ana Lee\n0:01\nWe use Partner Central 3 for billing and we waited 5 days for the last approval.\n"
        "Bo Chen\n0:20\nThe team gets about 40 tickets a week.")
DOC = Document("call_a", "transcript", TEXT, {"Ana Lee", "Bo Chen"}, "Call A")
EV = {"source": "call_a", "quote": "We use Partner Central 3 for billing and we waited 5 days", "speaker": "Ana Lee"}
EMPTY = {"goals": [], "pain_points": [], "current_state": [], "constraints": [], "stakeholders": []}


def explicit(**uc):
    base = {"title": "Billing agent", "problem": "Billing is manual", "evidence": [EV], "customer_value": "Less effort",
            "validation_question": "Who does it?", "confidence": "high", "priority": 1, "company_value": "Scope"}
    return {"what_we_heard": EMPTY, "explicit_use_cases": [{**base, **uc}], "open_questions": []}


def assemble(exp):
    cleaned, _ = repair_explicit(exp, [DOC])
    hidden, _ = repair_hidden({"hidden_use_cases": [], "inferred_heard_items": EMPTY, "open_questions": []}, [DOC])
    return assemble_report(cleaned, hidden, [DOC], build_meta([DOC], "T"))


class AssembleRegressionTests(unittest.TestCase):
    def test_system_name_containing_a_digit_is_kept(self):
        r = assemble(explicit(systems_involved=["Partner Central 3"]))
        self.assertEqual(r["use_cases"][0]["systems_involved"], ["Partner Central 3"])

    def test_system_not_in_documents_is_still_dropped(self):
        r = assemble(explicit(systems_involved=["Partner Central 3", "SAP 7"]))
        self.assertEqual(r["use_cases"][0]["systems_involved"], ["Partner Central 3"])

    def test_open_question_reason_with_an_ungrounded_number_is_repaired(self):
        exp = explicit()
        exp["open_questions"] = [{"question": "Who approves the invoices?", "evidence": [EV],
                                  "reason": "Unclear who owns the 90 day target."}]
        q = assemble(exp)["gaps_and_open_questions"][0]
        self.assertEqual(q["question"], "Who approves the invoices?")
        self.assertEqual(q["reason"], "Not stated in the sources.")

    def test_open_question_with_a_grounded_number_is_kept(self):
        exp = explicit()
        exp["open_questions"] = [{"question": "Why 5 days?", "reason": "The last approval took 5 days.", "evidence": [EV]}]
        q = assemble(exp)["gaps_and_open_questions"][0]
        self.assertEqual((q["question"], q["reason"]), ("Why 5 days?", "The last approval took 5 days."))

    def test_open_question_whose_text_is_ungrounded_is_dropped(self):
        exp = explicit()
        exp["open_questions"] = [{"question": "Is the 90 day target realistic?", "reason": "Unclear.", "evidence": [EV]}]
        self.assertEqual(assemble(exp)["gaps_and_open_questions"], [])


class StaleCheckpointTests(unittest.TestCase):
    def test_merge_recleans_data_produced_by_older_rules(self):
        from discovery_agent.analysis.pipeline import merge_report

        stale = explicit(systems_involved=["Partner Central 3"])
        stale["open_questions"] = [{"question": "Who approves?", "evidence": [EV], "reason": "Unclear who owns the 90 day target."}]
        hidden = {"hidden_use_cases": [], "inferred_heard_items": EMPTY, "open_questions": []}
        r = merge_report(stale, hidden, [DOC], "T")  # raw, never passed through repair_explicit
        self.assertEqual(r["gaps_and_open_questions"][0]["reason"], "Not stated in the sources.")
        self.assertEqual(r["use_cases"][0]["systems_involved"], ["Partner Central 3"])


if __name__ == "__main__":
    unittest.main()
