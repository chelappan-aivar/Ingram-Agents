"""The availability model: one policy drives the prompt, usage.json and the reports."""
import json

import pytest

from adr_generator.availability import (DEFAULT_ZONE_COUNT, MAX_ZONE_COUNT, AvailabilityPolicy,
                                        clamp_zone_count)
from adr_generator.config import Settings
from adr_generator.contracts import Design


def test_default_is_two_zones():
    """Two AZs is the Well-Architected Reliability baseline; a single AZ must be opted into."""
    assert DEFAULT_ZONE_COUNT == 2
    assert AvailabilityPolicy().zone_count == 2
    assert AvailabilityPolicy().multi_az is True


@pytest.mark.parametrize("given,expected", [
    (1, 1), (2, 2), (6, 6),
    (0, 1), (-5, 1), (99, MAX_ZONE_COUNT),          # clamped, never rejected
    ("3", 3), (None, DEFAULT_ZONE_COUNT), ("x", DEFAULT_ZONE_COUNT), ([], DEFAULT_ZONE_COUNT),
])
def test_zone_count_is_clamped_not_trusted(given, expected):
    assert clamp_zone_count(given) == expected
    assert AvailabilityPolicy.from_zone_count(given).zone_count == expected


def test_zone_names_follow_the_region():
    assert AvailabilityPolicy(2).zone_names("us-east-1") == ["us-east-1a", "us-east-1b"]
    assert AvailabilityPolicy(3).zone_names("eu-west-1") == ["eu-west-1a", "eu-west-1b", "eu-west-1c"]
    assert AvailabilityPolicy(2).zone_names("") == []


def test_usage_fields_are_what_the_calculator_sees():
    assert AvailabilityPolicy(1).usage_fields() == {"multi_az": False, "availability_zones": 1}
    assert AvailabilityPolicy(2).usage_fields() == {"multi_az": True, "availability_zones": 2}


def test_from_settings_reads_the_env_override():
    assert AvailabilityPolicy.from_settings(Settings(_env_file=None)).zone_count == 2
    assert AvailabilityPolicy.from_settings(Settings(_env_file=None, AVAILABILITY_ZONES=1)).zone_count == 1
    assert AvailabilityPolicy.from_settings(Settings(_env_file=None, AVAILABILITY_ZONES=3)).zone_count == 3


def test_from_design_prefers_az_boundaries():
    """Usage must follow the design that was drawn, not the configured default."""
    design = Design.model_validate({
        "region": "us-east-1",
        "availability_zones": ["us-east-1a", "us-east-1b"],
        "boundaries": [{"id": "az_a", "type": "az", "name": "us-east-1a"},
                       {"id": "az_b", "type": "az", "name": "us-east-1b"},
                       {"id": "vpc", "type": "vpc", "name": "Main VPC"}],
    })
    assert AvailabilityPolicy.from_design(design).zone_count == 2

    single = Design.model_validate({"boundaries": [{"id": "az_a", "type": "az", "name": "us-east-1a"}]})
    assert AvailabilityPolicy.from_design(single).zone_count == 1
    assert AvailabilityPolicy.from_design(single).multi_az is False


def test_from_design_falls_back_to_the_zone_list_then_the_default():
    no_boundaries = Design.model_validate({"availability_zones": ["us-east-1a", "us-east-1b", "us-east-1c"]})
    assert AvailabilityPolicy.from_design(no_boundaries).zone_count == 3
    # a fully serverless design declares neither: fall back to the policy default
    assert AvailabilityPolicy.from_design(Design()).zone_count == DEFAULT_ZONE_COUNT


def test_legacy_scalar_availability_zone_still_loads():
    """Designs saved before the multi-AZ change carry a single string."""
    design = Design.model_validate({"region": "us-east-1", "availability_zone": "us-east-1a"})
    assert design.availability_zones == ["us-east-1a"]
    assert design.availability_zone == "us-east-1a"          # back-compat accessor
    assert AvailabilityPolicy.from_design(design).zone_count == 1


def test_availability_zone_accessor_is_empty_when_unset():
    assert Design().availability_zone == ""
    assert "availability_zone" not in json.loads(Design().model_dump_json())   # not a field any more


def test_rules_differ_by_zone_count_and_never_overclaim():
    single = AvailabilityPolicy(1)
    multi = AvailabilityPolicy(2)
    assert "deliberate exception" in single.design_rule()
    assert "Multi-AZ" in multi.design_rule() and "more expensive" in multi.design_rule()
    # the single-AZ rule must forbid the resiliency claim Sparsh objected to
    assert "not describe the solution as highly available" in single.design_rule()
    assert "cross-AZ data transfer" in multi.usage_rule().lower() or "cross-AZ" in multi.usage_rule()
    assert "set by code" in single.usage_rule().lower()


def test_report_wording():
    assert AvailabilityPolicy(2).phrase() == "two Availability Zones"
    assert AvailabilityPolicy(1).phrase() == "one Availability Zone"
    summary = AvailabilityPolicy(2).deployment_summary("us-east-1", ["us-east-1a", "us-east-1b"])
    assert summary == "One Region (us-east-1), two Availability Zones (us-east-1a, us-east-1b)"
    assert "not specified" in AvailabilityPolicy(2).deployment_summary("us-east-1", [])
    assert "availability_zones is 2" in AvailabilityPolicy(2).report_method_line()
