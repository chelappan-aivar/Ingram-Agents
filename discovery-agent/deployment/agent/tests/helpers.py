"""Shared synthetic data and stubs. Nothing here calls AWS."""
import json

from discovery_agent.analysis import pipeline
from discovery_agent.data.documents import Document
from discovery_agent.runtime.state import RunConfig, RunState, Stages
from discovery_agent.data.storage import LocalStorage

TEXT_A = ("Ana Lee\n0:10\nOur billing reconciliation takes 3 days every month and it is all manual in spreadsheets.\n"
          "Bo Chen\n0:30\nWe also keep chasing the credit approvals by email.")
TEXT_B = "Ana Lee\n0:05\nThe reconciliation backlog is still there, nobody owns the credit approvals."

DOCS = [Document("call_a", "transcript", TEXT_A, {"Ana Lee", "Bo Chen"}, "Sep 1 · Call A"),
        Document("call_b", "transcript", TEXT_B, {"Ana Lee"}, "Sep 8 · Call B")]

E1 = {"source": "call_a", "quote": "billing reconciliation takes 3 days every month", "speaker": "Ana Lee"}
E2 = {"source": "call_a", "quote": "keep chasing the credit approvals by email", "speaker": "Bo Chen"}
E3 = {"source": "call_b", "quote": "nobody owns the credit approvals", "speaker": "Ana Lee"}
EMPTY = {"goals": [], "pain_points": [], "current_state": [], "constraints": [], "stakeholders": []}

EXPLICIT = {
    "what_we_heard": {**EMPTY, "pain_points": [{"text": "Billing reconciliation is manual", "tag": "stated", "evidence": [E1]}]},
    "explicit_use_cases": [{"title": "Reconciliation agent", "problem": "Reconciliation is manual and takes 3 days a month",
                            "evidence": [E1], "customer_value": "Less manual effort", "validation_question": "Who does it?",
                            "confidence": "high", "company_value": "Pilot candidate", "priority": 1}],
    "open_questions": [],
}
HIDDEN = {
    "hidden_use_cases": [{"category": "LATENT", "title": "Credit approval ownership", "problem": "Approvals are chased by email",
                          "evidence": [E2, E3], "why_hidden": "Nobody said who owns it", "customer_value": "Clear ownership",
                          "company_value": "Scope expansion", "confidence": "low", "validation_question": "Who approves?",
                          "priority": 2}],
    "inferred_heard_items": EMPTY,
    "open_questions": [],
}
GOOD_MERGE = {
    "what_we_heard": {**EMPTY, "pain_points": [{"id": "H-1", "text": "Billing reconciliation is manual", "tag": "stated", "evidence": [E1]}]},
    "use_cases": [
        {"id": "UC-1", "rank": 1, "category": "EXPLICIT", "title": "Reconciliation agent",
         "problem": "Reconciliation is manual and takes 3 days a month", "evidence": [E1],
         "customer_value": "Less manual effort", "company_value": "Pilot candidate", "confidence": "high",
         "validation_question": "Who does it?"},
        {"id": "UC-2", "rank": 2, "category": "LATENT", "title": "Credit approval ownership",
         "problem": "Approvals are chased by email", "evidence": [E2, E3], "why_hidden": "Nobody said who owns it",
         "hypothesis_label": "hypothesis, not confirmed", "customer_value": "Clear ownership",
         "company_value": "Scope expansion", "confidence": "low", "validation_question": "Who approves?"},
    ],
    "top_hidden_opportunities": [{"use_case_id": "UC-2", "title": "Credit approval ownership",
                                  "why_it_matters": "Repeated across both calls"}],
    "gaps_and_open_questions": [{"id": "Q-1", "question": "Who owns approvals?", "reason": "Unclear in both calls"}],
}
BAD_MERGE = {k: v for k, v in GOOD_MERGE.items() if k != "gaps_and_open_questions"}  # schema error
NARRATIVE = {"executive_summary": ["Summary."], "use_case_summaries": {"UC-1": "s1", "UC-2": "s2"},
             "opportunity_narratives": {"UC-2": "n2"}}


class FakeAgent:
    """Stands in for a Strands Agent: returns scripted JSON and records the prompts it received."""

    def __init__(self, stage, responses, calls):
        self.stage, self.responses, self.calls = stage, list(responses), calls

    def __call__(self, prompt):
        self.calls.append((self.stage, prompt))
        return json.dumps(self.responses.pop(0))


class StubStages(Stages):
    """Stages with recorded, scripted behaviour. Rendering uses the real (offline) renderers."""

    def __init__(self, calls, narrative=NARRATIVE, extract_error=None):
        super().__init__(build_corpus=pipeline.build_corpus)
        self.calls = calls
        self._narrative, self._extract_error = narrative, extract_error
        self.extract_explicit = self._extract
        self.find_hidden = self._hidden
        self.merge_report = self._merge
        self.write_narrative = self._narr
        self.resolve_clarifications = self._resolve

    def _resolve(self, corpus, report, questions, answers, docs, instructions=""):
        self.calls.append("resolve")
        # mimic the real stage's bookkeeping: mark reviewer-answered questions resolved, record meta
        from discovery_agent.analysis import pipeline
        out = dict(report)
        out["gaps_and_open_questions"] = pipeline.clarification_outcomes(questions, answers)
        meta = dict(report.get("meta", {}))
        meta["clarifications"] = pipeline.clarifications_meta(questions, answers)
        out["meta"] = meta
        return out

    def _extract(self, corpus, docs, instructions=""):
        self.calls.append("extract")
        if self._extract_error:
            raise RuntimeError(self._extract_error)
        return EXPLICIT

    def _hidden(self, corpus, explicit, docs, instructions=""):
        self.calls.append("hidden")
        return HIDDEN

    def _merge(self, explicit, hidden, docs, engagement, instructions=""):
        self.calls.append("merge")
        return {"schema_version": "1.0", "meta": pipeline.build_meta(docs, engagement, instructions), **GOOD_MERGE}

    def _narr(self, report, instructions=""):
        self.calls.append("narrative")
        return self._narrative


def seeded_storage(tmp):
    st = LocalStorage(tmp)
    st.write_bytes("input/Acme/call_a.txt", TEXT_A.encode())
    st.write_bytes("input/Acme/call_b.txt", TEXT_B.encode())
    st.write_bytes("input/Acme/_run", b"")  # marker, not a document
    st.write_bytes("input/Acme/diagram.png", b"\x89PNG")  # unsupported type, skipped
    return st


def make_state(storage, stages, **options):
    cfg = RunConfig.from_payload({"engagement": "Acme", "run_id": "r1", "options": options})
    return RunState(cfg, storage, stages)
