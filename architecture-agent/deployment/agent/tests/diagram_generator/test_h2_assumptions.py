import pytest

from diagram_generator.config import Settings
from diagram_generator.contracts import Extraction, IconRef, SourceDoc
from diagram_generator.harness.h2_assumptions import check_assumptions

DOC = ("Orders arrive from the web app and are processed by an AWS Lambda function. "
       "Results are stored in Amazon DynamoDB. A queue buffers the incoming orders.")
SOURCES = [SourceDoc(file="adr.md", text=DOC, chars_original=len(DOC))]
SETTINGS = Settings()


class StubCatalog:
    def __init__(self):
        self.calls = []

    def resolve(self, service, name, kind):
        self.calls.append((service, name, kind))
        return IconRef(shape=f"icon_{(service or name).replace(' ', '_').lower()}", category="stub",
                       fill_color="#123456", matched=service is not None)


def ev(quote="A queue buffers the incoming orders", file="adr.md"):
    return {"file": file, "quote": quote, "reasoning": "signals it"}


def extraction():
    e = {"file": "adr.md", "quote": "AWS Lambda", "reasoning": "r"}
    return Extraction.model_validate({
        "title": "Orders",
        "components": [
            {"id": "web_app", "name": "Web app", "kind": "external", "strength": "stated", "evidence": [e]},
            {"id": "orders_fn", "name": "Orders", "service": "AWS Lambda", "strength": "stated", "evidence": [e]},
            {"id": "orders_table", "name": "Orders table", "service": "Amazon DynamoDB", "kind": "data_store",
             "strength": "inferred", "evidence": [e]},
        ],
        "relationships": [
            {"id": "r1", "source": "web_app", "target": "orders_fn", "label": "sends orders", "strength": "stated", "evidence": [e]},
            {"id": "r2", "source": "orders_fn", "target": "orders_table", "label": "stores results", "strength": "inferred", "evidence": [e]},
        ],
        "boundaries": [{"id": "main_vpc", "type": "vpc", "name": "Main VPC", "strength": "stated", "evidence": [e]}],
    })


def assumed_queue(**kw):
    d = {"id": "a_queue", "name": "Order queue", "service": "Amazon SQS", "kind": "aws_service",
         "supports_ids": ["orders_fn"], "reasoning": "Lambda needs a producer-side buffer.", "evidence": [ev()]}
    d.update(kw)
    return d


def assumed_rel(**kw):
    d = {"id": "ar1", "source": "a_queue", "target": "orders_fn", "label": "triggers", "supports_ids": ["orders_fn"],
         "reasoning": "Queue feeds the function.", "evidence": [ev()]}
    d.update(kw)
    return d


def run(raw, ext=None, cat=None, settings=SETTINGS):
    return check_assumptions(raw, ext or extraction(), SOURCES, settings, cat or StubCatalog())


def test_good_assumptions_accepted_with_origin_assumed():
    raw = {"components": [assumed_queue()], "relationships": [assumed_rel()],
           "unresolved": [{"about": "Auth", "reasoning": "no signal", "suggestion": "Confirm login method.",
                           "related_ids": ["web_app"]}]}
    out, spec, rec = run(raw)
    assert rec.ok and rec.step == "harness_2" and not rec.dropped
    assert [c.id for c in out.components] == ["a_queue"] and [r.id for r in out.relationships] == ["ar1"]
    assert rec.added_ids == ["a_queue", "ar1"]
    origins = {c.id: c.origin for c in spec.components}
    assert origins == {"web_app": "stated", "orders_fn": "stated", "orders_table": "inferred", "a_queue": "assumed"}
    assert {r.id: r.origin for r in spec.relationships} == {"r1": "stated", "r2": "inferred", "ar1": "assumed"}
    assert {b.id: b.origin for b in spec.boundaries} == {"main_vpc": "stated"}
    assert spec.title == "Orders"
    assert len(out.unresolved) == 1 and out.unresolved[0].related_ids == ["web_app"]


def test_counts_reconcile():
    raw = {"components": [assumed_queue()], "relationships": [assumed_rel()],
           "boundaries": [{"id": "a_sub", "type": "subnet_private", "name": "Private", "parent": "main_vpc",
                           "supports_ids": ["orders_fn"], "reasoning": "x", "evidence": [ev()]}]}
    out, spec, rec = run(raw)
    assert rec.in_counts == {"components": 3, "relationships": 2, "boundaries": 1}
    assert rec.out_counts["components"] == 4 and len(spec.components) == 4
    assert rec.out_counts["relationships"] == 3 and rec.out_counts["boundaries"] == 2
    assert (rec.out_counts["assumed_components"], rec.out_counts["assumed_relationships"],
            rec.out_counts["assumed_boundaries"], rec.out_counts["unresolved"]) == (1, 1, 1, 0)


def test_icons_come_from_catalog_stub():
    cat = StubCatalog()
    out, spec, rec = run({"components": [assumed_queue()]}, cat=cat)
    assert len(cat.calls) == 4 and ("Amazon SQS", "Order queue", "aws_service") in cat.calls
    icons = {c.id: c.icon for c in spec.components}
    assert icons["a_queue"].shape == "icon_amazon_sqs" and icons["a_queue"].fill_color == "#123456"
    assert icons["web_app"].matched is False and icons["orders_fn"].shape == "icon_aws_lambda"


