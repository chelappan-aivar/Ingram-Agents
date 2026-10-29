"""The pipeline routes (api/pipeline.py + handler.py): Intake/Live Assist opportunities into a Discovery run and back.

Live Assist is a local HTTP server running the same contract (GET /intake/opportunities, GET .../export,
POST .../discovery), checking the access key. Nothing calls AWS.
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.test_api_handler import call, event, load_handler

OPP = "11111111-1111-4111-8111-111111111111"
KEY = "la-access-key-123"
TRANSCRIPT = "\n".join(f"Hannah Clarke\n{i}:00\nLine {i} about 400 VMs." for i in range(12))


class FakeLiveAssist(BaseHTTPRequestHandler):
    registered: list = []
    seen_keys: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self):
        self.seen_keys.append(self.headers.get("X-Access-Key"))
        if self.headers.get("X-Access-Key") != KEY:
            self._send(401, {"error": {"code": "unauthorized", "message": "Missing or wrong x-access-key header"}})
            return False
        return True

    def do_GET(self):  # noqa: N802
        if not self._auth():
            return
        if self.path == "/intake/opportunities":
            return self._send(200, {"enabled": True, "opportunities": [{
                "intake_opportunity_id": OPP, "name": "Woodgrove - DC exit", "customer": "Woodgrove Bank",
                "decision": {"status": "decided", "program": "MAP", "total_usd": None, "rationale": "x"},
                "checked_at": "2026-10-27T10:00:00Z", "call_count": 1, "last_call_at": "2026-10-27T12:00:00Z"}]})
        if self.path == f"/intake/opportunities/{OPP}/export":
            return self._send(200, {"engagement": "Woodgrove Bank Woodgrove - DC exit", "call_ids": ["call-" + "a" * 32],
                                    "documents": [
                                        {"name": "00_intake_record.md", "kind": "intake", "text": "# Intake record"},
                                        {"name": "01_2026-10-27_dc_transcript.txt", "kind": "transcript", "text": TRANSCRIPT},
                                        {"name": "../evil.txt", "kind": "x", "text": "dropped"}],
                                    "context": {"header": {"customer": "Woodgrove Bank", "aws_program": "MAP"},
                                                "participants": [], "calls": []}})
        self._send(404, {"error": {"code": "intake_opportunity_not_found", "message": "No such Intake opportunity"}})

    def do_POST(self):  # noqa: N802
        if not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.registered.append((self.path, body))
        self._send(201, {"run_id": body["run_id"], "state": "starting"})


REAL_SERVER_KEY = load_handler() and sys.modules["pipeline"].server_key   # before any test replaces it


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeLiveAssist)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeLiveAssist.registered, FakeLiveAssist.seen_keys = [], []
        self.h = load_handler()
        pipeline = sys.modules["pipeline"]
        pipeline.LIVE_ASSIST_API_URL = self.url
        pipeline.LIVE_ASSIST_KEY_SECRET = "live-assist/access-key"
        pipeline.server_key = REAL_SERVER_KEY
        self._real_server_key = REAL_SERVER_KEY

    def keyed(self, method, path, body=None, key=KEY):
        """The Lambda sends the key it reads from Secrets Manager; key=None means that secret is not set up."""
        pipeline = sys.modules["pipeline"]
        if key:
            pipeline.server_key = lambda: key
        else:
            pipeline.LIVE_ASSIST_KEY_SECRET = ""
            pipeline.server_key = self._real_server_key
        resp = self.h.handler(event(method, path, body), None)
        return resp["statusCode"], json.loads(resp["body"])

    def test_lists_opportunities_with_the_callers_key(self):
        status, out = self.keyed("GET", "/pipeline/opportunities")
        self.assertEqual(status, 200)
        self.assertEqual(out["opportunities"][0]["decision"]["program"], "MAP")
        self.assertEqual(out["opportunities"][0]["call_count"], 1)
        self.assertEqual(FakeLiveAssist.seen_keys, [KEY])

    def test_no_key_in_the_request_is_needed(self):
        ev = event("GET", "/pipeline/opportunities")
        self.assertNotIn("headers", ev)
        self.assertEqual(self.keyed("GET", "/pipeline/opportunities")[0], 200)

    def test_not_connected_without_the_secret(self):
        self.assertEqual(self.keyed("GET", "/pipeline/opportunities", key=None)[0], 404)

    def test_refusal_passed_on(self):
        status, out = self.keyed("GET", "/pipeline/opportunities", key="wrong-key-123")
        self.assertEqual(status, 401)
        self.assertIn("refused", out["error"])

    def test_import_writes_documents_to_the_engagement_input(self):
        status, out = self.keyed("POST", f"/pipeline/opportunities/{OPP}/import")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["engagement"], "Woodgrove Bank Woodgrove - DC exit")
        self.assertEqual([d["name"] for d in out["documents"]], ["00_intake_record.md", "01_2026-10-27_dc_transcript.txt"])
        prefix = "input/Woodgrove_Bank_Woodgrove_-_DC_exit/"
        self.assertEqual(self.h.s3().objects[prefix + "01_2026-10-27_dc_transcript.txt"], TRANSCRIPT.encode())
        self.assertFalse(any("evil" in k for k in self.h.s3().objects))
        context = json.loads(self.h.s3().objects[prefix + "_context.json"])
        self.assertEqual(context["header"]["aws_program"], "MAP")

    def test_imported_transcript_is_read_as_a_transcript(self):
        import tempfile
        from pathlib import Path
        from discovery_agent.data.documents import load_document
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "01_2026-10-27_dc_transcript.txt"
            p.write_text(TRANSCRIPT)
            doc = load_document(p)
        self.assertEqual(doc.kind, "transcript")
        self.assertEqual(doc.speakers, {"Hannah Clarke"})

    def test_bad_opportunity_id(self):
        self.assertEqual(self.keyed("POST", "/pipeline/opportunities/not-a-guid/import")[0], 404)

    def test_run_from_pipeline_registers_back(self):
        self.keyed("POST", f"/pipeline/opportunities/{OPP}/import")
        status, out = self.keyed("POST", "/runs", {"engagement": "Woodgrove Bank Woodgrove - DC exit",
                                                    "pipeline_opportunity_id": OPP, "pipeline_call_ids": ["call-x"]})
        self.assertEqual(status, 202, out)
        self.assertTrue(out["linked_to_live_assist"])
        path, body = FakeLiveAssist.registered[-1]
        self.assertEqual(path, f"/intake/opportunities/{OPP}/discovery")
        self.assertEqual(body["run_id"], out["run_id"])
        runs = call(self.h, "GET", "/runs")[1]["runs"]
        self.assertEqual(runs[0]["pipeline_opportunity_id"], OPP)

    def test_repeated_checks_of_one_opportunity_show_once(self):
        pipeline = sys.modules["pipeline"]
        def opp(i, name, checked, calls=0):
            return {"intake_opportunity_id": i, "name": name, "customer": "Tailwind Robotics", "checked_at": checked,
                    "call_count": calls}
        done = {"status": "succeeded", "run_id": "r"}
        pairs = [(opp("new", "Tailwind - VMware exit", "2026-10-28"), None),
                 (opp("old", "Tailwind VMware exit", "2026-10-26"), done),
                 (opp("other", "Tailwind - data lake", "2026-10-27"), None),
                 (opp("n1", "Nimbus - rehost", "2026-10-26"), None), (opp("n2", "Nimbus - rehost", "2026-10-28"), None)]
        kept = [o["intake_opportunity_id"] for o, _ in pipeline.dedupe(pairs)]
        self.assertEqual(kept, ["old", "other", "n2"])   # a finished run beats a newer check; else the newest

    def test_only_the_latest_intake_run_unless_older_work_exists(self):
        pipeline = sys.modules["pipeline"]
        def opp(i, checked, calls=0):
            return {"intake_opportunity_id": i, "name": i, "customer": "C", "checked_at": checked, "call_count": calls}
        pairs = [(opp("a", "2026-10-28T19:47:30Z"), None), (opp("b", "2026-10-28T19:47:40.5Z"), None),
                 (opp("old", "2026-10-26T19:58:00Z"), None), (opp("old-run", "2026-10-26T19:58:00Z"), {"status": "succeeded"}),
                 (opp("old-call", "2026-10-26T19:58:00Z", calls=1), None)]
        kept = [o["intake_opportunity_id"] for o, _ in pipeline.latest_run_only(pairs)]
        self.assertEqual(kept, ["a", "b", "old-run", "old-call"])

    def test_overview_lists_everything_unless_scope_latest(self):
        self.assertEqual(self.keyed("GET", "/pipeline/overview")[1]["count"], 1)
        ev = event("GET", "/pipeline/overview", query={"scope": "latest"})
        self.assertEqual(json.loads(self.h.handler(ev, None)["body"])["count"], 1)

    def test_run_still_starts_when_registering_fails(self):
        self.keyed("POST", f"/pipeline/opportunities/{OPP}/import")
        status, out = self.keyed("POST", "/runs", {"engagement": "Woodgrove Bank Woodgrove - DC exit",
                                                    "pipeline_opportunity_id": OPP}, key="wrong-key-123")
        self.assertEqual(status, 202)
        self.assertFalse(out["linked_to_live_assist"])

    def test_overview_joins_each_opportunity_with_its_discovery_output(self):
        h, eng = self.h, "Woodgrove Bank Woodgrove - DC exit"
        self.keyed("POST", f"/pipeline/opportunities/{OPP}/import")
        self.assertEqual(self.keyed("GET", "/pipeline/overview")[1]["rows"][0]["discovery"], None)
        status, run = self.keyed("POST", "/runs", {"engagement": eng, "pipeline_opportunity_id": OPP})
        base = f"output/{h._folder(eng)}/{run['run_id']}/"
        h.s3().objects[base + "status.json"] = json.dumps({"state": "succeeded", "completed_steps": []}).encode()
        h.s3().objects[base + "discovery.json"] = json.dumps({
            "meta": {"generated_at": "2026-10-29"}, "use_cases": [{"title": "DR", "category": "LATENT", "rank": 1}],
            "what_we_heard": {"pain_points": [{"text": "Slow backups"}]}, "gaps_and_open_questions": []}).encode()
        h.s3().objects[base + "narrative.json"] = json.dumps({"executive_summary": ["Summary."]}).encode()
        status, out = self.keyed("GET", "/pipeline/overview")
        self.assertEqual(status, 200, out)
        row = out["rows"][0]
        self.assertEqual(row["intake"]["decision"]["program"], "MAP")
        self.assertEqual(row["discovery"]["state"], "succeeded")
        self.assertEqual(row["discovery"]["summary"]["executive_summary"], ["Summary."])
        self.assertEqual(row["discovery"]["summary"]["pain_points"], ["Slow backups"])
        self.assertEqual(out["with_discovery"], 1)

    def test_plain_runs_unchanged(self):
        self.h.s3().objects["input/Acme/a.txt"] = b"x"
        status, out = call(self.h, "POST", "/runs", {"engagement": "Acme"})
        self.assertEqual(status, 202)
        self.assertNotIn("linked_to_live_assist", out)
        self.assertEqual(FakeLiveAssist.registered, [])


if __name__ == "__main__":
    unittest.main()
