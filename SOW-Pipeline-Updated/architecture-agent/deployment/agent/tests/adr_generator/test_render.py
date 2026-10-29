"""Tests for the adr.md and spec.json renderers."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from adr_generator.contracts import SCHEMA_VERSION, Design
from adr_generator.render.adr_md import render_adr
from adr_generator.render.spec_json import build_spec

FIX = Path(__file__).parent / "fixtures"
HEADINGS = [
    "## 1. Context", "## 2. Requirements addressed", "## 3. Architecture decisions", "## 4. Network layout",
    "## 5. Components", "## 6. Request and data flow", "## 7. Security and operations", "## 8. Assumptions",
    "## 9. Open questions",
]


def _load(name: str) -> Design:
    return Design.model_validate(json.loads((FIX / name).read_text()))


def _render(d: Design) -> str:
    return render_adr(d, source_name="srs.md", run_date="2026-10-09")


def _section(md: str, heading: str) -> str:
    start = md.index(heading) + len(heading)
    m = re.search(r"^## ", md[start:], re.M)
    return md[start:start + m.start()] if m else md[start:]


@pytest.fixture(scope="module")
def full() -> Design:
    return _load("design_full.json")


@pytest.fixture(scope="module")
def full_md(full: Design) -> str:
    return _render(full)


def test_headings_in_order(full_md: str) -> None:
    assert full_md.startswith("# Architecture Decision Record: Billing Dispute Assistant on AWS\n")
    pos = [full_md.index("\n" + h + "\n") for h in HEADINGS]
    assert pos == sorted(pos)
    assert len(HEADINGS) == 9


def test_metadata_table(full_md: str) -> None:
    for s in ("| Customer | Northbridge Cloud Distribution |", "| Status | Proposed |", "| Date | 2026-10-09 |",
              "| Region | us-east-1 |", "| Availability Zones | us-east-1a |",
              "| Source document | srs.md |", "| Components | 17 |"):
        assert s in full_md


def test_components_table_rows(full: Design, full_md: str) -> None:
    rows = [l for l in _section(full_md, "## 5. Components").splitlines() if l.startswith("| C-")]
    assert len(rows) == len(full.components)
    assert "| NAT Gateway (assumed) |" in full_md
    assert "db.t4g.medium, PostgreSQL 16.4, gp3 100 GiB, Single-AZ" in full_md


def test_every_component_in_network_layout(full: Design, full_md: str) -> None:
    layout = _section(full_md, "## 4. Network layout")
    assert "The solution runs in AWS Region us-east-1." in layout
    assert "Region-level services used:" in layout and "Outside AWS" in layout
    for c in full.components:
        assert c.name in layout, c.name


def test_flow_numbered_lines(full: Design, full_md: str) -> None:
    lines = [l for l in _section(full_md, "## 6. Request and data flow").splitlines() if l.strip()]
    assert len(lines) == len(full.relationships)
    for i, l in enumerate(lines, 1):
        assert l.startswith(f"{i}. ")
    assert lines[0].endswith("(F-01; HTTPS 443)")
    assert lines[8].endswith("(F-09)")          # no protocol/port


def test_markdown_hygiene(full_md: str) -> None:
    for md in (full_md, _render(Design()), _render(_load("design_partial.json"))):
        assert not re.search(r"</?[a-zA-Z][^>]*>", md)
        assert not md.startswith("---")
        assert "```" not in md
        assert md.endswith("\n") and not md.endswith("\n\n")
        for l in md.splitlines():
            if l.startswith("|"):                # one line per row, so cells carry no newlines
                assert l.endswith("|")


def test_pipe_escaping_and_newlines() -> None:
    d = Design.model_validate({"components": [
        {"id": "C-01", "name": "A|B", "service": "S\nT", "purpose": "x | y\nz"}]})
    md = _render(d)
    row = next(l for l in md.splitlines() if l.startswith("| C-01"))
    assert "A\\|B" in row and "x \\| y z" in row and "S T" in row
    assert len(re.findall(r"(?<!\\)\|", row)) == 8   # 7 cells -> 8 unescaped pipes


def test_empty_design_renders() -> None:
    md = _render(Design())
    assert "Not specified" in md and "None." in md
    for h in HEADINGS:
        assert h in md


def test_partial_design_renders() -> None:
    d = _load("design_partial.json")
    md = _render(d)
    assert "C-99" in md                                   # unknown id falls back to raw id
    assert "memory_mb: 256; architecture: arm64" in md    # list-of-{key,value} configuration
    assert "no_such_boundary" in md
    assert "Upload bucket (assumed)" in md
    assert "Bucket not named in the SRS" in md


def test_assumed_component_in_assumptions(full: Design, full_md: str) -> None:
    sect = _section(full_md, "## 8. Assumptions")
    assert "| A-01 |" in sect and "| A-03 |" in sect
    assert "| C-05 | NAT Gateway: NAT Gateway, public, 1 per AZ |" in sect


def test_decision_layout(full_md: str) -> None:
    assert "### ADR-001: Run services on ECS Fargate" in full_md
    assert "**Alternatives considered (rejected, not part of this design):** Amazon ECS on EC2 (adds host" in full_md
    assert "**Alternatives considered (rejected, not part of this design):** None recorded" in _render(
        Design.model_validate({"decisions": [{"id": "ADR-001"}]}))


def test_build_spec(full: Design) -> None:
    spec = build_spec(full, source_name="srs.md", generated_at="2026-10-09T12:00:00+00:00")
    assert spec["schema_version"] == SCHEMA_VERSION
    assert spec["source_srs"] == "srs.md" and spec["generated_at"].startswith("2026-10-09")
    assert set(spec) == {"schema_version", "generated_at", "source_srs", "title", "customer", "region",
                         "availability_zones", "boundaries", "components", "relationships", "assumptions"}
    assert len(spec["components"]) == len(full.components)
    assert len(spec["relationships"]) == len(full.relationships)
    assert len(spec["boundaries"]) == len(full.boundaries)
    assert len(spec["assumptions"]) == len(full.assumptions)
    comp = next(c for c in spec["components"] if c["id"] == "C-10")
    assert comp["placement"] == "private_subnet_a"
    assert comp["configuration"]["instance_type"] == "db.t4g.medium"
    assert set(comp) == {"id", "name", "service", "category", "role", "quantity", "placement", "configuration",
                         "sku_summary", "assumed", "assumption_reason", "requirement_ids", "decision_ids"}
    assert set(spec["relationships"][0]) == {"id", "source", "target", "label", "protocol", "port"}
    json.dumps(spec)


def test_build_spec_empty_and_partial() -> None:
    for d in (Design(), _load("design_partial.json")):
        json.dumps(build_spec(d, source_name="x", generated_at="t"))
