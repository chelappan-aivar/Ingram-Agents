"""The worker's view of the SOW agent's job items, in the shape the agent really writes (sow_generator/jobs.py)."""
import json
import sys
from pathlib import Path

import pytest
from fakes import SOW_MARKDOWN, UPSTREAM, FakeAgents

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployment" / "api"))


class FakeJobsTable:
    def __init__(self, items):
        self.items, self.gets = items, []

    def get_item(self, Key):          # noqa: N803
        self.gets.append(Key)
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}


@pytest.fixture
def worker(monkeypatch):
    import worker as w

    monkeypatch.setenv("SOW_DATA_BUCKET", "sow-bucket")
    return w


def _use(worker, monkeypatch, items, s3=None):
    table = FakeJobsTable(items)
    monkeypatch.setattr(worker, "sow_jobs", lambda: table)
    monkeypatch.setattr(worker, "_s3_text", lambda key: (s3 or {})[key])
    return table


def test_the_job_item_is_read_with_the_sort_key_the_agent_writes(worker, monkeypatch):
    """The agent upserts JOB#<id> / META. Reading 'JOB' would wait forever on every SOW stage."""
    table = _use(worker, monkeypatch, {("JOB#abc", "META"): {"status": "completed", "markdown": "# SOW"}})
    assert worker.read_sow_job("abc")["markdown"] == "# SOW"
    assert table.gets == [{"pk": "JOB#abc", "sk": "META"}]


def test_a_job_that_has_not_started_writing_yet_reads_as_empty_not_an_error(worker, monkeypatch):
    _use(worker, monkeypatch, {})
    assert worker.read_sow_job("abc") == {}


def test_a_draft_too_big_for_dynamodb_is_loaded_back_from_s3(worker, monkeypatch):
    item = {"status": "completed", "markdown": None, "markdown_in_s3": True, "markdown_key": "sow/OPP/s/sow.md"}
    _use(worker, monkeypatch, {("JOB#abc", "META"): item}, s3={"sow/OPP/s/sow.md": SOW_MARKDOWN})
    assert worker.read_sow_job("abc")["markdown"] == SOW_MARKDOWN


def test_a_large_check_result_is_loaded_back_from_s3(worker, monkeypatch):
    check = {"rules": [{"id": "x", "status": "pass"}], "issues": []}
    item = {"status": "completed", "check": None, "check_key": "sow/results/j/check.json"}
    _use(worker, monkeypatch, {("JOB#abc", "META"): item}, s3={"sow/results/j/check.json": json.dumps(check)})
    assert worker.read_sow_job("abc")["check"] == check


def test_an_inline_result_is_left_alone(worker, monkeypatch):
    check = {"rules": [], "issues": []}
    _use(worker, monkeypatch, {("JOB#abc", "META"): {"status": "completed", "check": check, "check_key": "ignored"}})
    assert worker.read_sow_job("abc")["check"] == check


def test_the_generate_request_carries_the_customer_name_the_agent_requires():
    """GenerateRequest.customer_name is required; leaving it out makes the agent reject the request."""
    from orchestrator.agents import JobPoller
    from orchestrator.contracts import PipelineState
    from orchestrator.pipeline import Pipeline

    agents = FakeAgents()
    poller = JobPoller(agents.read_job, interval=0, timeout=5, sleep=lambda _s: None)
    state = PipelineState(run_id="r1", template_id="aiml/ai-ml", upstream=dict(UPSTREAM))
    Pipeline(agents, poller=poller).advance(state)
    request = agents.payload_for("sow:generate")["request"]
    assert request["customer_name"] == "Fabrikam Insurance"
    assert request["opportunity_id"] == "OPP-2026-0517" and request["template_id"] == "aiml/ai-ml"
