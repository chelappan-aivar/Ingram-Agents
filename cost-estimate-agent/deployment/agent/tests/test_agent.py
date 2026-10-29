"""Unit tests for the cost estimate agent (no AWS, no model, no MCP server).

    cd deployment/agent && python -m pytest -q tests
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

import main
from cost_estimate import mcp_tool, orchestrator
from cost_estimate.service_validator import ServiceRequirement, ServiceValidator, post_validate
from cost_estimate.validators import validate_cost_data

ESTIMATE = {
    "estimate_name": "Cost Estimate", "estimate_id": "abc1234567890", "region": "us-east-1",
    "calculator_url": "https://calculator.aws/#/estimate?id=abc1234567890",
    "environments": {"Dev": {"services": [{"name": "Amazon EC2"}]},
                     "Prod": {"services": [{"name": "Amazon EC2"}, {"name": "Amazon S3"}]}},
}


# ---------------------------------------------------------------- validators / orchestrator
def test_validate_cost_data_accepts_a_full_estimate():
    assert validate_cost_data(ESTIMATE) is True


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.pop("calculator_url"), "calculator_url"),
    (lambda d: d.update(environments={}), "No environments"),
    (lambda d: d["environments"]["Dev"].update(services=[]), "empty services"),
])
def test_validate_cost_data_rejects_incomplete_estimates(mutate, message):
    data = copy.deepcopy(ESTIMATE)
    mutate(data)
    with pytest.raises(ValueError, match=message):
        validate_cost_data(data)


def test_count_services_sums_all_environments():
    assert orchestrator.count_services(ESTIMATE) == 3


def test_orchestrator_names_the_failing_stage(monkeypatch):
    orch = orchestrator.CostEstimateOrchestrator()
    monkeypatch.setattr(orch.mcp_tool, "create_estimate", lambda text, report=None: {"estimate_name": "x"})
    with pytest.raises(orchestrator.EstimateFailed) as exc:
        orch.generate_estimate("some infrastructure")
    assert exc.value.stage == "validate"

    def boom(text, report=None):
        raise RuntimeError("MCP failed after 3 attempts")
    monkeypatch.setattr(orch.mcp_tool, "create_estimate", boom)
    with pytest.raises(orchestrator.EstimateFailed) as exc:
        orch.generate_estimate("some infrastructure")
    assert exc.value.stage == "estimate"


def test_orchestrator_returns_summary_and_checklist(monkeypatch):
    orch = orchestrator.CostEstimateOrchestrator()

    def fake(text, report=None):
        report.update(checklist=[{"name": "Amazon EC2", "environment": "Prod"}], coverage={"missing": []}, attempts=1)
        return copy.deepcopy(ESTIMATE)
    monkeypatch.setattr(orch.mcp_tool, "create_estimate", fake)
    out = orch.generate_estimate("some infrastructure")
    assert out["services_count"] == 3 and out["calculator_url"] == ESTIMATE["calculator_url"]
    assert out["checklist"] == [{"name": "Amazon EC2", "environment": "Prod"}] and out["attempts"] == 1


# ---------------------------------------------------------------- main.py routing
def test_ping_makes_no_model_call(monkeypatch):
    monkeypatch.setattr(main, "get_orchestrator", lambda: pytest.fail("ping must not build an estimate"))
    out = main.invoke({"action": "ping"})
    assert out["status"] == "completed" and out["model_id"] == mcp_tool.DEFAULT_MODEL_ID


@pytest.mark.parametrize("payload", [{}, {"action": "nope"}, {"action": 3}, {"prompt": "old style payload"}])
def test_missing_or_unknown_action_fails(payload):
    out = main.invoke(payload)
    assert out["status"] == "failed" and out["stage"] == "input"


@pytest.mark.parametrize("text", [None, "short", "x" * 20_001, 42])
def test_estimate_input_bounds(text):
    out = main.invoke({"action": "estimate", "requirements": text})
    assert out["status"] == "failed" and out["stage"] == "input"


def test_estimate_failure_is_reported(monkeypatch):
    def fail(text):
        raise orchestrator.EstimateFailed("estimate", "MCP failed")
    monkeypatch.setattr(main, "get_orchestrator", lambda: SimpleNamespace(generate_estimate=fail))
    assert main.invoke({"action": "estimate", "requirements": "x" * 40}) == {
        "status": "failed", "stage": "estimate", "error": "MCP failed"}


# ---------------------------------------------------------------- configuration from the environment
def test_model_settings_come_from_env(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "us.anthropic.test")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "4096")
    assert mcp_tool.model_id() == "us.anthropic.test" and mcp_tool.max_output_tokens() == 4096
    monkeypatch.delenv("MODEL_ID")
    monkeypatch.delenv("MAX_OUTPUT_TOKENS")
    assert mcp_tool.model_id() == "global.anthropic.claude-sonnet-5-5" and mcp_tool.max_output_tokens() == 32000


def test_mcp_command(monkeypatch):
    monkeypatch.setenv("MCP_COMMAND", "sample-aws-pricing-calculator-mcp --flag")
    assert mcp_tool.mcp_command() == ["sample-aws-pricing-calculator-mcp", "--flag"]
    monkeypatch.delenv("MCP_COMMAND")
    monkeypatch.setattr(mcp_tool.shutil, "which", lambda name: None)
    assert mcp_tool.mcp_command() == ["npx", "-y", "sample-aws-pricing-calculator-mcp@1.3.3"]


# ---------------------------------------------------------------- validation hook + post-validation
class FakeJudge:
    def __init__(self, missing):
        self._missing = missing
        self.calls = 0

    def missing(self, required, provided):
        self.calls += 1
        return list(self._missing)


def _event(services):
    return SimpleNamespace(tool_use={"name": "build_estimate", "input": {"services": json.dumps(services)}},
                           cancel_tool=False)


def test_pre_validation_blocks_then_lets_through():
    checklist = [ServiceRequirement(name="AWS Batch")]
    validator = ServiceValidator(checklist, FakeJudge(["AWS Batch"]), max_rejections=2)
    for _ in range(2):
        ev = _event([{"service": "awsFargate"}])
        validator._before_tool_call(ev)
        assert "AWS Batch" in ev.cancel_tool
    ev = _event([{"service": "awsFargate"}])
    validator._before_tool_call(ev)
    assert ev.cancel_tool is False and validator.rejections == 2


def test_pre_validation_ignores_other_tools_and_fails_open():
    judge = FakeJudge(["x"])
    validator = ServiceValidator([ServiceRequirement(name="x")], judge)
    other = SimpleNamespace(tool_use={"name": "search_services", "input": {}}, cancel_tool=False)
    validator._before_tool_call(other)
    broken = SimpleNamespace(tool_use={"name": "build_estimate", "input": {"services": "not json"}}, cancel_tool=False)
    validator._before_tool_call(broken)
    assert other.cancel_tool is False and broken.cancel_tool is False and judge.calls == 0


def test_post_validate_returns_coverage_and_never_raises():
    saved = {"groups": {"g": {"name": "Prod", "services": {"a": {"serviceCode": "ec2", "serviceName": "EC2"}}}}}
    client = SimpleNamespace(call_tool_sync=lambda **kw: {"status": "success", "content": [{"text": json.dumps(saved)}]})
    out = post_validate(client, "https://calculator.aws/#/estimate?id=x", [ServiceRequirement(name="EC2")],
                        FakeJudge([]))
    assert out == {"saved": 1, "requested": 1, "missing": []}
    assert post_validate(client, None, [], FakeJudge([])) is None

    def boom(**kw):
        raise RuntimeError("network down")
    assert post_validate(SimpleNamespace(call_tool_sync=boom), "id", [], FakeJudge([])) is None
