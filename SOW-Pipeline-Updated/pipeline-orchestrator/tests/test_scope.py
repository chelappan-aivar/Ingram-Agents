"""Extracting the agreed scope from a SOW: the handoff that makes architecture a second stage."""
from fakes import SOW_MARKDOWN

from orchestrator.scope import extract_scope, parse_sections, scope_is_usable


def test_in_and_out_of_scope_are_read_from_the_sow():
    scope = extract_scope(SOW_MARKDOWN, source="sow:OPP-1")
    assert "Amazon Kinesis streaming ingestion" in scope["out_of_scope"]
    assert "Data migration from the legacy platform" in scope["out_of_scope"]
    assert any("Amazon Bedrock (Claude) agents" in item for item in scope["in_scope"])
    assert scope["source"] == "sow:OPP-1"
    assert scope_is_usable(scope)


def test_exclusions_do_not_leak_into_the_in_scope_list():
    """'## Out of Scope' is nested inside '# Scope of Work', so its bullets sit in both bodies."""
    scope = extract_scope(SOW_MARKDOWN)
    for excluded in scope["out_of_scope"]:
        assert excluded not in scope["in_scope"]


def test_nested_subsections_are_inside_the_parent_body():
    """A parent section with subsections must not read as empty."""
    parsed = parse_sections(SOW_MARKDOWN)
    scope_section = next(s for s in parsed.sections if s.heading == "Scope of Work")
    assert "Amazon Bedrock" in scope_section.body
    assert "## In Scope" in scope_section.body


def test_heading_variants_are_all_recognised():
    for heading in ("Out of Scope", "Out-of-Scope", "Exclusions", "Not Included", "Not in Scope"):
        md = f"# Scope of Work\n## In Scope\n- Thing A\n## {heading}\n- Legacy decommissioning\n"
        scope = extract_scope(md)
        assert scope["out_of_scope"] == ["Legacy decommissioning"], heading


def test_numbered_and_bulleted_items_both_work():
    md = "# Scope of Work\n## In Scope\n1. First thing\n2) Second thing\n* Third thing\n"
    assert extract_scope(md)["in_scope"] == ["First thing", "Second thing", "Third thing"]


def test_markdown_emphasis_and_links_are_stripped():
    md = ("# Scope of Work\n## In Scope\n- **Amazon Bedrock** agents\n"
          "- [Amazon S3](https://aws.amazon.com/s3) storage\n")
    assert extract_scope(md)["in_scope"] == ["Amazon Bedrock agents", "Amazon S3 storage"]


def test_a_table_is_read_from_its_first_column():
    md = ("# Scope of Work\n## Out of Scope\n"
          "| Item | Reason |\n| --- | --- |\n| Data migration | Customer owns it |\n"
          "| Legacy decommissioning | Phase 2 |\n")
    assert extract_scope(md)["out_of_scope"] == ["Data migration", "Legacy decommissioning"]


def test_an_inline_sentence_is_read_when_there_is_no_section():
    md = ("# Scope of Work\nThe engagement covers claims triage.\n"
          "Out of scope: data migration, legacy decommissioning and user training.\n")
    out = extract_scope(md)["out_of_scope"]
    assert "data migration" in out and "legacy decommissioning" in out and "user training" in out


def test_prose_falls_back_to_sentences():
    md = "# Scope of Work\n## Out of Scope\nData migration is excluded. Training is excluded.\n"
    assert extract_scope(md)["out_of_scope"] == ["Data migration is excluded", "Training is excluded"]


def test_duplicates_are_collapsed():
    md = "# Scope of Work\n## Out of Scope\n- Data migration\n- data migration\n- Data Migration\n"
    assert len(extract_scope(md)["out_of_scope"]) == 1


def test_a_sow_with_no_scope_split_reports_empty_rather_than_guessing():
    """A wrong out-of-scope list would silently delete components, so guessing is not allowed."""
    scope = extract_scope("# Statement of Work\nSome commercial terms only.\n")
    assert scope["in_scope"] == [] and scope["out_of_scope"] == []
    assert not scope_is_usable(scope)


def test_never_raises_on_odd_markdown():
    for md in ("", "   ", "#", "####### seven", "no headings", "|||", "- bullet with no heading"):
        assert extract_scope(md) is not None


def test_scope_is_usable_needs_at_least_one_list():
    assert not scope_is_usable(None)
    assert not scope_is_usable({})
    assert not scope_is_usable({"in_scope": [], "out_of_scope": []})
    assert scope_is_usable({"out_of_scope": ["something"]})


# ----------------------------------------------------------------- the CVA template's own headings
CVA_SOW = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "samples" / "04-cva-woodgrove-bank" / "output" / "sow.md").read_text(encoding="utf-8")


def test_the_cva_exclusions_and_deliverables_are_read():
    """'Exclusion(s)' and 'Deliverable(s)' are the EMEA USC template's headings."""
    scope = extract_scope(CVA_SOW)
    assert any("Migration delivery or modernisation" in i for i in scope["out_of_scope"])
    assert any("Implementation, configuration, or modification" in i for i in scope["out_of_scope"])
    assert len(scope["out_of_scope"]) == 6


def test_the_cva_phases_are_read_as_in_scope_even_though_they_are_not_bullets():
    scope = extract_scope(CVA_SOW)
    phases = [i for i in scope["in_scope"] if i.startswith("Phase ")]
    assert len(phases) == 8, scope["in_scope"]
    assert "Phase 1: Tool Deployment, Configuration and Validation" in phases
    assert not any("Partner will ensure" in i for i in scope["in_scope"]), "assumptions are not scope"


def test_a_phased_scope_in_plain_lines_is_read():
    md = "# Scope of Work\n## In Scope\nPhase 1: Discovery. We interview people.\nPhase 2: Build. We build it.\n"
    assert extract_scope(md)["in_scope"] == ["Phase 1: Discovery", "Phase 2: Build"]
