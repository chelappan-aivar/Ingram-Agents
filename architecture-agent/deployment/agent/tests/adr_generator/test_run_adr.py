"""adr_generator.cli end to end with FakeLLM (no AWS)."""
from __future__ import annotations

import json
from pathlib import Path

from adr_generator.llm.bedrock import FakeLLM, LLMError
from tests.adr_generator.conftest import FIXTURES

from adr_generator import cli as run_adr

SRS = "# SRS\nThe system shall let customers dispute a bill.\n"


def _srs(tmp_path) -> Path:
    p = tmp_path / "srs.md"
    p.write_text(SRS, encoding="utf-8")
    return p


def _run_dir(out_dir: Path) -> Path:
    runs = list((out_dir / "runs").iterdir())
    assert len(runs) == 1
    return runs[0]


def test_success_writes_all_artifacts(tmp_path, settings):
    design = json.loads((FIXTURES / "design_full.json").read_text(encoding="utf-8"))
    lines: list[str] = []
    out_dir = tmp_path / "out"
    code = run_adr.main([str(_srs(tmp_path)), "--out", str(out_dir)], settings=settings,
                        llm=FakeLLM(tool_responses=[design]), out=lines.append)
    assert code == 0
    run = _run_dir(out_dir)
    assert (run / "input" / "srs.md").read_text(encoding="utf-8") == SRS
    for name in ("design.json", "spec.json", "metadata.json"):
        json.loads((run / name).read_text(encoding="utf-8"))
    assert json.loads((run / "design.json").read_text(encoding="utf-8")) == design
    assert (run / "adr.md").read_text(encoding="utf-8").startswith("# Architecture Decision Record")
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed" and meta["model_id"] == "fake-model" and meta["run_id"] == run.name
    assert meta["counts"]["components"] == len(design["components"])
    assert set(meta["counts"]) >= {"requirements", "decisions", "components", "relationships",
                                   "assumptions", "assumed_components"}
    text = "\n".join(lines)
    for stage in ("INGEST", "DESIGN", "RENDER", "DONE"):
        assert stage in text
    assert str(run) in text


def test_llm_error_fails_with_metadata(tmp_path, settings):
    lines: list[str] = []
    out_dir = tmp_path / "out"
    code = run_adr.main([str(_srs(tmp_path)), "--out", str(out_dir)], settings=settings,
                        llm=FakeLLM(tool_responses=[LLMError("boom", "truncated")]), out=lines.append)
    assert code == 1
    run = _run_dir(out_dir)
    meta = json.loads((run / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed" and "boom" in meta["error"]
    assert not (run / "adr.md").exists()
    assert any("FAILED" in l and "boom" in l for l in lines)


def test_input_error_fails(tmp_path, settings):
    bad = tmp_path / "srs.csv"
    bad.write_text("a,b", encoding="utf-8")
    lines: list[str] = []
    out_dir = tmp_path / "out"
    code = run_adr.main([str(bad), "--out", str(out_dir)], settings=settings, llm=FakeLLM(), out=lines.append)
    assert code == 1
    meta = json.loads((_run_dir(out_dir) / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed" and "unsupported" in meta["error"]


def test_missing_srs(tmp_path, settings):
    lines: list[str] = []
    code = run_adr.main([str(tmp_path / "nope.md"), "--out", str(tmp_path / "out")], settings=settings,
                        llm=FakeLLM(), out=lines.append)
    assert code != 0 and "not found" in lines[0]
    assert not (tmp_path / "out").exists()
