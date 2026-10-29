"""Harness 3 tests: parse + check_drawio against small hand-built diagrams."""
from __future__ import annotations

import pytest

from diagram_generator.config import Settings
from diagram_generator.contracts import (FinalSpec, IconRef, SpecBoundary, SpecComponent, SpecRelationship)
from diagram_generator.harness.h3_drawio import check_drawio, parse_drawio
from diagram_generator.render.drawio_tools import ValidationOutcome, validate

ICON_STYLE = ("sketch=0;outlineConnect=0;html=1;shape=mxgraph.aws4.resourceIcon;"
              "resIcon=mxgraph.aws4.{shape};")
OK_VALIDATION = ValidationOutcome(ok=True, errors=[], warnings=[], raw="")


def _icon(shape="lambda"):
    return IconRef(shape=shape, category="compute", fill_color="#ED7100")


def _spec(rels=(("api", "fn"),), with_vpc=True, direction="one_way") -> FinalSpec:
    comps = [SpecComponent(id=i, name=i, origin="stated", icon=_icon()) for i in ("api", "fn", "db")]
    comps[0].kind = "aws_service"
    return FinalSpec(
        title="t",
        components=comps,
        relationships=[SpecRelationship(id=f"r{n}", source=s, target=t, label="x", direction=direction,
                                       origin="stated")
                       for n, (s, t) in enumerate(rels, 1)],
        boundaries=[SpecBoundary(id="vpc", type="vpc", name="VPC", origin="stated")] if with_vpc else [],
    )


def _xml(cells: str) -> str:
    return ('<mxfile><diagram id="d" name="p"><mxGraphModel><root><mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>' + cells + "</root></mxGraphModel></diagram></mxfile>")


def _icon_cell(cid, parent="1", shape="lambda"):
    return (f'<mxCell id="{cid}" value="{cid}" style="{ICON_STYLE.format(shape=shape)}" vertex="1" parent="{parent}">'
            '<mxGeometry x="0" y="0" width="48" height="48" as="geometry"/></mxCell>')


def _edge(eid, s, t, label=""):
    return (f'<mxCell id="{eid}" value="{label}" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" '
            f'source="{s}" target="{t}"><mxGeometry relative="1" as="geometry"/></mxCell>')


VPC = ('<mxCell id="vpc" value="VPC" style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc;" '
       'vertex="1" parent="1"><mxGeometry x="0" y="0" width="500" height="300" as="geometry"/></mxCell>')
BASE = VPC + _icon_cell("api") + _icon_cell("fn") + _icon_cell("db")


# ----------------------------------------------------------------------------- parse
def test_parse_basic():
    facts = parse_drawio(_xml(BASE + _edge("e1", "api", "fn", "invoke")))
    assert {"api", "fn", "db", "vpc"} <= set(facts.vertices)
    assert facts.vertices["api"]["parent"] == "1"
    assert facts.edges == [("e1", "api", "fn", "invoke")]
    assert facts.parse_error is None


def test_parse_edge_label_child_cell():
    label = ('<mxCell id="e1l" value="calls" style="edgeLabel;html=1;" vertex="1" connectable="0" parent="e1">'
             '<mxGeometry relative="1" as="geometry"/></mxCell>')
    facts = parse_drawio(_xml(BASE + _edge("e1", "api", "fn") + label))
    assert facts.edges[0][3] == "calls"


def test_parse_garbage_does_not_raise():
    facts = parse_drawio("<not-xml")
    assert facts.parse_error


# ----------------------------------------------------------------------------- check
def test_all_found_ok():
    rec = check_drawio(_xml(BASE + _edge("e1", "api", "fn")), _spec(), OK_VALIDATION, 1)
    assert rec.step == "harness_3" and rec.ok, rec.errors
    assert rec.in_counts == {"components": 3, "relationships": 1, "boundaries": 1}
    assert rec.out_counts == {"components_found": 3, "relationships_found": 1, "boundaries_found": 1,
                              "extra_icons": 0, "edges_total": 1}
    assert not rec.errors and not rec.warnings


def test_missing_component_is_error():
    xml = _xml(VPC + _icon_cell("api") + _icon_cell("fn") + _edge("e1", "api", "fn"))
    rec = check_drawio(xml, _spec(), OK_VALIDATION, 1)
    assert not rec.ok
    assert rec.details["missing_components"] == ["db"]
    assert rec.out_counts["components_found"] == 2


def test_missing_relationship_is_error():
    rec = check_drawio(_xml(BASE), _spec(), OK_VALIDATION, 2)
    assert not rec.ok and rec.attempt == 2
    assert rec.details["missing_relationships"] == [["api", "fn"]]
    assert rec.out_counts["relationships_found"] == 0


def test_reversed_is_warning():
    rec = check_drawio(_xml(BASE + _edge("e1", "fn", "api")), _spec(), OK_VALIDATION, 1)
    assert rec.ok
    assert rec.details["reversed_relationships"] == [["api", "fn"]]
    assert any("opposite direction" in w for w in rec.warnings)
    assert rec.out_counts["relationships_found"] == 1


