"""End-to-end: the real 9 stages (diagram_generator.stages.default_stages) with a FakeLLM and the real draw.io toolchain.

The fake Extractor/Assumer answers rebuild tests/fixtures/coder_sample_spec.json, and the fake Diagram coder
answers with tests/fixtures/coder_sample_layout.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagram_generator.harness.log import read_records
from diagram_generator.llm.bedrock import FakeLLM, LLMError
from diagram_generator.pipeline import Pipeline
from diagram_generator.render.drawio_tools import find_drawio_cli
from diagram_generator.stages import NO_ARCH_MESSAGE, default_stages

FIX = Path(__file__).resolve().parent / "fixtures"
DOC = "fleet_adr.md"
DOC_TEXT = """# Fleet Telemetry Ingestion
Vehicles in the fleet publish telemetry to AWS IoT Core over MQTT.
IoT Core forwards every reading into a Kinesis Data Streams stream.
A Lambda function consumes the stream inside a private subnet of the VPC in us-east-1.
The processor stores the latest vehicle state in DynamoDB.
Raw readings are archived to an S3 bucket for analytics.
Fleet operators need dashboards showing vehicle status and downloadable reports.
"""

pytestmark = pytest.mark.skipif(find_drawio_cli(None) is None, reason="draw.io CLI not installed")


@pytest.fixture
def settings(tmp_path):
    """These tests exercise the LLM Diagram coder and drawn assumptions (LAYOUT_ENGINE=llm, ASSUMPTIONS_MODE=draw)."""
    from tests.diagram_generator.conftest import make_settings
    return make_settings(tmp_path, LAYOUT_ENGINE="llm", ASSUMPTIONS_MODE="draw")


def ev(quote: str, why: str = "The document states it.") -> list[dict]:
    assert quote in DOC_TEXT, quote
    return [{"file": DOC, "quote": quote, "reasoning": why}]


def extraction_raw() -> dict:
    spec = json.loads((FIX / "coder_sample_spec.json").read_text())
    quotes = {
        "fleet_ops": "Fleet operators need dashboards",
        "vehicle_fleet": "Vehicles in the fleet publish telemetry",
        "iot_core": "publish telemetry to AWS IoT Core",
        "kinesis_stream": "into a Kinesis Data Streams stream",
        "stream_processor": "A Lambda function consumes the stream",
        "telemetry_table": "stores the latest vehicle state in DynamoDB",
        "archive_bucket": "archived to an S3 bucket",
        "aws_cloud": "AWS IoT Core",
        "region_use1": "us-east-1",
        "vpc_main": "private subnet of the VPC",
        "subnet_private": "inside a private subnet",
        "r1": "Vehicles in the fleet publish telemetry to AWS IoT Core",
        "r2": "IoT Core forwards every reading into a Kinesis Data Streams stream",
        "r3": "A Lambda function consumes the stream",
        "r4": "The processor stores the latest vehicle state in DynamoDB",
        "r5": "Raw readings are archived to an S3 bucket",
    }
    comps = [{k: c[k] for k in ("id", "name", "service", "kind", "boundary")} |
             {"strength": "stated", "evidence": ev(quotes[c["id"]])}
             for c in spec["components"] if c["origin"] != "assumed"]
    bounds = [{k: b[k] for k in ("id", "type", "name", "parent")} | {"strength": "stated", "evidence": ev(quotes[b["id"]])}
              for b in spec["boundaries"]]
    rels = [{k: r[k] for k in ("id", "source", "target", "label", "direction")} |
            {"strength": "stated", "evidence": ev(quotes[r["id"]])}
            for r in spec["relationships"] if r["origin"] != "assumed"]
    return {"title": spec["title"], "components": comps, "relationships": rels, "boundaries": bounds,
            "contradictions": [], "resolutions": []}


def assumer_raw() -> dict:
    why = ev("Fleet operators need dashboards showing vehicle status and downloadable reports",
             "Operators need an access path to the stored state and reports.")
    return {
        "components": [{"id": "ops_api", "name": "Operations API", "service": "Amazon API Gateway", "kind": "aws_service",
                        "boundary": "region_use1", "supports_ids": ["fleet_ops", "telemetry_table"],
                        "reasoning": "Operators cannot reach the data without an API.", "evidence": why}],
        "relationships": [
            {"id": "r6", "source": "fleet_ops", "target": "ops_api", "label": "view dashboards",
             "supports_ids": ["fleet_ops"], "reasoning": "Operators use the API.", "evidence": why},
            {"id": "r7", "source": "ops_api", "target": "telemetry_table", "label": "read status",
             "supports_ids": ["telemetry_table"], "reasoning": "Dashboards read vehicle state.", "evidence": why},
            {"id": "r8", "source": "ops_api", "target": "archive_bucket", "label": "download reports",
             "supports_ids": ["archive_bucket"], "reasoning": "Reports come from the archive.", "evidence": why},
        ],
        "boundaries": [],
        "unresolved": [{"about": "Operator authentication", "reasoning": "No identity provider is mentioned.",
                        "suggestion": "Confirm how operators sign in.", "related_ids": ["fleet_ops"]}],
    }


def good_layout() -> dict:
    return json.loads((FIX / "coder_sample_layout.json").read_text())


def run_job(store, bus, settings, make_job, llm: FakeLLM):
    job = make_job(store, {DOC: DOC_TEXT.encode()})
    Pipeline(settings, store, bus, lambda step: llm, default_stages()).run(job.job_id)
    return store.get(job.job_id), store.job_dir(job.job_id)


def test_full_pipeline_produces_all_outputs(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[extraction_raw(), assumer_raw(), good_layout()])
    job, d = run_job(store, bus, settings, make_job, llm)
    assert job.status == "COMPLETED", job.error
    for name in ("diagram.png", "diagram.svg", "diagram.drawio", "layout.json", "assumptions.json", "unresolved.json",
                 "contradictions.json", "spec.json", "extraction.json", "report.json", "metadata.json",
                 "harness.log.jsonl", "sources.json"):
        assert (d / name).is_file() and (d / name).stat().st_size > 0, name
    assert "color-scheme: light" in (d / "diagram.svg").read_text()[:5000]

    recs = read_records(d)
    assert [r.step for r in recs] == ["harness_1", "harness_2", "harness_3", "export"]
    h1, h2, h3, ex = recs
    assert h1.out_counts["components"] == 7 and h1.out_counts["relationships"] == 5
    assert h2.out_counts["components"] == 8 and h2.out_counts["relationships"] == 8
    assert h3.ok and h3.out_counts["components_found"] == 8 and h3.out_counts["relationships_found"] == 8
    assert h3.out_counts["layout_errors"] == 0 and ex.ok
    from diagram_generator.render.edge_router import find_node
    if find_node(settings):
        assert h3.out_counts["edges_routed"] == 8 and h3.out_counts["edges_unrouted"] == 0

    report = json.loads((d / "report.json").read_text())
    assert report["status"] == "pass_with_warnings"  # one unresolved item
    assert report["summary"]["components_by_origin"] == {"stated": 7, "inferred": 0, "assumed": 1}
    added = [w for w in report["warnings"] if "assumed component(s) that are not in the ADR" in w]
    assert len(added) == 1 and "spec.json and usage.json do not include them" in added[0]
    meta = json.loads((d / "metadata.json").read_text())
    assert meta["status"] == "COMPLETED"


def test_bad_layout_is_sent_back_and_fixed(settings, store, bus, make_job):
    bad = good_layout()
    bad["nodes"][3].update(x=380, y=340)  # kinesis_stream on top of iot_core
    llm = FakeLLM(tool_responses=[extraction_raw(), assumer_raw(), bad, good_layout()])
    job, d = run_job(store, bus, settings, make_job, llm)
    assert job.status == "COMPLETED", job.error
    h3 = [r for r in read_records(d) if r.step == "harness_3"]
    assert [r.ok for r in h3] == [False, True]
    assert any("overlap" in e for e in h3[0].errors)
    retry_text = llm.tool_calls[3]["messages"][0]["content"][0]["text"]
    assert "It failed these checks" in retry_text and "kinesis_stream" in retry_text


def test_layout_still_bad_after_retries_is_exported_with_warnings(settings, store, bus, make_job):
    bad = good_layout()
    bad["nodes"][3].update(x=380, y=340)
    llm = FakeLLM(tool_responses=[extraction_raw(), assumer_raw(), bad, bad, bad])
    job, d = run_job(store, bus, settings, make_job, llm)
    assert job.status == "COMPLETED", job.error
    assert (d / "diagram.png").exists()
    final = [r for r in read_records(d) if r.step == "harness_3"][-1]
    assert final.ok and any("exported with layout problem" in w for w in final.warnings)


def test_truncated_coder_answer_triggers_compact_retry(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[extraction_raw(), assumer_raw(),
                                  LLMError("cut off", "truncated"), good_layout()])
    job, d = run_job(store, bus, settings, make_job, llm)
    assert job.status == "COMPLETED", job.error
    h3 = [r for r in read_records(d) if r.step == "harness_3"]
    assert "cut off before the layout was complete" in h3[0].errors[0] and h3[-1].ok


def test_no_components_fails_cleanly(settings, store, bus, make_job):
    empty = {"title": "Nothing", "components": [], "relationships": [], "boundaries": [],
             "contradictions": [], "resolutions": []}
    job, d = run_job(store, bus, settings, make_job, FakeLLM(tool_responses=[empty]))
    assert job.status == "FAILED" and NO_ARCH_MESSAGE in job.error["message"]
    assert not (d / "diagram.png").exists()
