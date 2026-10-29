"""Pipeline framework with stub stages."""
from __future__ import annotations

import json

import pytest

from diagram_generator.harness.log import read_records
from diagram_generator.jobs import ARTIFACT_TYPES, STAGES, EventBus
from diagram_generator.llm.bedrock import FakeLLM, LLMError
from diagram_generator.pipeline import Pipeline, StageFailure
from tests.diagram_generator.conftest import make_stub_stages

MD = b"# Orders\nA web app on ECS behind an ALB.\n"


def run(settings, store, bus, make_job, llm, stages, files=None):
    job = make_job(store, files or {"a.md": MD, "b.md": MD + b"More text\n"})
    Pipeline(settings, store, bus, lambda step: llm, stages).run(job.job_id)
    return store.get(job.job_id), store.job_dir(job.job_id)


def meta_of(d):
    return json.loads((d / "metadata.json").read_text(encoding="utf-8"))


def test_success(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[{"components": [1, 2, 3]}])
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages())
    assert job.status == "COMPLETED" and job.error is None and job.report_status == "pass"
    assert {"ingest", "extractor", "harness_1", "package"} <= set(job.timings)
    events = bus.history(job.job_id)
    stages = [e["stage"] for e in events]
    assert stages[-1] == "COMPLETED" and events[-1]["progress"] == 100
    prog = [e["progress"] for e in events]
    assert prog == sorted(prog)
    assert set(events[0]) == {"job_id", "stage", "kind", "message", "counts", "progress", "ts"}
    ing = next(e for e in events if e["stage"] == "INGEST" and e["kind"] == "completed")
    assert ing["counts"] == {"files": 2}
    assert (d / "logs.txt").stat().st_size > 0
    # job.json persisted
    assert json.loads((d / "job.json").read_text())["status"] == "COMPLETED"


def test_harness_logs_line_and_emits_event(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[{"components": [1, 2]}])
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages())
    recs = read_records(d)
    assert len(recs) == 1 and recs[0].step == "harness_1" and recs[0].out_counts == {"components": 2}
    ev = [e for e in bus.history(job.job_id) if e["stage"] == "HARNESS_1" and e["message"].startswith("harness_1")]
    assert len(ev) == 1 and ev[0]["counts"] == {"components": 2} and "components=2" in ev[0]["message"]
    assert ev[0]["kind"] == "info"
    assert "harness.log.jsonl" in store.artifacts(job.job_id)


def test_metadata_written_with_llm_info(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[{"components": []}], model_id="m-primary")
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages())
    m = meta_of(d)
    assert m["job_id"] == job.job_id and m["status"] == "COMPLETED" and m["filenames"] == ["a.md", "b.md"]
    assert m["app_version"] and m["models_used"] == {"extractor": "m-primary"}
    assert m["fallbacks"] == [] and m["tokens"] == {"input": 0, "output": 0}
    assert m["llm_calls"][0]["step"] == "extractor" and "ingest" in m["timings"]
    assert m["configured_models"]["fallback"] == settings.fallback_model_id
    # passed max_tokens for the step
    assert llm.tool_calls[0]["max_tokens"] == settings.extractor_max_tokens


def test_metadata_records_fallback_and_tokens(settings, store, bus, make_job):
    class FB(FakeLLM):
        def converse_tool(self, *a, **k):
            out = super().converse_tool(*a, **k)
            self.last_call = {**self.last_call, "model_id": "sonnet", "fallback_used": True,
                              "primary_error": "opus down", "input_tokens": 100, "output_tokens": 50}
            return out
    job, d = run(settings, store, bus, make_job, FB(tool_responses=[{}]), make_stub_stages())
    m = meta_of(d)
    assert m["fallbacks"] == [{"step": "extractor", "model_id": "sonnet", "primary_error": "opus down"}]
    assert m["tokens"] == {"input": 100, "output": 50}


