import copy
import unittest

from discovery_agent.analysis import pipeline
from discovery_agent.analysis.assemble import MAX_USE_CASES, assemble_report, repair_explicit, repair_hidden
from discovery_agent.analysis.validate import validate_report
from tests.helpers import DOCS, E1, E2, E3, EMPTY, EXPLICIT, HIDDEN

BY_ID = {d.id: d for d in DOCS}
META = pipeline.build_meta(DOCS, "T")


def uc(title, **kw):
    base = {"title": title, "problem": "Reconciliation is manual and takes 3 days a month", "evidence": [E1],
            "customer_value": "Less manual effort", "validation_question": "Who does it?", "confidence": "high",
            "company_value": "Pilot candidate", "priority": 2}
    return {**base, **kw}


def explicit(*cases, heard=None, questions=None):
    return {"what_we_heard": heard or EMPTY, "explicit_use_cases": list(cases), "open_questions": questions or []}


class RepairTests(unittest.TestCase):
    def test_clean_input_passes_through_unchanged(self):
        clean, stats = repair_explicit(copy.deepcopy(EXPLICIT), DOCS)
        self.assertEqual(len(clean["explicit_use_cases"]), 1)
        self.assertEqual(stats, {})

    def test_identity_and_attribution_questions_are_dropped(self):
        qs = [
            {"question": "Are Marcus Lee and Marcus Obi the same person?", "reason": "r", "type": "gap"},
            {"question": "Who said the two-day figure, Dana or Marcus?", "reason": "r", "type": "conflict"},
            {"question": "Whose spreadsheet is the working sheet?", "reason": "r", "type": "gap"},
            {"question": "Which systems hold the discount rules the customers rely on?", "reason": "r",
             "evidence": [E1], "type": "gap"},  # a legitimate scope question — must survive
        ]
        clean, stats = repair_explicit(explicit(uc("a", evidence=[E1]), questions=qs), DOCS)
        kept = [q["question"] for q in clean["open_questions"]]
        self.assertEqual(len(kept), 1)
        self.assertIn("discount rules", kept[0])
        self.assertEqual(stats.get("question_dropped_identity_noise"), 3)

    def test_invented_quote_removes_the_entry_and_then_the_item(self):
        bad_quote = {"source": "call_a", "quote": "never said this"}
        clean, stats = repair_explicit(explicit(uc("a", evidence=[E1, bad_quote]), uc("b", evidence=[bad_quote])), DOCS)
        self.assertEqual([u["title"] for u in clean["explicit_use_cases"]], ["a"])
        self.assertEqual(len(clean["explicit_use_cases"][0]["evidence"]), 1)
        self.assertEqual(stats["use_case_dropped_no_evidence"], 1)

    def test_system_not_in_documents_is_dropped_but_real_one_kept(self):
        clean, stats = repair_explicit(explicit(uc("a", systems_involved=["spreadsheets", "SAP"])), DOCS)
        self.assertEqual(clean["explicit_use_cases"][0]["systems_involved"], ["spreadsheets"])
        self.assertEqual(stats["systems_dropped_not_in_documents"], 1)

    def test_ungrounded_numbers_drop_list_entries_and_sentences_only(self):
        item = uc("a", current_process=["Wait 14 days", "Reconcile monthly"],
                  problem="Reconciliation takes 3 days. It then waits 99 days for approval.")
        clean, _ = repair_explicit(explicit(item), DOCS)
        got = clean["explicit_use_cases"][0]
        self.assertEqual(got["current_process"], ["Reconcile monthly"])
        self.assertEqual(got["problem"], "Reconciliation takes 3 days.")

    def test_item_whose_only_problem_sentence_is_ungrounded_is_dropped(self):
        clean, stats = repair_explicit(explicit(uc("a", problem="It takes 99 days.")), DOCS)
        self.assertEqual(clean["explicit_use_cases"], [])
        self.assertEqual(stats["use_case_dropped_text_unsupported"], 1)

    def test_owner_name_must_exist_and_role_must_be_literal(self):
        owners = [{"name": "Ana Lee", "role": "Head of Billing"}, {"name": "Zed Nobody", "role": "x"}]
        clean, stats = repair_explicit(explicit(uc("a", owners=owners)), DOCS)
        self.assertEqual(clean["explicit_use_cases"][0]["owners"], [{"name": "Ana Lee"}])
        self.assertEqual(stats["owner_roles_dropped_not_in_documents"], 1)
        self.assertEqual(stats["owners_dropped_name_not_in_documents"], 1)

    def test_name_order_does_not_matter(self):
        clean, _ = repair_explicit(explicit(uc("a", owners=[{"name": "Lee, Ana"}])), DOCS)
        self.assertEqual(clean["explicit_use_cases"][0]["owners"], [{"name": "Lee, Ana"}])

    def test_wrong_speaker_is_removed_but_quote_kept(self):
        wrong = {**E1, "speaker": "Somebody Else"}
        clean, stats = repair_explicit(explicit(uc("a", evidence=[wrong])), DOCS)
        self.assertNotIn("speaker", clean["explicit_use_cases"][0]["evidence"][0])
        self.assertEqual(stats["speaker_removed"], 1)

    def test_stakeholder_without_a_grounded_name_is_dropped(self):
        heard = {**EMPTY, "stakeholders": [{"text": "x", "tag": "stated", "evidence": [E1], "name": "Ana Lee"},
                                           {"text": "y", "tag": "stated", "evidence": [E1]}]}
        clean, _ = repair_explicit(explicit(heard=heard), DOCS)
        self.assertEqual([s["name"] for s in clean["what_we_heard"]["stakeholders"]], ["Ana Lee"])

    def test_hidden_findings_need_category_and_why_hidden(self):
        raw = copy.deepcopy(HIDDEN)
        raw["hidden_use_cases"].append({**raw["hidden_use_cases"][0], "title": "no why", "why_hidden": ""})
        raw["hidden_use_cases"].append({**raw["hidden_use_cases"][0], "title": "bad cat", "category": "EXPLICIT"})
        clean, _ = repair_hidden(raw, DOCS)
        self.assertEqual([u["title"] for u in clean["hidden_use_cases"]], ["Credit approval ownership"])


