import threading
import unittest
from unittest import mock

import main


class Tracker:
    def __init__(self):
        self.events = []

    def add(self, name, metadata):
        self.events.append(("add", name, metadata))
        return "task-1"

    def done(self, task_id):
        self.events.append(("done", task_id))


class StartBackgroundTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from discovery_agent.data.storage import LocalStorage
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = LocalStorage(self.tmp.name)

    def run_and_wait(self, side_effect=None):
        tracker, finished = Tracker(), threading.Event()

        def fake(payload, **kw):
            try:
                if side_effect:
                    raise side_effect
            finally:
                finished.set()

        with mock.patch.object(main, "handle_invocation", fake):
            out = main.start_background({"engagement": "Acme", "run_id": "r1"}, tracker, self.storage)
            self.assertTrue(finished.wait(5))
        for t in threading.enumerate():
            if t.name.startswith("run-"):
                t.join(5)
        return out, tracker

    def test_returns_immediately_and_registers_the_busy_task(self):
        out, tracker = self.run_and_wait()
        self.assertEqual(out["status"], "accepted")
        self.assertEqual(out["status_key"], "output/Acme/r1/status.json")
        self.assertEqual(tracker.events, [("add", "discovery_run", {"run_id": "r1"}), ("done", "task-1")])

    def test_task_is_completed_even_when_the_run_crashes(self):
        _, tracker = self.run_and_wait(side_effect=RuntimeError("boom"))
        self.assertEqual(tracker.events[-1], ("done", "task-1"))

    def test_bad_payload_is_rejected_before_anything_starts(self):
        tracker = Tracker()
        with self.assertRaises(ValueError):
            main.start_background({"engagement": "../x"}, tracker, self.storage)
        self.assertEqual(tracker.events, [])

    def test_duplicate_delivery_does_not_start_a_second_run(self):
        import json as j
        for state, expected in (("running", "already_started"), ("succeeded", "already_started"), ("failed", "accepted")):
            self.storage.write_bytes("output/Acme/r1/status.json", j.dumps({"state": state}).encode())
            tracker = Tracker()
            with mock.patch.object(main, "handle_invocation", lambda *a, **k: None):
                out = main.start_background({"engagement": "Acme", "run_id": "r1"}, tracker, self.storage)
            for t in threading.enumerate():
                if t.name.startswith("run-"):
                    t.join(5)
            self.assertEqual(out["status"], expected, state)
            self.assertEqual(bool(tracker.events), expected == "accepted")

    def test_resume_may_restart_a_finished_run(self):
        import json as j
        self.storage.write_bytes("output/Acme/r1/status.json", j.dumps({"state": "succeeded"}).encode())
        with mock.patch.object(main, "handle_invocation", lambda *a, **k: None):
            out = main.start_background({"engagement": "Acme", "run_id": "r1", "options": {"resume": True}},
                                                 Tracker(), self.storage)
        for t in threading.enumerate():
            if t.name.startswith("run-"):
                t.join(5)
        self.assertEqual(out["status"], "accepted")


class RouteTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from discovery_agent.data.storage import LocalStorage
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = LocalStorage(self.tmp.name)

    def _route(self, payload):
        seen = []
        with mock.patch.object(main, "handle_invocation", lambda p, **k: seen.append(p)):
            out = main.route(payload, Tracker(), self.storage)
        for t in threading.enumerate():
            if t.name.startswith("run-"):
                t.join(5)
        return out, seen

    def test_ping_makes_no_run(self):
        out, seen = self._route({"action": "ping"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(seen, [])

    def test_no_action_means_start(self):
        out, seen = self._route({"engagement": "Acme", "run_id": "r1"})
        self.assertEqual(out["status"], "accepted")
        self.assertEqual(seen[0]["engagement"], "Acme")

    def test_resume_with_answers_sets_the_option(self):
        self.storage.write_bytes("output/Acme/r1/status.json", b'{"state": "running"}')
        out, seen = self._route({"action": "resume_with_answers", "engagement": "Acme", "run_id": "r1"})
        self.assertEqual(out["status"], "accepted")
        self.assertTrue(seen[0]["options"]["resume_with_answers"])

    def test_bad_payload_and_unknown_action_are_errors(self):
        self.assertEqual(self._route({"engagement": "../x"})[0]["status"], "error")
        self.assertEqual(self._route({"action": "drop_tables"})[0]["status"], "error")
        self.assertEqual(main.route("not a dict")["status"], "error")


if __name__ == "__main__":
    unittest.main()