def test_stage_failure(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[{"components": []}])
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages(fail_at="HARNESS_1"))
    assert job.status == "FAILED" and job.error == {"stage": "HARNESS_1", "message": "stub failure in HARNESS_1"}
    last = bus.history(job.job_id)[-1]
    assert last["stage"] == "FAILED" and last["kind"] == "failed" and last["message"] == "stub failure in HARNESS_1"
    m = meta_of(d)
    assert m["status"] == "FAILED" and m["error"]["stage"] == "HARNESS_1"
    assert not (d / "report.json").exists()


def test_llm_error_fails_job_at_current_stage(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[LLMError("output cut", "truncated")])
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages())
    assert job.status == "FAILED" and job.error["stage"] == "EXTRACTOR"
    assert "truncated" in job.error["message"] and "output cut" in job.error["message"]
    assert meta_of(d)["status"] == "FAILED"


def test_unexpected_exception(settings, store, bus, make_job):
    llm = FakeLLM(tool_responses=[{}])
    job, d = run(settings, store, bus, make_job, llm, make_stub_stages(fail_at="PACKAGE", failure=ValueError("oops")))
    assert job.status == "FAILED" and job.error["stage"] == "PACKAGE"
    assert job.error["message"] == "Internal error: ValueError: oops"
    assert (d / "metadata.json").exists()


def test_scanned_pdf_ingest_failure_via_stub_ingest(settings, store, bus, make_job, fixtures_dir):
    from diagram_generator.ingest.extractors import ExtractionError

    def ingest(ctx):
        from diagram_generator.ingest.extractors import extract_sources
        try:
            extract_sources(ctx.input_paths, 1000)
        except ExtractionError as e:
            raise StageFailure("INGEST", str(e))
    job, d = run(settings, store, bus, make_job, FakeLLM(), [("INGEST", ingest)],
                 files={"e_scanned.pdf": (fixtures_dir / "e_scanned.pdf").read_bytes()})
    assert job.status == "FAILED" and job.error["stage"] == "INGEST" and "Scanned" in job.error["message"]


def test_missing_input_files(settings, store, bus):
    job = store.create(["x.md"])
    Pipeline(settings, store, bus, lambda s: FakeLLM(), make_stub_stages()).run(job.job_id)
    assert store.get(job.job_id).status == "FAILED"
    assert store.get(job.job_id).error["stage"] == "INGEST"


def test_stage_without_completed_event_gets_one(settings, store, bus, make_job):
    job, _ = run(settings, store, bus, make_job, FakeLLM(), [("INGEST", lambda ctx: None)])
    assert job.status == "COMPLETED"
    assert any(e["stage"] == "INGEST" and e["kind"] == "completed" for e in bus.history(job.job_id))


def test_default_stages_lazy_import(settings, store, bus, make_job, monkeypatch):
    import sys
    import types

    mod = types.ModuleType("diagram_generator.stages")
    mod.default_stages = lambda: [("INGEST", lambda ctx: ctx.emit("INGEST", "completed", "stub"))]
    monkeypatch.setitem(sys.modules, "diagram_generator.stages", mod)
    job, _ = run(settings, store, bus, make_job, FakeLLM(), None)
    assert job.status == "COMPLETED"


def test_bus_listener_and_finish(settings, store, make_job):
    bus, seen = EventBus(), []
    bus.add_listener(seen.append)
    job, _ = run(settings, store, bus, make_job, FakeLLM(tool_responses=[{}]), make_stub_stages())
    assert seen == bus.history(job.job_id) and job.job_id in bus._finished


def test_constants():
    assert STAGES[0] == "QUEUED" and STAGES[-2:] == ["COMPLETED", "FAILED"] and len(STAGES) == 12
    assert ARTIFACT_TYPES["diagram.drawio"] == "application/xml"
    assert ARTIFACT_TYPES["harness.log.jsonl"] == "application/x-ndjson"
    assert len(ARTIFACT_TYPES) == 14 and "layout.json" in ARTIFACT_TYPES
