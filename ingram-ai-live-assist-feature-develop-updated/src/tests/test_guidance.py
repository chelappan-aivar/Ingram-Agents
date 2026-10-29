from app.guidance import UnknownOpportunityTypeError, list_opportunity_types, load_guidance


def test_list_opportunity_types_returns_specialised_configs_only():
    types = list_opportunity_types()
    assert "cloud_migration" in types
    assert "hardware_refresh" in types
    assert "managed_services" in types
    assert "general" not in types


def test_general_guidance_covers_the_approved_topics():
    from app.guidance import load_general_guidance

    required = load_general_guidance().required_field_ids()
    assert "business_goal" in required
    assert {"desired_outcomes", "scope", "current_environment", "data_volumes", "security_compliance", "target_region", "target_timeline"} <= set(required)


def test_load_guidance_cloud_migration():
    guidance = load_guidance("cloud_migration")
    assert guidance.opportunity_type == "cloud_migration"
    assert "current_environment" in guidance.required_field_ids()
    assert "budget_range" in guidance.all_field_ids()
    assert "budget_range" not in guidance.required_field_ids()  # optional field


def test_load_guidance_unknown_type_raises():
    try:
        load_guidance("not_a_real_type")
        assert False, "expected UnknownOpportunityTypeError"
    except UnknownOpportunityTypeError:
        pass


def test_get_field_returns_none_for_missing_id():
    guidance = load_guidance("cloud_migration")
    assert guidance.get_field("does_not_exist") is None
    assert guidance.get_field("target_timeline") is not None
