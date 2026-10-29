"""The SOW's agreed scope, carried into the design and checked against the result."""
from adr_generator.contracts import Design
from adr_generator.scope import Scope, parse_scope, violations


def test_bullets_and_numbering_are_stripped():
    scope = Scope(in_scope=["- Amazon Bedrock triage", "* Amazon S3 store", "1. AWS Lambda glue", "  ", ""])
    assert scope.in_scope == ["Amazon Bedrock triage", "Amazon S3 store", "AWS Lambda glue"]


def test_a_markdown_block_is_accepted_as_well_as_a_list():
    scope = Scope(in_scope="- Amazon Bedrock triage\n- Amazon S3 store\n\n")
    assert scope.in_scope == ["Amazon Bedrock triage", "Amazon S3 store"]


def test_empty_scope_is_falsy_so_callers_can_skip_it():
    assert not Scope()
    assert not Scope(in_scope=[], out_of_scope=[])
    assert Scope(in_scope=["something"])


def test_parse_scope_accepts_both_shapes_and_returns_none_for_nothing():
    assert parse_scope({}) is None
    assert parse_scope({"scope": {}}) is None
    assert parse_scope({"scope": {"in_scope": ["a thing"]}}).in_scope == ["a thing"]
    flat = parse_scope({"out_of_scope": ["Amazon Kinesis"], "scope_source": "sow:OPP-1"})
    assert flat.out_of_scope == ["Amazon Kinesis"] and flat.source == "sow:OPP-1"


def test_prompt_block_names_both_lists():
    block = Scope(in_scope=["Claims triage"], out_of_scope=["Data migration"], source="sow:OPP-1").prompt_block()
    assert "<scope>" in block and "</scope>" in block
    assert "## In scope" in block and "- Claims triage" in block
    assert "## Out of scope" in block and "- Data migration" in block
    assert "sow:OPP-1" in block


def test_prompt_block_is_explicit_when_a_list_is_empty():
    block = Scope(in_scope=["Only this"]).prompt_block()
    assert "- (nothing excluded)" in block


def _design(*components) -> Design:
    return Design.model_validate({"components": [
        {"id": f"C-{i:02d}", **c} for i, c in enumerate(components, start=1)]})


def test_out_of_scope_component_is_reported():
    design = _design(
        {"name": "Claims Triage Agent", "service": "Amazon Bedrock", "role": "Classifies claims"},
        {"name": "Event Stream", "service": "Amazon Kinesis Data Streams", "role": "Streams events"},
    )
    scope = Scope(in_scope=["Amazon Bedrock claims triage"], out_of_scope=["Amazon Kinesis streaming"])
    found = violations(design, scope)
    assert len(found) == 1
    assert "C-02" in found[0] and "Amazon Kinesis" in found[0] and "out of scope" in found[0]


def test_in_scope_only_design_is_clean():
    design = _design({"name": "Claims Triage Agent", "service": "Amazon Bedrock", "role": "Classifies claims"})
    scope = Scope(in_scope=["Amazon Bedrock claims triage"], out_of_scope=["Amazon Kinesis streaming"])
    assert violations(design, scope) == []


def test_no_scope_or_no_exclusions_means_no_checking():
    design = _design({"name": "Anything", "service": "Amazon Kinesis Data Streams"})
    assert violations(design, None) == []
    assert violations(design, Scope(in_scope=["whatever"])) == []


def test_match_needs_two_identifying_tokens_so_serverless_alone_does_not_fire():
    """'Amazon OpenSearch Serverless' must not match a component merely described as serverless."""
    design = _design({"name": "Order API", "service": "AWS Lambda", "role": "Serverless request handler"})
    scope = Scope(out_of_scope=["Amazon OpenSearch Serverless vector store"])
    assert violations(design, scope) == []

    real = _design({"name": "Vector Store", "service": "Amazon OpenSearch Serverless"})
    assert len(violations(real, scope)) == 1


def test_one_warning_per_component_even_with_several_matching_exclusions():
    design = _design({"name": "Event Stream", "service": "Amazon Kinesis Data Streams"})
    scope = Scope(out_of_scope=["Amazon Kinesis streaming", "Kinesis Data Streams ingestion"])
    assert len(violations(design, scope)) == 1


def test_design_rule_outranks_the_srs_explicitly():
    rule = Scope(in_scope=["a"], out_of_scope=["b"]).design_rule()
    assert "outranks the SRS" in rule and "open question" in rule


def test_a_long_exclusion_sentence_is_not_matched_on_two_incidental_words():
    """CVA exclusions are sentences; 'configuration' + 'migration' appearing in a component is not a breach."""
    design = _design({"name": "Landing Zone", "service": "AWS Control Tower", "role": "Account configuration",
                      "purpose": "Baseline configuration for the migration landing zone"})
    scope = Scope(out_of_scope=[
        "Implementation, configuration, or modification of AWS services (no build, deployment, or hands-on engineering).",
        "Migration delivery or modernisation activities (no workload moves, refactoring, re-platforming, or PoCs)."])
    assert violations(design, scope) == []


def test_a_short_exclusion_still_matches_with_two_words():
    design = _design({"name": "Event Stream", "service": "Amazon Kinesis Data Streams"})
    assert len(violations(design, Scope(out_of_scope=["Amazon Kinesis streaming"]))) == 1


def test_an_external_system_named_in_an_exclusion_is_not_a_breach():
    """Found on the first real run: 'Modifications to Guidewire ClaimCenter' is not a Guidewire component."""
    design = _design(
        {"name": "Guidewire ClaimCenter", "service": "Guidewire ClaimCenter", "category": "external",
         "role": "Claims system of record"},
        {"name": "FNOL Lambda Functions", "service": "AWS Lambda", "category": "compute",
         "role": "Creates draft claims in Guidewire ClaimCenter", "purpose": "Calls the Guidewire Cloud API"})
    scope = Scope(out_of_scope=["Modifications to Guidewire ClaimCenter beyond draft claim creation through the Cloud API"])
    assert violations(design, scope) == []


def test_role_text_alone_never_triggers_a_warning():
    design = _design({"name": "Order API", "service": "AWS Lambda", "category": "compute",
                      "role": "Reads from Amazon Kinesis streaming", "purpose": "Consumes Kinesis streams"})
    assert violations(design, Scope(out_of_scope=["Amazon Kinesis streaming"])) == []
