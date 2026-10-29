import copy

from diagram_generator.config import Settings
from diagram_generator.contracts import SourceDoc
from diagram_generator.harness.h1_extraction import check_extraction

DOC_A = "The Orders API runs on AWS Lambda. It writes orders to Amazon DynamoDB. Users reach it through Amazon API Gateway."
DOC_B = "The reporting job reads from DynamoDB. The database is Amazon RDS."
SOURCES = [SourceDoc(file="a.md", text=DOC_A, chars_original=len(DOC_A)),
           SourceDoc(file="b.md", text=DOC_B, chars_original=len(DOC_B))]
SETTINGS = Settings()


def ev(quote, file="a.md"):
    return {"file": file, "quote": quote, "reasoning": "names it"}


def comp(cid, quote, service="AWS Lambda", strength="stated", file="a.md", **kw):
    return {"id": cid, "name": cid, "service": service, "kind": "aws_service", "strength": strength,
            "evidence": [ev(quote, file)], **kw}


def rel(rid, s, t, quote="It writes orders to Amazon DynamoDB", **kw):
    return {"id": rid, "source": s, "target": t, "label": "writes orders", "strength": "stated",
            "evidence": [ev(quote)], **kw}


def good():
    return {
        "title": "Orders",
        "components": [
            comp("orders_api", "The Orders API runs on AWS Lambda"),
            comp("orders_table", "writes orders to Amazon DynamoDB", service="Amazon DynamoDB"),
            comp("gateway", "through Amazon API Gateway", service="Amazon API Gateway"),
        ],
        "relationships": [rel("r1", "orders_api", "orders_table"),
                          rel("r2", "gateway", "orders_api", "Users reach it through Amazon API Gateway")],
        "boundaries": [],
        "contradictions": [],
        "resolutions": [],
    }


def contradiction(removed):
    return {
        "subject": "database engine",
        "claim_a": {"statement": "DynamoDB", "strength": "stated", "evidence": ev("reads from DynamoDB", "b.md")},
        "claim_b": {"statement": "RDS", "strength": "stated", "evidence": ev("The database is Amazon RDS", "b.md")},
        "removed_ids": removed,
        "note": "clash",
    }


def test_valid_extraction_ok_and_counts():
    ext, rec = check_extraction(good(), SOURCES, SETTINGS)
    assert rec.ok and ext is not None and not rec.errors
    assert rec.step == "harness_1"
    assert rec.in_counts == {"files": 2, "chars": len(DOC_A) + len(DOC_B)}
    assert rec.out_counts == {"components": 3, "relationships": 2, "boundaries": 0, "contradictions": 0,
                              "resolutions": 0, "unverified_quotes": 0}
    assert rec.dropped == []


def test_schema_error_is_blocking():
    raw = good()
    del raw["title"]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None and not rec.ok
    assert any(e.startswith("schema:") and "title" in e for e in rec.errors)


def test_non_dict_is_blocking():
    ext, rec = check_extraction("nope", SOURCES, SETTINGS)
    assert ext is None and not rec.ok


def test_duplicate_component_id_blocking():
    raw = good()
    raw["components"].append(comp("orders_api", "The Orders API runs on AWS Lambda"))
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None and any("duplicate id 'orders_api'" in e for e in rec.errors)


def test_component_and_boundary_share_one_id_space():
    raw = good()
    raw["boundaries"] = [{"id": "gateway", "type": "vpc", "name": "VPC", "evidence": [ev("The Orders API")]}]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None and any("duplicate id 'gateway'" in e for e in rec.errors)


def test_relationship_id_cannot_collide_with_component():
    raw = good()
    raw["relationships"][0]["id"] = "gateway"
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None and any("duplicate id 'gateway'" in e for e in rec.errors)


def test_unknown_references_blocking():
    raw = good()
    raw["relationships"].append(rel("r3", "orders_api", "ghost"))
    raw["components"][0]["boundary"] = "nowhere"
    raw["boundaries"] = [{"id": "vpc", "type": "vpc", "name": "VPC", "parent": "also_nowhere",
                          "evidence": [ev("The Orders API")]}]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None
    joined = "\n".join(rec.errors)
    assert "target 'ghost'" in joined and "unknown boundary 'nowhere'" in joined and "unknown parent 'also_nowhere'" in joined


def test_boundary_parent_cycle_blocking():
    raw = good()
    raw["boundaries"] = [
        {"id": "b1", "type": "vpc", "name": "V1", "parent": "b2", "evidence": [ev("The Orders API")]},
        {"id": "b2", "type": "vpc", "name": "V2", "parent": "b1", "evidence": [ev("The Orders API")]},
    ]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None and any("cycle" in e for e in rec.errors)


