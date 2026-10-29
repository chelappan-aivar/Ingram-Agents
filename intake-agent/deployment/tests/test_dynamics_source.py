"""POST /runs: open Dynamics 365 opportunities become CRM-style documents for the agent."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for sub in ("api", "shared/python"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("RUNS_TABLE", "test-runs")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import dynamics  # noqa: E402
import handler  # noqa: E402

ROWS = [{"id": "1", "name": "Acme migration", "customer": "Acme", "description": "Move to AWS", "estimated_value": 90000,
         "close_date": None, "request_type": "", "workload": "SAP", "url": "https://crm/1"}]


class DynamicsSource(unittest.TestCase):
    def test_rows_become_crm_documents(self):
        with mock.patch.object(dynamics, "search_opportunities", return_value=ROWS) as search:
            docs = handler._dynamics_documents({"limit": 10})
        search.assert_called_once_with("", 10, "open")
        self.assertEqual(docs[0]["source"], "crm")
        self.assertIn("Customer: Acme", docs[0]["text"])
        self.assertNotIn("Estimated close date", docs[0]["text"])   # empty values are left out

    def test_limit_is_validated(self):
        for bad in (0, 51, "5", True):
            with self.assertRaises(handler.HttpError) as ctx:
                handler._dynamics_documents({"limit": bad})
            self.assertEqual(ctx.exception.status, 400)

    def test_dynamics_failure_is_a_502(self):
        with mock.patch.object(dynamics, "search_opportunities", side_effect=dynamics.DynamicsError("boom")):
            with self.assertRaises(handler.HttpError) as ctx:
                handler._dynamics_documents({})
        self.assertEqual(ctx.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