def test_reversed_bidirectional_no_warning():
    rec = check_drawio(_xml(BASE + _edge("e1", "fn", "api")), _spec(direction="bidirectional"), OK_VALIDATION, 1)
    assert rec.ok and not rec.warnings


def test_extra_icon_is_warning():
    xml = _xml(BASE + _icon_cell("stray", shape="sqs") + _edge("e1", "api", "fn"))
    rec = check_drawio(xml, _spec(), OK_VALIDATION, 1)
    assert rec.ok
    assert rec.details["extra_icon_ids"] == ["stray"]
    assert rec.out_counts["extra_icons"] == 1


def test_decor_cells_are_not_extra_icons():
    cells = (VPC + _icon_cell("api") + _icon_cell("fn") + _icon_cell("db")
             + _icon_cell("api__icon", parent="api__box") + _icon_cell("fn__label")
             + _edge("e1", "api", "fn"))
    rec = check_drawio(_xml(cells), _spec(), OK_VALIDATION, 1)
    assert rec.ok and rec.out_counts["extra_icons"] == 0


def test_missing_boundary_is_warning_not_error():
    xml = _xml(_icon_cell("api") + _icon_cell("fn") + _icon_cell("db") + _edge("e1", "api", "fn"))
    rec = check_drawio(xml, _spec(), OK_VALIDATION, 1)
    assert rec.ok
    assert rec.details["missing_boundaries"] == ["vpc"]
    assert rec.out_counts["boundaries_found"] == 0


def test_dangling_edge_is_error():
    rec = check_drawio(_xml(BASE + _edge("e1", "api", "nowhere")), _spec(), OK_VALIDATION, 1)
    assert not rec.ok
    assert any("nowhere" in e for e in rec.errors)
    assert rec.details["dangling_edges"][0]["id"] == "nowhere"


def test_validator_errors_are_prefixed_and_block():
    bad = ValidationOutcome(ok=False, errors=["Missing root cell"], warnings=["small font"], raw="")
    rec = check_drawio(_xml(BASE + _edge("e1", "api", "fn")), _spec(), bad, 1)
    assert not rec.ok
    assert "validator: Missing root cell" in rec.errors
    assert "validator: small font" in rec.warnings


@pytest.mark.parametrize("suffix", ["__box", "__icon", "__label"])
def test_edge_attached_to_decor_cell_resolves(suffix):
    cells = BASE + _icon_cell("api" + suffix) + _edge("e1", "api" + suffix, "fn")
    rec = check_drawio(_xml(cells), _spec(), OK_VALIDATION, 1)
    assert rec.ok, rec.errors
    assert rec.out_counts["relationships_found"] == 1


def test_edge_attached_to_parent_container_resolves():
    # actor style: visible container "api_c" holds the spec-id cell "api"
    cells = (VPC + _icon_cell("api_c") + _icon_cell("api", parent="api_c") + _icon_cell("fn") + _icon_cell("db")
             + _edge("e1", "api_c", "fn"))
    rec = check_drawio(_xml(cells), _spec(), OK_VALIDATION, 1)
    assert rec.ok, rec.errors


def test_edge_attached_to_child_resolves():
    # spec id is the container, edge attaches to the inner icon
    cells = (VPC + _icon_cell("api") + _icon_cell("inner_icon", parent="api") + _icon_cell("fn") + _icon_cell("db")
             + _edge("e1", "inner_icon", "fn"))
    rec = check_drawio(_xml(cells), _spec(), OK_VALIDATION, 1)
    assert rec.ok, rec.errors


def test_boundary_container_does_not_swallow_components():
    # an edge to the VPC group must not count as an edge to every component inside it
    cells = (VPC + _icon_cell("api", parent="vpc") + _icon_cell("fn", parent="vpc") + _icon_cell("db", parent="vpc")
             + _edge("e1", "api", "vpc"))
    rec = check_drawio(_xml(cells), _spec(), OK_VALIDATION, 1)
    assert not rec.ok
    assert rec.details["missing_relationships"] == [["api", "fn"]]


def test_unparseable_xml_is_error():
    rec = check_drawio("<mxfile><oops", _spec(), OK_VALIDATION, 1)
    assert not rec.ok and rec.errors


def test_json_round_trip():
    rec = check_drawio(_xml(BASE + _edge("e1", "api", "fn")), _spec(), OK_VALIDATION, 1)
    assert rec.model_dump_json()


# ----------------------------------------------------------------------------- end to end with real validator
def test_real_validator_through_h3(tmp_path):
    path = tmp_path / "diagram.drawio"
    xml = _xml(BASE + _edge("e1", "api", "fn"))
    path.write_text(xml, encoding="utf-8")
    val = validate(path, Settings())
    rec = check_drawio(xml, _spec(), val, 1)
    assert rec.ok, (rec.errors, val.raw)