def test_no_evidence_blocking():
    raw = good()
    raw["components"][0]["evidence"] = []
    raw["relationships"][0]["evidence"] = [ev("   ")]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert ext is None
    assert any("component 'orders_api' has no evidence" in e for e in rec.errors)
    assert any("relationship 'r1' has no evidence" in e for e in rec.errors)


def test_contradiction_items_left_in_lists_are_dropped_and_logged():
    raw = good()
    raw["components"] += [
        comp("db_dynamo", "reads from DynamoDB", service="Amazon DynamoDB", file="b.md"),
        comp("db_rds", "The database is Amazon RDS", service="Amazon RDS", file="b.md"),
    ]
    raw["relationships"] += [rel("r3", "orders_api", "db_dynamo", "reads from DynamoDB"),
                             rel("r4", "db_rds", "orders_api", "The database is Amazon RDS")]
    raw["contradictions"] = [contradiction(["db_dynamo", "db_rds"])]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok and ext is not None
    assert {c.id for c in ext.components} == {"orders_api", "orders_table", "gateway"}
    assert {r.id for r in ext.relationships} == {"r1", "r2"}
    dropped = {d["id"]: d["reason"] for d in rec.dropped}
    assert set(dropped) == {"db_dynamo", "db_rds", "r3", "r4"}
    assert "contradiction" in dropped["db_dynamo"] and "contradicted component" in dropped["r3"]
    assert rec.out_counts["contradictions"] == 1 and rec.out_counts["components"] == 3
    assert rec.details["contradictions"][0]["removed_ids"] == ["db_dynamo", "db_rds"]


def test_contradiction_correctly_omitted_but_dependent_relationship_left():
    raw = good()
    raw["relationships"].append(rel("r3", "orders_api", "db_rds", "The database is Amazon RDS"))
    raw["contradictions"] = [contradiction(["db_dynamo", "db_rds"])]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok and [d["id"] for d in rec.dropped] == ["r3"]


def test_removed_boundary_reparents_children_with_warning():
    raw = good()
    raw["boundaries"] = [
        {"id": "vpc", "type": "vpc", "name": "VPC", "evidence": [ev("The Orders API")]},
        {"id": "sub", "type": "subnet_private", "name": "Sub", "parent": "vpc", "evidence": [ev("The Orders API")]},
    ]
    raw["components"][0]["boundary"] = "vpc"
    raw["contradictions"] = [contradiction(["vpc"])]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok
    assert [b.id for b in ext.boundaries] == ["sub"] and ext.boundaries[0].parent is None
    assert ext.components[0].boundary is None
    assert any("parent set to null" in w for w in rec.warnings)
    assert any("boundary set to null" in w for w in rec.warnings)


def test_contradiction_without_removed_ids_warns():
    raw = good()
    raw["contradictions"] = [contradiction([])]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok and any("lists no removed_ids" in w for w in rec.warnings)


def test_unverified_quote_is_warning_only():
    raw = good()
    raw["components"][0]["evidence"] = [ev("this sentence is not in the document")]
    raw["components"][1]["evidence"] = [ev("writes orders to Amazon DynamoDB", file="missing.pdf")]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok and ext is not None and len(ext.components) == 3     # never dropped
    assert rec.out_counts["unverified_quotes"] == 2
    uq = rec.details["unverified_quotes"]
    assert {(u["id"], u["file"]) for u in uq} == {("orders_api", "a.md"), ("orders_table", "missing.pdf")}
    assert uq[0]["quote"] == "this sentence is not in the document"
    assert any("unverified quote" in w for w in rec.warnings)


def test_quote_with_pdf_artefacts_still_verified():
    raw = good()
    raw["components"][0]["evidence"] = [ev("The Orders API  runs on AWS Lambda")]
    ext, rec = check_extraction(raw, SOURCES, SETTINGS)
    assert rec.ok and rec.out_counts["unverified_quotes"] == 0


def test_warnings_too_many_components_missing_service_long_label_empty():
    settings = Settings(max_components_warn=2)
    raw = good()
    raw["components"][0]["service"] = None
    raw["relationships"][0]["label"] = "sends data over a very long path"
    ext, rec = check_extraction(raw, SOURCES, settings)
    assert rec.ok
    joined = "\n".join(rec.warnings)
    assert "exceeds 2" in joined and "no catalog `service`" in joined and "more than 4 words" in joined

    ext, rec = check_extraction({"title": "x"}, SOURCES, SETTINGS)
    assert rec.ok and any("no components extracted" in w for w in rec.warnings)


def test_input_not_mutated():
    raw = good()
    raw["contradictions"] = [contradiction(["gateway"])]
    snapshot = copy.deepcopy(raw)
    check_extraction(raw, SOURCES, SETTINGS)
    assert raw == snapshot
