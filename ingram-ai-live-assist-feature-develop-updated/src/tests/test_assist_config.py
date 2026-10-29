import pytest

from app.assist_config import AssistConfig, InvalidAssistConfigError


def test_defaults_when_missing():
    assert AssistConfig.from_payload(None) == AssistConfig()
    assert AssistConfig.from_payload({}) == AssistConfig()


def test_valid_config_parses_and_strips_instructions():
    config = AssistConfig.from_payload(
        {"scope": "open", "frequency": "proactive", "max_suggestions": 3, "instructions": "  Be brief.  "}
    )
    assert config == AssistConfig("open", "proactive", 3, "Be brief.")
    assert config.to_dict()["scope"] == "open"


@pytest.mark.parametrize(
    "payload",
    [
        "open",
        {"scope": "anything"},
        {"frequency": "always"},
        {"max_suggestions": 0},
        {"max_suggestions": 4},
        {"max_suggestions": True},
        {"max_suggestions": "2"},
        {"instructions": 5},
        {"instructions": "x" * 2001},
        {"unknown_key": 1},
    ],
)
def test_invalid_config_rejected(payload):
    with pytest.raises(InvalidAssistConfigError):
        AssistConfig.from_payload(payload)