class AssembleTests(unittest.TestCase):
    def build(self, ex, hi=None):
        ex, _ = repair_explicit(ex, DOCS)
        hi, _ = repair_hidden(hi or {"hidden_use_cases": [], "inferred_heard_items": EMPTY, "open_questions": []}, DOCS)
        return assemble_report(ex, hi, DOCS, META)

    def test_assembled_report_is_valid_and_labelled(self):
        ex, _ = repair_explicit(EXPLICIT, DOCS)
        hi, _ = repair_hidden(HIDDEN, DOCS)
        r = assemble_report(ex, hi, DOCS, META)
        self.assertEqual(validate_report(r, BY_ID), [])
        self.assertEqual(r["use_cases"][0]["category"], "EXPLICIT")
        self.assertNotIn("hypothesis_label", r["use_cases"][0])
        self.assertEqual(r["use_cases"][1]["hypothesis_label"], "hypothesis, not confirmed")
        self.assertEqual(r["top_hidden_opportunities"][0]["use_case_id"], "UC-2")

    def test_ranking_priority_then_confidence_then_breadth_then_order(self):
        cases = [uc("low prio", priority=3), uc("medium conf", priority=1, confidence="medium"),
                 uc("high conf one source", priority=1), uc("high conf two sources", priority=1, evidence=[E1, E3]),
                 uc("tie later", priority=1, evidence=[E1, E3])]
        r = self.build(explicit(*cases))
        self.assertEqual([u["title"] for u in r["use_cases"]],
                         ["high conf two sources", "tie later", "high conf one source", "medium conf", "low prio"])
        self.assertEqual([u["rank"] for u in r["use_cases"]], [1, 2, 3, 4, 5])

    def test_duplicate_titles_collapse_and_cap_applies(self):
        r = self.build(explicit(uc("Same"), uc("same")))
        self.assertEqual(len(r["use_cases"]), 1)
        many = explicit(*[uc(f"case {a}{b}") for a in "abcde" for b in "abcdefg"][:MAX_USE_CASES + 6])
        self.assertEqual(len(self.build(many)["use_cases"]), MAX_USE_CASES)

    def test_missing_company_value_gets_a_neutral_default(self):
        item = uc("a")
        item.pop("company_value")
        self.assertTrue(self.build(explicit(item))["use_cases"][0]["company_value"])

    def test_top_opportunities_are_the_first_five_non_explicit(self):
        hidden = {"hidden_use_cases": [uc(f"hidden {c}", category="LATENT", why_hidden="w", evidence=[E2, E3], priority=1,
                                          problem="Approvals are chased by email")
                                       for c in "abcdefg"], "inferred_heard_items": EMPTY, "open_questions": []}
        r = self.build(explicit(uc("e")), hidden)
        self.assertEqual(len(r["top_hidden_opportunities"]), 5)
        cats = {u["id"]: u["category"] for u in r["use_cases"]}
        self.assertTrue(all(cats[t["use_case_id"]] != "EXPLICIT" for t in r["top_hidden_opportunities"]))

    def test_heard_items_are_capped_and_numbered_and_questions_deduped(self):
        heard = {**EMPTY, "goals": [{"text": f"goal {c}", "tag": "stated", "evidence": [E1]} for c in "abcdefghijkl"]}
        q = {"question": "Who owns it?", "reason": "unclear"}
        r = self.build(explicit(uc("a"), heard=heard, questions=[q, {**q, "question": "who owns it"}]))
        self.assertEqual(len(r["what_we_heard"]["goals"]), 8)
        self.assertEqual(r["what_we_heard"]["goals"][0]["id"], "H-1")
        self.assertEqual(len(r["gaps_and_open_questions"]), 1)

    def test_every_quote_in_the_report_is_unchanged_from_the_pass(self):
        r = self.build(EXPLICIT)
        self.assertEqual(r["use_cases"][0]["evidence"][0]["quote"], E1["quote"])


if __name__ == "__main__":
    unittest.main()