def test_empty_output_is_fine():
    out, spec, rec = run({})
    assert rec.ok and len(spec.components) == 3 and not rec.added_ids
    out, spec, rec = run("garbage")
    assert rec.ok and rec.warnings


@pytest.mark.parametrize("existing", ["orders_fn", "main_vpc", "r1"])
def test_id_collision_dropped(existing):
    out, spec, rec = run({"components": [assumed_queue(id=existing)]})
    assert out.components == [] and len(spec.components) == 3
    assert rec.dropped[0]["id"] == existing and "already exists" in rec.dropped[0]["reason"]


def test_duplicate_within_assumed_dropped():
    out, spec, rec = run({"components": [assumed_queue(), assumed_queue()]})
    assert len(out.components) == 1 and len(rec.dropped) == 1


def test_unknown_supports_ids_dropped():
    out, spec, rec = run({"components": [assumed_queue(supports_ids=["ghost"])]})
    assert out.components == [] and "unknown supports_ids: ghost" in rec.dropped[0]["reason"]


def test_empty_supports_ids_dropped():
    out, spec, rec = run({"components": [assumed_queue(supports_ids=[])]})
    assert out.components == [] and "supports_ids is empty" in rec.dropped[0]["reason"]


def test_no_evidence_dropped():
    out, _, rec = run({"components": [assumed_queue(evidence=[])]})
    assert out.components == [] and rec.dropped[0]["reason"] == "no evidence quote"
    out, _, rec = run({"components": [assumed_queue(evidence=[ev("  ")])]})
    assert out.components == [] and rec.dropped[0]["reason"] == "no evidence quote"


def test_unknown_boundary_and_endpoints_dropped():
    raw = {"components": [assumed_queue(boundary="nowhere")],
           "relationships": [assumed_rel(source="ghost"), assumed_rel(id="ar2", source="orders_fn", target="ghost")]}
    out, spec, rec = run(raw)
    assert out.components == [] and out.relationships == []
    reasons = {d["id"]: d["reason"] for d in rec.dropped}
    assert "unknown boundary" in reasons["a_queue"] and "unknown source" in reasons["ar1"] and "unknown target" in reasons["ar2"]


def test_relationship_dropped_when_its_assumed_component_was_dropped():
    raw = {"components": [assumed_queue(evidence=[])], "relationships": [assumed_rel()]}
    out, spec, rec = run(raw)
    assert out.relationships == [] and {d["id"] for d in rec.dropped} == {"a_queue", "ar1"}
    assert len(spec.relationships) == 2


def test_schema_invalid_item_dropped_not_failed():
    raw = {"components": [{"id": "a_x"}, assumed_queue()]}
    out, spec, rec = run(raw)
    assert rec.ok and [c.id for c in out.components] == ["a_queue"]
    assert rec.dropped[0]["id"] == "a_x" and rec.dropped[0]["reason"].startswith("schema:")


def test_assumed_boundary_parent_order_tolerated_and_cycle_dropped():
    def b(i, parent):
        return {"id": i, "type": "subnet_private", "name": i, "parent": parent, "supports_ids": ["orders_fn"],
                "reasoning": "x", "evidence": [ev()]}
    out, spec, rec = run({"boundaries": [b("a_child", "a_parent"), b("a_parent", "main_vpc")]})
    assert {x.id for x in out.boundaries} == {"a_child", "a_parent"}
    out, spec, rec = run({"boundaries": [b("a_x", "a_y"), b("a_y", "a_x")]})
    assert out.boundaries == [] and len(rec.dropped) == 2


def test_unverified_quote_drops_the_assumption():
    out, _, rec = run({"components": [assumed_queue(evidence=[ev("not in the doc at all")])]})
    assert out.components == [] and rec.out_counts["unverified_quotes"] == 1
    assert rec.details["unverified_quotes"][0]["id"] == "a_queue"
    assert any(d["id"] == "a_queue" and "evidence not found" in d["reason"] for d in rec.dropped)


def test_cap_warning():
    def many(n):
        return {"components": [assumed_queue(id=f"a_c{i}") for i in range(n)]}
    # 3 extracted components -> threshold max(3, 0.9) = 3
    _, _, rec = run(many(3))
    assert not any("exceeds the cap" in w for w in rec.warnings)
    _, _, rec = run(many(4))
    assert rec.ok and any("exceeds the cap" in w for w in rec.warnings)


def test_unresolved_unknown_related_ids_filtered():
    raw = {"unresolved": [{"about": "DB", "reasoning": "r", "suggestion": "Confirm engine.",
                           "related_ids": ["orders_fn", "ghost"]}]}
    out, _, rec = run(raw)
    assert out.unresolved[0].related_ids == ["orders_fn"] and any("unknown related_ids" in w for w in rec.warnings)


def test_extraction_unchanged():
    ext = extraction()
    before = ext.model_dump()
    run({"components": [assumed_queue(id="orders_fn")]}, ext=ext)
    assert ext.model_dump() == before
