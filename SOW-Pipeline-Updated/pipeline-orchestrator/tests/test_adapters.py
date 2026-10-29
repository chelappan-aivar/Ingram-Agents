"""The translations between the three agents. A quiet mismatch here becomes a wrong SOW."""
import copy

import pytest
from fakes import ARCHITECTURE_RESULT, COST_RESULT, DESIGN, SOW_MARKDOWN, UPSTREAM, USAGE_JSON

from orchestrator import adapters


# ----------------------------------------------------------------- SOW -> architecture
def test_the_sow_is_passed_as_the_srs_with_the_scope_alongside():
    scope = {"in_scope": ["Amazon Bedrock triage"], "out_of_scope": ["Amazon Kinesis"], "source": "sow:OPP-1"}
    payload = adapters.architecture_request(SOW_MARKDOWN, scope)
    assert payload["srs_text"] == SOW_MARKDOWN
    assert payload["srs_filename"] == "sow.md"
    assert payload["scope"] == {"in_scope": ["Amazon Bedrock triage"],
                                "out_of_scope": ["Amazon Kinesis"], "source": "sow:OPP-1"}


def test_an_empty_scope_is_omitted_rather_than_sent_empty():
    """An empty scope block would read as 'nothing is in scope' to the design prompt."""
    for scope in (None, {}, {"in_scope": [], "out_of_scope": []}):
        assert "scope" not in adapters.architecture_request(SOW_MARKDOWN, scope)


# ----------------------------------------------------------------- architecture -> cost
def test_cost_requirements_are_built_from_usage_figures():
    text = adapters.cost_requirements(ARCHITECTURE_RESULT)
    assert "Claims FNOL Assistant" in text
    assert "Region: us-east-2" in text
    assert "Amazon Bedrock" in text and "invocations_per_month 18,000" in text
    assert "Amazon RDS" in text and "multi_az yes" in text
    assert adapters.COST_MIN_CHARS <= len(text) <= adapters.COST_MAX_CHARS


def test_cost_requirements_state_the_availability_model_so_per_zone_resources_are_priced():
    text = adapters.cost_requirements(ARCHITECTURE_RESULT)
    assert "2 Availability Zones (us-east-2a, us-east-2b)" in text
    assert "once per zone" in text and "cross-AZ data transfer" in text


def test_a_single_zone_design_says_no_standby():
    single = copy.deepcopy(ARCHITECTURE_RESULT)
    single["adr"]["design"]["availability_zones"] = ["us-east-2a"]
    text = adapters.cost_requirements(single)
    assert "1 Availability Zone (us-east-2a)" in text
    assert "no standby instances" in text


def test_cost_requirements_fall_back_to_components_when_usage_is_missing():
    no_usage = copy.deepcopy(ARCHITECTURE_RESULT)
    no_usage.pop("usage")
    text = adapters.cost_requirements(no_usage)
    assert "Amazon Bedrock" in text and "db.t4g.medium" in text


def test_cost_requirements_refuse_to_price_an_empty_architecture():
    """Sending a vague description would return a confident, wrong number."""
    with pytest.raises(ValueError):
        adapters.cost_requirements({})
    with pytest.raises(ValueError):
        adapters.cost_requirements({"adr": {"design": {"title": "x", "components": []}}})


def test_a_legacy_scalar_availability_zone_is_still_understood():
    legacy = copy.deepcopy(ARCHITECTURE_RESULT)
    design = legacy["adr"]["design"]
    design.pop("availability_zones")
    design["availability_zone"] = "us-east-2a"
    assert "1 Availability Zone (us-east-2a)" in adapters.cost_requirements(legacy)


def test_the_design_is_found_whichever_action_produced_it():
    """run_pipeline nests the design under 'adr'; generate_adr returns it at the top level."""
    flat = {"design": DESIGN, "usage_json": USAGE_JSON}
    assert "Claims FNOL Assistant" in adapters.cost_requirements(flat)


# ----------------------------------------------------------------- -> UpstreamInputs
def test_architecture_block_lists_services_without_actors():
    block = adapters.architecture_block(ARCHITECTURE_RESULT)
    names = [s["name"] for s in block["services"]]
    assert names == ["Amazon Bedrock", "Amazon S3", "Amazon RDS"]
    assert "Actor" not in names          # an actor is not a service to cover in scope
    assert all(s["purpose"] for s in block["services"])


def test_architecture_block_carries_nfrs_constraints_and_the_availability_model():
    block = adapters.architecture_block(ARCHITECTURE_RESULT)
    assert "p95 latency under 800 ms." in block["nfrs"]
    assert "Data stays in us-east-2." in block["nfrs"]          # constraints count as NFRs
    assert "Triage 18,000 FNOLs per month." not in block["nfrs"]  # functional, not an NFR
    assert block["assumptions"][0] == "Deployed across 2 Availability Zones: us-east-2a, us-east-2b."


def test_architecture_block_carries_scope_warnings_into_the_review():
    with_warning = copy.deepcopy(ARCHITECTURE_RESULT)
    with_warning["scope_warnings"] = ["C-05 Event Stream (Amazon Kinesis) appears to be out of scope."]
    block = adapters.architecture_block(with_warning)
    assert any("out of scope" in a for a in block["assumptions"])


def test_architecture_block_carries_the_diagram_image():
    block = adapters.architecture_block(ARCHITECTURE_RESULT)
    assert block["diagram_images"][0]["filename"] == "architecture.png"
    assert block["diagram_images"][0]["content_b64"]


