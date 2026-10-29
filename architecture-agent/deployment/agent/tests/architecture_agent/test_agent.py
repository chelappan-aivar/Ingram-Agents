"""AgentCore entrypoint contract (no AWS): routing, request validation, every action's response.
Pipeline mains are replaced by stubs that write the same files the real ones do; model calls use FakeLLM."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from adr_generator.llm.bedrock import FakeLLM, LLMError
from architecture_agent import actions, agent
from tests.usage_estimator.test_usage import fake_usage

DESIGN = json.loads((Path(__file__).parents[1] / "adr_generator" / "fixtures" / "design_full.json").read_text())


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode())


def _adr_main(ok=True):
    def main(argv, out=print, **kw):
        if ok:
            run = Path(argv[argv.index("--out") + 1]) / "runs" / "run1"
            _write(run / "adr.md", "# ADR"); _write(run / "spec.json", '{"a": 1}'); _write(run / "metadata.json", '{"m": 1}')
        out("adr status line")
        return 0 if ok else 1
    return main


def _diagram_main(status="COMPLETED"):
    def main(argv, out=print, on_event=None, **kw):
        job = Path(argv[argv.index("--out") + 1]) / "jobs" / "job1"
        _write(job / "job.json", json.dumps({"status": status, "report_status": "pass",
                                             "error": {"message": "boom"} if status != "COMPLETED" else None}))
        _write(job / "diagram.png", b"PNG"); _write(job / "diagram.svg", b"<svg/>"); _write(job / "diagram.drawio", "<mxfile/>")
        _write(job / "report.json", '{"status": "pass"}'); _write(job / "metadata.json", '{"d": 1}')
        if on_event:
            on_event({"stage": "EXTRACTOR", "kind": "started", "message": "m", "counts": {}, "progress": 8})
        out("diagram status line")
        return 0 if status == "COMPLETED" else 1
    return main


@pytest.fixture(autouse=True)
def no_live_model_calls(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test reached the real model layer")
    monkeypatch.setattr(actions, "make_llm", refuse)
    monkeypatch.setattr(actions.usage_cli, "make_llm", refuse)


def invoke(payload):
    return agent.invoke(payload)


@pytest.mark.parametrize("payload,msg", [
    ({}, "Send srs_text or srs_base64"),
    ({"srs_text": "  "}, "Send srs_text or srs_base64"),
    ({"srs_text": "x", "srs_filename": "a.exe"}, "Unsupported SRS file type"),
    ({"srs_base64": "!!!", "srs_filename": "a.pdf"}, "not valid base64"),
])
def test_bad_pipeline_requests_fail_fast(payload, msg):
    r = invoke(payload)
    assert r["status"] == "failed" and r["stage"] == "request" and msg in r["error"]


def test_unknown_action():
    r = invoke({"action": "nope"})
    assert r["status"] == "failed" and "Unknown action" in r["error"] and "revise_adr" in r["error"]


# ----------------------------------------------------------------------------- run_pipeline (default)
def test_success_response_shape(monkeypatch):
    monkeypatch.setattr(actions.adr_cli, "main", _adr_main())
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main())
    r = invoke({"srs_text": "# SRS", "srs_filename": "srs.md"})
    assert r["status"] == "completed" and r["report_status"] == "pass"
    # `design` is carried through so a caller can revise or re-diagram without re-reading the ADR;
    # it is None here because the fake ADR run writes no design.json.
    assert r["adr"] == {"adr_md": "# ADR", "spec_json": {"a": 1}, "design": None}
    assert base64.b64decode(r["diagram"]["png_base64"]) == b"PNG" and r["diagram"]["drawio_xml"] == "<mxfile/>"
    assert r["run_id"] == "run1" and r["job_id"] == "job1" and r["adr_metadata"] == {"m": 1}
    # no scope supplied -> nothing to report
    assert r["scope"] is None and r["scope_warnings"] == []


def test_pipeline_runs_usage_between_adr_and_diagram(monkeypatch):
    def adr_main(argv, out=print, **kw):
        run = Path(argv[argv.index("--out") + 1]) / "runs" / "run1"
        _write(run / "adr.md", "# ADR"); _write(run / "spec.json", '{"a": 1}'); _write(run / "metadata.json", '{"m": 1}')
        _write(run / "design.json", json.dumps(DESIGN)); _write(run / "input" / "srs.md", "# SRS")
        return 0
    llm = FakeLLM(tool_responses=[fake_usage()])
    monkeypatch.setattr(actions.usage_cli, "make_llm", lambda settings: llm)
    monkeypatch.setattr(actions.adr_cli, "main", adr_main)
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main())
    r = invoke({"srs_text": "# SRS", "srs_filename": "srs.md"})
    assert r["status"] == "completed" and r["usage"]["status"] == "completed"
    assert list(r["usage"]["usage_json"]["services"])[0] == "networking_and_cdn"
    assert r["usage"]["usage_assumptions_md"].startswith("# Usage estimate")
    assert r["usage"]["usage_metadata"]["status"] == "completed"


def test_pipeline_usage_failure_still_draws_the_diagram(monkeypatch):
    monkeypatch.setattr(actions.adr_cli, "main", _adr_main())          # writes no design.json
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main())
    r = invoke({"srs_text": "# SRS"})
    assert r["status"] == "completed" and r["job_id"] == "job1"
    assert r["usage"]["status"] == "failed" and r["usage"]["stage"] == "usage"


def test_adr_failure_stops_before_diagram(monkeypatch):
    monkeypatch.setattr(actions.adr_cli, "main", _adr_main(ok=False))
    monkeypatch.setattr(actions.diagram_cli, "main", lambda *a, **k: pytest.fail("diagram must not run"))
    r = invoke({"srs_text": "# SRS"})
    assert r["status"] == "failed" and r["stage"] == "adr" and "adr status line" in r["log_tail"]


def test_diagram_failure_keeps_the_adr(monkeypatch):
    monkeypatch.setattr(actions.adr_cli, "main", _adr_main())
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main(status="FAILED"))
    r = invoke({"srs_text": "# SRS"})
    assert r["status"] == "failed" and r["stage"] == "diagram" and r["error"] == "boom" and r["adr"]["adr_md"] == "# ADR"


# ----------------------------------------------------------------------------- extract_srs
def test_extract_srs_reads_text_and_rejects_empty():
    r = invoke({"action": "extract_srs", "srs_text": "# Title\nThe system shall do X.\n", "srs_filename": "a.md"})
    assert r["status"] == "completed" and "shall do X" in r["srs_text"] and r["filename"] == "a.md" and r["truncated"] is False
    assert invoke({"action": "extract_srs", "srs_text": " "})["stage"] == "request"


# ----------------------------------------------------------------------------- generate_adr / revise_adr
def _fake(monkeypatch, *responses):
    llm = FakeLLM(tool_responses=list(responses), model_id="fake-model")
    monkeypatch.setattr(actions, "make_llm", lambda settings: llm)
    return llm


def test_generate_adr(monkeypatch):
    llm = _fake(monkeypatch, DESIGN)
    r = invoke({"action": "generate_adr", "srs_text": "# SRS\nStuff.", "srs_filename": "srs.md"})
    assert r["status"] == "completed" and r["run_id"] and r["adr_md"].startswith("# ")
    assert r["design"]["title"] == DESIGN["title"] and r["spec_json"] and r["counts"]["components"] == len(DESIGN["components"])
    assert r["model_id"] == "fake-model" and set(r["tokens"]) == {"input", "output"}
    assert llm.tool_calls[0]["tool_spec"]["toolSpec"]["name"] == "submit_design"
    assert "Stuff." in llm.tool_calls[0]["messages"][0]["content"][0]["text"]


def test_adr_actions_print_live_status_lines(monkeypatch, capsys):
    _fake(monkeypatch, DESIGN, {**DESIGN, "title": "T2"}, LLMError("boom", "truncated"))
    invoke({"action": "generate_adr", "srs_text": "# SRS\nStuff.", "srs_filename": "srs.md"})
    out = capsys.readouterr().out
    assert "INGEST" in out and "Calling fake-model" in out and "Design received" in out and "stop:" in out
    invoke({"action": "revise_adr", "design": DESIGN, "srs_text": "s", "instruction": "i"})
    assert "REVISE" in capsys.readouterr().out
    invoke({"action": "revise_adr", "design": DESIGN, "srs_text": "s", "instruction": "i"})
    assert "FAILED" in capsys.readouterr().out


def test_generate_adr_model_failure_and_bad_request(monkeypatch):
    _fake(monkeypatch, LLMError("boom", "truncated"))
    r = invoke({"action": "generate_adr", "srs_text": "x"})
    assert r["status"] == "failed" and r["stage"] == "adr" and r["error"] == "boom [truncated]"
    assert invoke({"action": "generate_adr"})["stage"] == "request"


def test_revise_adr_uses_the_design_tool_and_keeps_run_id(monkeypatch):
    llm = _fake(monkeypatch, {**DESIGN, "title": "Revised title"})
    r = invoke({"action": "revise_adr", "run_id": "run-42", "design": DESIGN, "srs_text": "# SRS", "instruction": "Rename it"})
    assert r["status"] == "completed" and r["run_id"] == "run-42" and r["design"]["title"] == "Revised title"
    sent = llm.tool_calls[0]
    assert sent["tool_spec"]["toolSpec"]["name"] == "submit_design"
    assert "Rename it" in sent["messages"][0]["content"][0]["text"]


@pytest.mark.parametrize("payload", [
    {"design": DESIGN, "srs_text": "s"},                      # no instruction
    {"design": DESIGN, "instruction": "i"},                   # no srs_text
    {"srs_text": "s", "instruction": "i"},                    # no design
    {"design": ["not", "an", "object"], "srs_text": "s", "instruction": "i"},        # design is not an object
])
def test_revise_adr_bad_requests(payload):
    r = invoke({"action": "revise_adr"} | payload)
    assert r["status"] == "failed" and r["stage"] == "request"


# ----------------------------------------------------------------------------- estimate_usage
def test_estimate_usage(monkeypatch, capsys):
    llm = _fake(monkeypatch, fake_usage())
    r = invoke({"action": "estimate_usage", "run_id": "run-7", "design": DESIGN, "srs_text": "# SRS\nVolumes.",
                "srs_filename": "srs.md"})
    assert r["status"] == "completed" and r["run_id"] == "run-7"
    assert list(r["usage_json"]) == ["services"] and r["usage_json"]["services"]["database"][0]["multi_az"] is False
    assert r["usage_assumptions_md"].startswith("# Usage estimate") and r["counts"]["entries"] == 14
    assert r["model_id"] == "fake-model" and set(r["tokens"]) == {"input", "output"}
    assert llm.tool_calls[0]["tool_spec"]["toolSpec"]["name"] == "submit_usage"
    assert "Volumes." in llm.tool_calls[0]["messages"][0]["content"][0]["text"]
    assert "USAGE" in capsys.readouterr().out


def test_estimate_usage_failures(monkeypatch):
    _fake(monkeypatch, LLMError("boom", "truncated"))
    r = invoke({"action": "estimate_usage", "design": DESIGN, "srs_text": "s"})
    assert r["status"] == "failed" and r["stage"] == "usage" and r["error"] == "boom [truncated]"
    for payload in ({"design": DESIGN}, {"srs_text": "s"}, {"design": [1], "srs_text": "s"}):
        assert invoke({"action": "estimate_usage"} | payload)["stage"] == "request"


# ----------------------------------------------------------------------------- generate_diagram
def test_generate_diagram(monkeypatch):
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main())
    r = invoke({"action": "generate_diagram", "adr_md": "# ADR"})
    assert r["status"] == "completed" and r["report_status"] == "pass" and r["job_id"] == "job1"
    assert base64.b64decode(r["diagram"]["svg_base64"]) == b"<svg/>"
    assert invoke({"action": "generate_diagram"})["stage"] == "request"


def test_generate_diagram_failure(monkeypatch):
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main(status="FAILED"))
    r = invoke({"action": "generate_diagram", "adr_md": "# ADR"})
    assert r["status"] == "failed" and r["stage"] == "diagram" and r["error"] == "boom"


def test_generate_diagram_stream_sends_progress_then_result(monkeypatch):
    monkeypatch.setattr(actions.diagram_cli, "main", _diagram_main())
    events = list(invoke({"action": "generate_diagram", "adr_md": "# ADR", "stream": True}))
    assert [e["event"] for e in events] == ["progress", "result"]
    assert events[0]["stage"] == "EXTRACTOR" and events[0]["progress"] == 8
    assert events[1]["status"] == "completed" and events[1]["diagram"]["drawio_xml"] == "<mxfile/>"


def test_stream_reports_a_crash_instead_of_hanging(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(actions.diagram_cli, "main", boom)
    events = list(invoke({"action": "generate_diagram", "adr_md": "# ADR", "stream": True}))
    assert events[-1]["event"] == "result" and events[-1]["status"] == "failed" and "kaboom" in events[-1]["error"]


# ----------------------------------------------------------------------------- concurrency
def test_concurrent_requests_do_not_share_state(monkeypatch):
    """Two revise requests and a diagram request running at the same moment each get their own result: the
    pipelines keep no mutable state between requests."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(3)

    def fake_make_llm(settings):
        def answer(system, messages):
            barrier.wait(timeout=10)  # all three requests are inside their model call at the same moment
            wanted = messages[0]["content"][0]["text"].split("TITLE=")[-1].split()[0]
            return {**DESIGN, "title": wanted}
        return FakeLLM(tool_responses=[answer])

    def diagram_main(argv, out=print, on_event=None, **kw):
        barrier.wait(timeout=10)
        return _diagram_main()(argv, out, on_event)

    monkeypatch.setattr(actions, "make_llm", fake_make_llm)
    monkeypatch.setattr(actions.diagram_cli, "main", diagram_main)

    def revise(title):
        return invoke({"action": "revise_adr", "run_id": title, "design": DESIGN, "srs_text": "s",
                       "instruction": f"TITLE={title}"})

    with ThreadPoolExecutor(3) as pool:
        a, b = pool.submit(revise, "Alpha"), pool.submit(revise, "Beta")
        d = pool.submit(invoke, {"action": "generate_diagram", "adr_md": "# ADR"})
        results = a.result(30), b.result(30), d.result(30)
    assert results[0]["design"]["title"] == "Alpha" and results[0]["run_id"] == "Alpha"
    assert results[1]["design"]["title"] == "Beta" and results[1]["run_id"] == "Beta"
    assert results[2]["status"] == "completed" and results[2]["job_id"] == "job1"
