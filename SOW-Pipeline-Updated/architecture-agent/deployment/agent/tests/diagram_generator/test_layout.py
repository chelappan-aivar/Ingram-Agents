"""Layout checks, XML builder and the layout coder's prompt/tool (no AWS)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagram_generator.contracts import FinalSpec, Layout
from diagram_generator.harness.h3_drawio import check_drawio
from diagram_generator.harness.layout_check import check_layout, fit_groups
from diagram_generator.llm.bedrock import FakeLLM
from diagram_generator.llm.layout_coder import TOOL_NAME, build_tool_spec, build_user_message, generate_layout, retry_layout
from diagram_generator.render.drawio_builder import build_xml
from diagram_generator.render.drawio_tools import find_drawio_cli, validate
from tests.diagram_generator.conftest import make_settings

FIX = Path(__file__).resolve().parent / "fixtures"


def spec() -> FinalSpec:
    return FinalSpec.model_validate_json((FIX / "coder_sample_spec.json").read_text())


def layout(**changes) -> Layout:
    data = json.loads((FIX / "coder_sample_layout.json").read_text())
    for key, fn in changes.items():
        fn(data[key])
    return Layout.model_validate(data)


def move(node_id, x, y):
    def fn(nodes):
        for n in nodes:
            if n["id"] == node_id:
                n.update(x=x, y=y)
    return fn


def test_reference_layout_is_clean():
    rep = check_layout(spec(), layout())
    assert rep.errors == [], rep.errors
    assert rep.counts["nodes"] == 8 and rep.counts["groups"] == 4


def test_overlap_and_boundary_errors():
    rep = check_layout(spec(), layout(nodes=move("kinesis_stream", 400, 340)))
    assert any("overlap" in e and "kinesis_stream" in e for e in rep.errors)
    rep = check_layout(spec(), layout(nodes=move("stream_processor", 700, 600)))  # outside its subnet
    assert any("stream_processor" in e and "inside its boundary 'subnet_private'" in e for e in rep.errors)
    rep = check_layout(spec(), layout(nodes=move("iot_core", 960, 330)))  # region-level box inside the VPC
    assert any("iot_core" in e and "does not belong to" in e for e in rep.errors)
    rep = check_layout(spec(), layout(nodes=move("vehicle_fleet", 300, 600)))  # actor inside the cloud
    assert any("vehicle_fleet" in e and "aws_cloud" in e for e in rep.errors)


def test_missing_items():
    rep = check_layout(spec(), layout(nodes=lambda ns: ns.pop(), groups=lambda gs: gs.pop()))
    assert any("ops_api" in e and "no position" in e for e in rep.errors)
    assert any("subnet_private" in e and "no rectangle" in e for e in rep.errors)


def resize(group_id, **geo):
    def fn(groups):
        for g in groups:
            if g["id"] == group_id:
                g.update(**geo)
    return fn


def test_group_containment():
    rep = check_layout(spec(), layout(groups=resize("vpc_main", w=150)))
    assert any("subnet_private" in e and "inside its parent 'vpc_main'" in e for e in rep.errors)


def test_fit_groups_grows_around_a_poking_member():
    # subnet 890..1150 x 290..470; stream_processor box 960..1080 x 330..450 pokes out on the right and bottom
    bad = layout(groups=resize("subnet_private", w=150, h=150))
    assert any("stream_processor" in e for e in check_layout(spec(), bad).errors)
    fitted, notes = fit_groups(spec(), bad)
    assert check_layout(spec(), fitted).errors == []
    sub = next(g for g in fitted.groups if g.id == "subnet_private")
    assert (sub.x, sub.y) == (890, 290) and sub.x + sub.w >= 1100 and sub.y + sub.h >= 470  # grown, not moved
    assert [n for n in notes if "subnet_private" in n and "stream_processor" in n]
    assert fitted.nodes == bad.nodes  # boxes never move


def test_fit_groups_keeps_a_clean_layout_and_skips_far_members():
    assert fit_groups(spec(), layout()) == (layout(), [])
    far = layout(nodes=move("stream_processor", 1360, 700))  # wholly outside its subnet: a placement mistake
    fitted, notes = fit_groups(spec(), far)
    assert notes == [] and fitted == far


def test_fit_groups_never_grows_into_the_title_band():
    # moving the cloud's top edge up to fit iot_core would put it in the title band: no growth there
    tight = layout(groups=resize("aws_cloud", y=200, h=630), nodes=move("iot_core", 360, 150))
    fitted, _ = fit_groups(spec(), tight)
    assert all(g.y >= 120 for g in fitted.groups)
    assert len(check_layout(spec(), fitted).errors) <= len(check_layout(spec(), tight).errors)


def test_builder_output_passes_validator_and_coverage(tmp_path):
    xml = build_xml(spec(), layout())
    p = tmp_path / "d.drawio"
    p.write_text(xml)
    v = validate(p, make_settings(tmp_path))
    assert v.ok, v.errors
    rec = check_drawio(xml, spec(), v, 1)
    assert rec.ok, rec.errors
    assert rec.out_counts["components_found"] == 8 and rec.out_counts["relationships_found"] == 8
    assert "Fleet Telemetry Ingestion" in xml and "startArrow=none" in xml
    assert "exitX" not in xml and "<Array" not in xml  # edges float until edge_router routes them


@pytest.mark.skipif(find_drawio_cli(None) is None, reason="draw.io CLI not installed")
def test_builder_output_exports(tmp_path):
    from diagram_generator.render.drawio_tools import export
    p = tmp_path / "diagram.drawio"
    p.write_text(build_xml(spec(), layout()))
    res = export(p, tmp_path, make_settings(tmp_path))
    assert res.png and res.png.stat().st_size > 10_000 and not res.errors


def test_layout_coder_prompt_and_retry():
    s = make_settings(Path("/tmp"))
    tool = build_tool_spec()
    assert tool["toolSpec"]["name"] == TOOL_NAME
    props = tool["toolSpec"]["inputSchema"]["json"]["properties"]
    assert {"nodes", "groups", "subtitle"} <= set(props) and "edges" not in props and "$defs" not in json.dumps(tool)
    msg = build_user_message(spec())
    for item in ["fleet_ops", "region_use1", "r8", "8 nodes, 4 groups (8 relationships drawn by code)"]:
        assert item in msg
    good = json.loads((FIX / "coder_sample_layout.json").read_text())
    llm = FakeLLM(tool_responses=[good, good])
    out = generate_layout(llm, s, spec())
    assert len(out.nodes) == 8 and llm.tool_calls[0]["tool_spec"]["toolSpec"]["name"] == TOOL_NAME
    retry_layout(llm, s, spec(), out, ["components 'a' and 'b' overlap"])
    text = llm.tool_calls[1]["messages"][0]["content"][0]["text"]
    assert "Your previous layout" in text and "components 'a' and 'b' overlap" in text