def test_the_calculator_url_becomes_the_sow_source_link():
    """This is the wiring that was missing: the SOW refers to this link instead of a cost table."""
    block = adapters.cost_block(COST_RESULT)
    assert block["source_link"] == "https://calculator.aws/#/estimate?id=abc123"


def test_cost_block_flattens_per_service_monthly_costs():
    block = adapters.cost_block(COST_RESULT)
    assert {"service": "Amazon Bedrock", "monthly": 4200.0} in block["line_items"]
    assert len(block["line_items"]) == 3
    assert block["currency"] == "USD" and block["region"] == "us-east-2"


def test_annual_total_is_derived_when_the_calculator_gives_only_monthly():
    block = adapters.cost_block(COST_RESULT)
    assert block["monthly_total"] == 8500.0
    assert block["annual_total"] == 102000.0


def test_cost_block_survives_a_sparse_estimate():
    block = adapters.cost_block({"calculator_url": "https://calculator.aws/#/estimate?id=z"})
    assert block["source_link"].endswith("id=z")
    assert block["line_items"] == [] and "monthly_total" not in block


def test_upstream_is_merged_not_mutated():
    original = copy.deepcopy(UPSTREAM)
    merged = adapters.upstream_with_results(UPSTREAM, ARCHITECTURE_RESULT, COST_RESULT)
    assert UPSTREAM == original                      # caller's copy untouched
    assert merged["intake"]["customer_name"] == "Fabrikam Insurance"
    assert merged["architecture"]["services"][0]["name"] == "Amazon Bedrock"
    assert merged["cost"]["source_link"].startswith("https://calculator.aws/")


def test_merging_skips_blocks_with_nothing_in_them():
    merged = adapters.upstream_with_results(UPSTREAM, {}, {})
    assert "architecture" not in merged and "cost" not in merged


def test_the_merged_upstream_validates_against_the_sow_contract():
    """The real test of these adapters: the SOW agent must accept what we produce."""
    import sys
    from pathlib import Path

    sow_agent = Path(__file__).resolve().parents[2] / "sow-generator-agent" / "deployment" / "agent"
    if not sow_agent.is_dir():
        import pytest as _pytest
        _pytest.skip("sow-generator-agent is not present in this checkout")
    sys.path.insert(0, str(sow_agent))
    from sow_generator.schemas import UpstreamInputs      # noqa: PLC0415

    merged = adapters.upstream_with_results(UPSTREAM, ARCHITECTURE_RESULT, COST_RESULT)
    validated = UpstreamInputs.model_validate(merged)
    assert validated.cost.source_link == "https://calculator.aws/#/estimate?id=abc123"
    assert [s.name for s in validated.architecture.services] == ["Amazon Bedrock", "Amazon S3", "Amazon RDS"]
    assert validated.architecture.nfrs


# ----------------------------------------------------------------- cost link -> SOW
URL = "https://calculator.aws/#/estimate?id=real123"


def test_the_placeholder_is_replaced_with_the_real_link():
    out, how = adapters.apply_cost_link(SOW_MARKDOWN, URL)
    assert how == "placeholder" and URL in out and "[cost calculator link]" not in out


def test_a_stale_sample_link_is_replaced_not_duplicated():
    md = ("# Pricing\nOne-time: USD 1.\nAWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample-x\n"
          "# Schedule\nWeeks.\n")
    out, how = adapters.apply_cost_link(md, URL)
    assert how == "replaced" and URL in out and "sample-x" not in out
    assert out.count("calculator.aws") == 1


def test_a_bare_stale_url_in_the_pricing_section_is_replaced():
    md = "# Pricing\nSee https://calculator.aws/#/estimate?id=old for details.\n# Next\nx\n"
    out, how = adapters.apply_cost_link(md, URL)
    assert how == "replaced" and "id=old" not in out and URL in out


def test_a_stale_url_outside_the_pricing_section_is_left_alone():
    md = "# Notes\nhttps://calculator.aws/#/estimate?id=elsewhere\n# Pricing\nOne-time USD 1.\n"
    out, how = adapters.apply_cost_link(md, URL)
    assert how == "inserted" and "id=elsewhere" in out and URL in out


def test_a_pricing_section_without_any_link_gets_one_inserted_inside_it():
    md = "# Pricing\nOne-time fee USD 50,000.\n# Schedule I\nWeeks.\n"
    out, how = adapters.apply_cost_link(md, URL)
    assert how == "inserted"
    pricing = out.split("# Pricing")[1].split("# Schedule I")[0]
    assert URL in pricing and "USD 50,000" in pricing


def test_a_sow_with_no_pricing_section_gets_one_appended():
    out, how = adapters.apply_cost_link("# Scope\nThings.\n", URL)
    assert how == "appended" and out.rstrip().endswith(URL) and "# Pricing" in out


def test_applying_the_same_link_twice_changes_nothing():
    once, _ = adapters.apply_cost_link(SOW_MARKDOWN, URL)
    twice, how = adapters.apply_cost_link(once, URL)
    assert how == "unchanged" and twice == once


def test_no_url_means_no_change():
    assert adapters.apply_cost_link(SOW_MARKDOWN, "") == (SOW_MARKDOWN, "unchanged")


def test_the_link_never_brings_a_figure_with_it():
    out, _ = adapters.apply_cost_link("# Pricing\nOne-time USD 50,000.\n", URL)
    assert "run-rate" in out.lower() and "USD 8" not in out
