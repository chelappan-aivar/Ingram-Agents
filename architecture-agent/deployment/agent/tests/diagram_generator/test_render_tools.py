"""Render toolchain tests: vendored validator/fixers, light-mode SVG, real draw.io CLI export."""
from __future__ import annotations

import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from diagram_generator.config import ROOT_DIR, Settings
from diagram_generator.harness.h3_drawio import parse_drawio
from diagram_generator.render.drawio_tools import (export, find_drawio_cli, force_light_svg, post_process, validate)

EXAMPLES = sorted((ROOT_DIR / "vendor" / "aws-architecture-diagram" / "references").glob("example-*.drawio"))
SETTINGS = Settings()


def test_examples_present():
    assert len(EXAMPLES) >= 5


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_parse_and_validate_examples(example, tmp_path):
    facts = parse_drawio(example.read_text(encoding="utf-8"))
    assert facts.parse_error is None and facts.vertices and facts.edges
    out = validate(example, SETTINGS)
    assert out.ok, out.errors
    assert not out.errors
    assert "VALIDATION PASSED" in out.raw
    assert not any(w.startswith("AWS4 shapes used") for w in out.warnings)   # summary line excluded
    assert "AWS4 shapes used" in out.raw                                     # but kept in raw


def test_validate_reports_errors(tmp_path):
    bad = tmp_path / "bad.drawio"
    bad.write_text("<mxfile><diagram><mxGraphModel><root><mxCell id=\"1\" parent=\"0\"/></root>"
                   "</mxGraphModel></diagram></mxfile>", encoding="utf-8")
    out = validate(bad, SETTINGS)
    assert not out.ok
    assert any("root cell" in e for e in out.errors)


def test_validate_missing_file_does_not_raise(tmp_path):
    out = validate(tmp_path / "nope.drawio", SETTINGS)
    assert not out.ok and out.errors


def test_post_process_example_copy(tmp_path):
    copy = tmp_path / "diagram.drawio"
    shutil.copyfile(EXAMPLES[0], copy)
    res = post_process(copy, SETTINGS)
    assert res.ok, res.output
    assert "Post-processing" in res.output
    assert validate(copy, SETTINGS).ok


def test_post_process_bad_input_returns_not_ok(tmp_path):
    bad = tmp_path / "bad.drawio"
    bad.write_text("<mxfile><oops", encoding="utf-8")
    assert post_process(bad, SETTINGS).ok is False
    assert post_process(tmp_path / "missing.drawio", SETTINGS).ok is False


# ----------------------------------------------------------------------------- force_light_svg
def test_force_light_svg_appends_to_existing_style(tmp_path):
    p = tmp_path / "a.svg"
    body = '<defs/><g><rect width="1" height="1"/></g></svg>'
    p.write_text('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
                 'style="background: transparent; background-color: transparent; color-scheme: light dark; '
                 'fill: red;" width="10px">' + body, encoding="utf-8")
    force_light_svg(p)
    text = p.read_text(encoding="utf-8")
    assert "color-scheme: light;" in text and "light dark" not in text
    assert "background-color: #ffffff" in text and "transparent" not in text
    assert "fill: red" in text                       # unrelated declarations kept
    assert text.endswith(body)                       # rest of the file byte-identical
    assert text.startswith('<?xml version="1.0"?>\n')


def test_force_light_svg_adds_style_when_missing(tmp_path):
    p = tmp_path / "b.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="5"><g/></svg>', encoding="utf-8")
    force_light_svg(p)
    text = p.read_text(encoding="utf-8")
    assert re.search(r'<svg [^>]*style="color-scheme: light; background-color: #ffffff;"', text)
    assert text.endswith("<g/></svg>")


def test_force_light_svg_idempotent(tmp_path):
    p = tmp_path / "c.svg"
    p.write_text('<svg width="5"><g/></svg>', encoding="utf-8")
    force_light_svg(p)
    once = p.read_text(encoding="utf-8")
    force_light_svg(p)
    assert p.read_text(encoding="utf-8") == once


# ----------------------------------------------------------------------------- find_drawio_cli
def test_find_drawio_cli_prefers_setting(tmp_path):
    fake = tmp_path / "fakecli"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert find_drawio_cli(Settings(DRAWIO_CLI=str(fake))) == str(fake)


# ----------------------------------------------------------------------------- real export
CLI = find_drawio_cli(SETTINGS)
needs_cli = pytest.mark.skipif(CLI is None, reason="draw.io CLI not installed")


def _check_export(res, out_dir):
    assert not res.errors, res.errors
    assert res.png == out_dir / "diagram.png" and res.png.stat().st_size > 1000
    assert res.png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert res.svg == out_dir / "diagram.svg" and res.svg.stat().st_size > 1000
    svg = res.svg.read_text(encoding="utf-8")
    assert "color-scheme: light;" in svg and "light dark" not in svg
    assert "file://" not in svg
    hrefs = re.findall(r'(?:xlink:)?href="([^"]*)"', svg)
    assert not [h for h in hrefs if h.startswith(("/", "file:")) or re.match(r"^[A-Za-z]:\\", h)], hrefs


@needs_cli
def test_export_real_cli(tmp_path):
    src = tmp_path / "diagram.drawio"
    shutil.copyfile(next(p for p in EXAMPLES if p.stem == "example-event-driven"), src)
    out_dir = tmp_path / "out"
    t0 = time.time()
    res = export(src, out_dir, SETTINGS)
    print(f"export took {time.time() - t0:.1f}s")
    _check_export(res, out_dir)


@needs_cli
def test_export_without_force_light(tmp_path):
    src = tmp_path / "diagram.drawio"
    shutil.copyfile(EXAMPLES[0], src)
    res = export(src, tmp_path / "out", Settings(svg_force_light=False))
    assert not res.errors and res.svg is not None
    assert "color-scheme: light dark" in res.svg.read_text(encoding="utf-8")


@needs_cli
def test_export_two_concurrent(tmp_path):
    jobs = []
    for i, ex in enumerate(EXAMPLES[:2]):
        src = tmp_path / f"in{i}" / "diagram.drawio"
        src.parent.mkdir()
        shutil.copyfile(ex, src)
        jobs.append((src, tmp_path / f"out{i}"))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda j: export(j[0], j[1], SETTINGS), jobs))
    for res, (_, out_dir) in zip(results, jobs):
        _check_export(res, out_dir)


@needs_cli
def test_export_bad_input_collects_errors(tmp_path):
    src = tmp_path / "bad.drawio"
    src.write_text("not xml at all", encoding="utf-8")
    res = export(src, tmp_path / "out", SETTINGS)       # must not raise
    assert res.errors


def test_export_missing_cli_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr("diagram_generator.render.drawio_tools.find_drawio_cli", lambda s: None)
    res = export(tmp_path / "x.drawio", tmp_path / "out", SETTINGS)
    assert res.png is None and res.svg is None and res.errors
