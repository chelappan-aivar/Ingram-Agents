"""The engagement overview and report routes (api/overview.py, handler.py) and the context in the Markdown report."""
import json
import unittest

from discovery_agent.rendering.readme import render_markdown
from tests.test_api_handler import call, load_handler
from tests.test_render import report

import overview

CONTEXT = {
    "header": {"customer": "Woodgrove Bank", "seller": "Priya Nair", "opportunity_size_usd": 180000,
               "opportunity_size_basis": "CRM estimated value", "crm_url": "https://org.crm.dynamics.com/x",
               "aws_program": "MAP", "funding_status": "decided", "estimated_funding_usd": 54000,
               "intake_checked_at": "2026-10-27T10:00:00Z", "evil": "dropped"},
    "participants": [{"name": "Priya Nair", "side": "ingram", "role": "Seller"},
                     {"name": "Hannah Clarke", "side": "partner"}, {"name": "Dana Cole", "side": "customer"},
                     {"name": "Bad", "side": "attacker"}],
    "calls": [{"title": "Kickoff", "date": "2026-10-20", "summary": "Agreed scope", "documents": ["01_a.txt"]}],
}
REPORT_DOC = {"meta": {"sources": [{"id": "01_a.txt", "kind": "transcript", "label": "Oct 20"}]},
              "use_cases": [{"category": "EXPLICIT"}, {"category": "LATENT"}],
              "gaps_and_open_questions": [{"status": "open"}, {"status": "resolved"}],
              "what_we_heard": {"stakeholders": [
                  {"name": "Cole, Dana", "role": "CIO", "evidence": [{"source": "01_a.txt", "quote": "q"}]},
                  {"name": "Sam Roe", "role": "CFO", "evidence": [{"source": "01_a.txt", "quote": "q"}]}]}}


class OverviewTests(unittest.TestCase):
    def test_context_is_cleaned(self):
        c = overview.clean_context({**CONTEXT, "header": {**CONTEXT["header"], "crm_url": "javascript:alert(1)"}})
        self.assertNotIn("evil", c["header"])
        self.assertNotIn("crm_url", c["header"])
        self.assertEqual([p["name"] for p in c["participants"]], ["Priya Nair", "Hannah Clarke", "Dana Cole"])
        self.assertEqual(overview.clean_context("nonsense"), {})

    def test_stakeholders_by_side_and_unplaced(self):
        out = overview.stakeholders(overview.clean_context(CONTEXT), REPORT_DOC)
        self.assertEqual([p["name"] for p in out["ingram"]], ["Priya Nair"])
        self.assertEqual(out["customer"][0]["role"], "CIO")            # 'Cole, Dana' matched 'Dana Cole'
        self.assertEqual(out["customer"][0]["sources"], ["01_a.txt"])
        self.assertEqual([p["name"] for p in out["unplaced"]], ["Sam Roe"])  # not in any call: never guessed

    def test_summary_lines(self):
        lines = overview.done(overview.clean_context(CONTEXT), REPORT_DOC, "succeeded")
        self.assertIn("MAP selected (decided)", lines[0])
        self.assertIn("54,000 USD", lines[0])
        self.assertTrue(lines[1].startswith("1 Live Assist call held"))
        self.assertIn("2 use cases (1 hidden) and 1 open questions", lines[2])

    def test_timeline_falls_back_to_sources_without_context(self):
        self.assertEqual(overview.timeline({}, REPORT_DOC), [{"title": "Oct 20"}])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.h = load_handler()
        self.base = "Woodgrove_Bank"
        s3 = self.h.s3().objects
        s3[f"input/{self.base}/_context.json"] = json.dumps(CONTEXT).encode()
        s3[f"output/{self.base}/r-1/status.json"] = json.dumps({"state": "succeeded", "completed_steps": []}).encode()
        s3[f"output/{self.base}/r-2/status.json"] = json.dumps(
            {"state": "running", "updated_at": self.h._iso(), "completed_steps": []}).encode()
        s3[f"output/{self.base}/r-1/discovery.json"] = json.dumps(REPORT_DOC).encode()
        s3[f"output/{self.base}/r-1/narrative.json"] = json.dumps({"executive_summary": ["x"]}).encode()

    def test_overview_while_running_has_context_but_no_report(self):
        status, out = call(self.h, "GET", "/runs/r-2/overview", query={"engagement": "Woodgrove Bank"})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["header"]["aws_program"], "MAP")
        self.assertEqual(out["summary"][-1], "Discovery is analysing the documents.")
        self.assertEqual(out["stakeholders"]["unplaced"], [])

    def test_overview_after_success_adds_report_findings(self):
        status, out = call(self.h, "GET", "/runs/r-1/overview", query={"engagement": "Woodgrove Bank"})
        self.assertEqual(status, 200, out)
        self.assertEqual([p["name"] for p in out["stakeholders"]["unplaced"]], ["Sam Roe"])

    def test_report_route(self):
        status, out = call(self.h, "GET", "/runs/r-1/report", query={"engagement": "Woodgrove Bank"})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["narrative"]["executive_summary"], ["x"])
        self.assertEqual(out["context"]["header"]["customer"], "Woodgrove Bank")

    def test_report_refused_until_succeeded(self):
        self.assertEqual(call(self.h, "GET", "/runs/r-2/report", query={"engagement": "Woodgrove Bank"})[0], 409)
        self.assertEqual(call(self.h, "GET", "/runs/nope/report", query={"engagement": "Woodgrove Bank"})[0], 404)


class ContextInReportTests(unittest.TestCase):
    def test_markdown_has_context_section_with_program_and_calls(self):
        md = render_markdown(report(), None, CONTEXT)
        self.assertIn("## Context from earlier agents", md)
        self.assertIn("MAP, decided", md)
        self.assertIn("2026-10-20 Kickoff: Agreed scope", md)

    def test_markdown_without_context_is_unchanged(self):
        self.assertEqual(render_markdown(report(), None), render_markdown(report(), None, None))
        self.assertNotIn("Context from earlier agents", render_markdown(report(), None))


if __name__ == "__main__":
    unittest.main()
