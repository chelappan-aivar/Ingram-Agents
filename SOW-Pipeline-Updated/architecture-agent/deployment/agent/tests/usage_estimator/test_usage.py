"""Usage Estimator (no AWS): tool spec, code rules, both renderers and the CLI, with FakeLLM."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from adr_generator.contracts import Design
from adr_generator.llm.bedrock import FakeLLM, LLMError
from tests.adr_generator.conftest import FIXTURES
from usage_estimator import cli as usage_cli
from usage_estimator.estimator import TOOL_NAME, build_tool_spec, build_user_message
from usage_estimator.registry import GROUPS, SERVICES
from usage_estimator.render import build_usage, render_assumptions
from usage_estimator.rules import apply
from tests.usage_estimator.test_registry import expected

DESIGN_RAW = json.loads((FIXTURES / "design_full.json").read_text(encoding="utf-8"))
DESIGN = Design.model_validate(DESIGN_RAW)


def fake_usage() -> dict:
    """A plausible `submit_usage` answer for design_full.json (C-01..C-03 are actor/external)."""
    fargate = lambda cid, tasks: {"component_id": cid, "vcpu_hours_per_month": tasks * 730,
                                  "memory_gb_per_month": tasks * 2 * 730, "operating_system": "Linux",
                                  "architecture": "arm64", "explanation": f"{tasks} task(s) x 1 vCPU x 730 h.",
                                  "assumed_fields": []}
    return {
        "workload_profile": [
            {"name": "Billing agents", "value": "200", "unit": "users", "basis": "stated", "reason": "REQ-02"},
            {"name": "Disputes per day", "value": "1000", "unit": "per day", "basis": "assumed", "reason": "Typical."},
        ],
        "usage": {
            "application_load_balancer": [{"component_id": "C-04", "load_balancer_count": 1, "lcu_per_hour": 1.5,
                                           "rules_count": 4, "availability_zones": 2, "explanation": "Light API load.",
                                           "assumed_fields": ["rules_count"]}],
            "nat_gateway": [{"component_id": "C-05", "gateway_count": 1, "data_processed_gb": "40",
                             "explanation": "Ticketing calls.", "assumed_fields": []}],
            "internet_gateway": [{"component_id": "C-06", "data_transfer_in_gb": 20, "data_transfer_out_gb": 15,
                                  "attached_vpc_count": 1, "explanation": "API traffic.", "assumed_fields": []}],
            "aws_fargate": [fargate("C-07", 2), fargate("C-08", 1)],
            "amazon_ecr": [{"component_id": "C-09", "storage_gb": 4, "data_transfer_out_gb": 0, "repositories": 2,
                            "image_scans_per_month": 40, "explanation": "10 images each.", "assumed_fields": []}],
            "amazon_rds": [{"component_id": "C-10", "engine": "PostgreSQL", "instance_class": "db.t4g.medium",
                            "instance_count": 1, "storage_gb": 100, "multi_az": True, "backup_retention_days": 7,
                            "explanation": "Design storage.", "assumed_fields": []}],
            "amazon_s3": [{"component_id": "C-11", "storage_class": "Standard", "storage_gb": 250.5,
                           "get_requests_per_month": 90000, "put_requests_per_month": 30000,
                           "data_transfer_out_gb": -3, "explanation": "Invoices.", "assumed_fields": ["storage_gb"]}],
            "amazon_sqs": [{"component_id": "C-12", "queue_type": "Standard", "requests_per_month": 90000,
                            "average_message_size_kb": 2, "data_transfer_out_gb": 0, "explanation": "3 calls/msg.",
                            "assumed_fields": []}],
            "aws_lambda": [{"component_id": "C-13", "invocations_per_month": 30000, "average_duration_seconds": 1.2,
                            "memory_mb": 512, "architecture": "arm64", "explanation": "One per message.",
                            "assumed_fields": []}],
            "amazon_bedrock": [
                {"component_id": "C-14", "model_id": "us.anthropic.claude-sonnet-4-6",
                 "input_tokens_per_month": 60000000, "output_tokens_per_month": 6000000,
                 "invocations_per_month": 30000, "inference_type": "On-Demand", "explanation": "2k in per call.",
                 "assumed_fields": []},
                {"component_id": "C-14", "model_id": "amazon.titan-embed-text-v2:0",
                 "input_tokens_per_month": 5000000, "output_tokens_per_month": 0, "invocations_per_month": 30000,
                 "inference_type": "On-Demand", "explanation": "Embeddings.", "assumed_fields": []}],
            "aws_secrets_manager": [{"component_id": "C-15", "secrets_count": 3, "api_calls_per_month": 20000,
                                     "rotation_enabled": "yes", "rotation_frequency_days": 30, "explanation": "Cached.",
                                     "assumed_fields": []}],
            "aws_kms": [{"component_id": "C-16", "customer_managed_keys": 1, "api_calls_per_month": 100000,
                         "key_type": "Symmetric", "multi_region": False, "explanation": "Bucket keys.",
                         "assumed_fields": []}],
            "amazon_comprehend": [{"component_id": "C-99"}],
        },
        "warnings": ["The SRS gives no dispute volume; 1,000 per day is assumed."],
        "notes": ["Bedrock tokens dominate usage."],
    }


def test_tool_spec_is_built_from_the_registry():
    spec = build_tool_spec()["toolSpec"]
    assert spec["name"] == TOOL_NAME
    usage = spec["inputSchema"]["json"]["properties"]["usage"]["properties"]
    assert set(usage) == {s.key for s in SERVICES}
    rds = usage["amazon_rds"]["items"]["properties"]
    # code sets the single-AZ and derived fields; the model never sees them
    assert "multi_az" not in rds and "instance_class" in rds and "explanation" in rds
    assert "memory_gb" not in usage["aws_lambda"]["items"]["properties"]


def test_user_message_carries_srs_and_design():
    msg = build_user_message("srs.md", "The system shall X.", DESIGN)
    assert '<document file="srs.md">' in msg and "<design>" in msg and "C-10" in msg and TOOL_NAME in msg


def test_rules_force_single_az_derive_coerce_and_warn():
    est = apply(fake_usage(), DESIGN)
    by = {(e.component_id, e.service.name, e.values.get("model_id")): e for e in est.entries}
    alb = by[("C-04", "Application Load Balancer", None)]
    assert alb.values["availability_zones"] == 1 and alb.assumed_fields == ["rules_count"]
    assert by[("C-10", "Amazon RDS", None)].values["multi_az"] is False
    assert by[("C-13", "AWS Lambda", None)].values["memory_gb"] == 0.5
    assert by[("C-05", "NAT Gateway", None)].values == {"gateway_count": 1, "data_processed_gb": 40,
                                                       "availability_zones": 1}
    assert by[("C-15", "AWS Secrets Manager", None)].values["rotation_enabled"] is True
    s3 = by[("C-11", "Amazon S3", None)].values
    assert s3["storage_gb"] == 250.5 and s3["data_transfer_out_gb"] == 0
    w = "\n".join(est.warnings)
    assert est.warnings[0].startswith("The SRS gives no dispute volume")       # the model's warnings come first
    assert "data_transfer_out_gb was negative" in w
    assert "'amazon_comprehend', which is not in the cost-calculator schema" in w
    assert "C-17 Amazon CloudWatch has no usage entry" in w                         # coverage check
    assert "C-01" not in w and "C-02" not in w                                  # actors/external need no entry
    assert "more than one" not in w                                             # two Bedrock models are allowed


def test_rules_never_raise_on_junk():
    for junk in (None, [], "x", {"usage": "x"}, {"usage": {"aws_lambda": ["x", None, {}]}}):
        est = apply(junk, DESIGN)
        assert isinstance(est.warnings, list)


def test_usage_json_is_exactly_the_schema_shape():
    usage = build_usage(apply(fake_usage(), DESIGN))
    assert list(usage) == ["services"] and list(usage["services"]) == GROUPS
    schema = {e["service"]: list(e) for entries in expected().values() for e in entries}
    for group, entries in usage["services"].items():
        for e in entries:
            assert list(e) == schema[e["service"]], e["service"]   # same keys, same order, nothing extra
    assert [e["model_id"] for e in usage["services"]["ai_and_ml"]] == ["us.anthropic.claude-sonnet-4-6",
                                                                      "amazon.titan-embed-text-v2:0"]
    assert len(usage["services"]["serverless_compute"]) == 3   # Lambda + one Fargate entry per component
    assert usage["services"]["observability"] == []


def _run_folder(tmp_path) -> Path:
    run = tmp_path / "runs" / "run-1"
    (run / "input").mkdir(parents=True)
    (run / "input" / "srs.md").write_text("# SRS\nThe system shall let customers dispute a bill.\n")
    shutil.copyfile(FIXTURES / "design_full.json", run / "design.json")
    return run


def test_cli_writes_usage_files(tmp_path, settings):
    run, lines = _run_folder(tmp_path), []
    llm = FakeLLM(tool_responses=[fake_usage()])
    assert usage_cli.main([str(run)], settings=settings, llm=llm, out=lines.append) == 0
    usage = json.loads((run / "usage.json").read_text())
    assert list(usage["services"]) == GROUPS
    md = (run / "usage_assumptions.md").read_text()
    assert md.startswith("# Usage estimate: ") and md.index("## 1. Warnings") < md.index("## 2. Workload profile")
    assert "730 hours" in md and "C-10 Case Database" in md and "Bedrock tokens dominate usage." in md
    assert json.loads((run / "usage.raw.json").read_text()) == fake_usage()
    meta = json.loads((run / "usage_metadata.json").read_text())
    assert meta["status"] == "completed" and meta["counts"]["entries"] == 14
    assert "dispute a bill" in llm.tool_calls[0]["messages"][0]["content"][0]["text"]
    assert any("USAGE" in l for l in lines) and any("DONE" in l for l in lines)


def test_cli_failures(tmp_path, settings):
    lines: list[str] = []
    assert usage_cli.main([str(tmp_path)], settings=settings, llm=FakeLLM(), out=lines.append) == 2
    run = _run_folder(tmp_path)
    llm = FakeLLM(tool_responses=[LLMError("boom", "truncated")])
    assert usage_cli.main([str(run)], settings=settings, llm=llm, out=lines.append) == 1
    meta = json.loads((run / "usage_metadata.json").read_text())
    assert meta["status"] == "failed" and "boom" in meta["error"] and not (run / "usage.json").exists()


# ----------------------------------------------------------------- availability model -> usage.json
def _design_with_zones(*zones: str) -> Design:
    """design_full.json with its AZ boundaries replaced, so only the zone count differs."""
    raw = json.loads(json.dumps(DESIGN_RAW))
    raw["availability_zones"] = list(zones)
    raw.pop("availability_zone", None)
    raw["boundaries"] = [b for b in raw["boundaries"] if b.get("type") != "az"] + [
        {"id": f"az_{chr(97 + i)}", "type": "az", "name": z, "parent": "main_vpc"}
        for i, z in enumerate(zones)
    ]
    return Design.model_validate(raw)


def test_usage_follows_a_multi_az_design_so_the_calculator_prices_it():
    """The AZ count reaches the cost calculator through usage.json, so it must track the design."""
    est = apply(fake_usage(), _design_with_zones("us-east-1a", "us-east-1b"))
    by = {(e.component_id, e.service.name, e.values.get("model_id")): e for e in est.entries}
    assert by[("C-10", "Amazon RDS", None)].values["multi_az"] is True
    assert by[("C-04", "Application Load Balancer", None)].values["availability_zones"] == 2
    assert by[("C-05", "NAT Gateway", None)].values["availability_zones"] == 2


def test_usage_follows_a_single_az_design():
    est = apply(fake_usage(), _design_with_zones("us-east-1a"))
    by = {(e.component_id, e.service.name, e.values.get("model_id")): e for e in est.entries}
    # the model answered multi_az True / 2 zones; the design overrules it either way
    assert by[("C-10", "Amazon RDS", None)].values["multi_az"] is False
    assert by[("C-04", "Application Load Balancer", None)].values["availability_zones"] == 1


def test_three_zone_design_is_priced_for_three():
    est = apply(fake_usage(), _design_with_zones("us-east-1a", "us-east-1b", "us-east-1c"))
    by = {(e.component_id, e.service.name, e.values.get("model_id")): e for e in est.entries}
    assert by[("C-04", "Application Load Balancer", None)].values["availability_zones"] == 3
    assert by[("C-10", "Amazon RDS", None)].values["multi_az"] is True


def test_usage_prompt_and_report_state_the_designs_availability_model():
    from usage_estimator.estimator import build_system_prompt

    multi = _design_with_zones("us-east-1a", "us-east-1b")
    assert "two Availability Zones" in build_system_prompt(multi)
    assert "one Availability Zone" in build_system_prompt(_design_with_zones("us-east-1a"))

    md = render_assumptions(apply(fake_usage(), multi), multi,
                            source_name="srs.md", run_date="2026-10-09", model_id="m")
    assert "two Availability Zones (us-east-1a, us-east-1b)" in md
    assert "availability_zones is 2" in md

    single = _design_with_zones("us-east-1a")
    md_single = render_assumptions(apply(fake_usage(), single), single,
                                   source_name="srs.md", run_date="2026-10-09", model_id="m")
    assert "one Availability Zone (us-east-1a)" in md_single
