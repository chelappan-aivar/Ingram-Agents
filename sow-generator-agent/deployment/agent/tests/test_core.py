"""The local replacements for app.core / app.integrations.aws."""

from sow_generator.agents.factory import ROLE_EDITOR, ROLE_GENERATION, ROLE_VERIFICATION, temperature_policy
from sow_generator.core.bedrock import BEDROCK_MAX_IMAGE_BYTES, TemperaturePolicy, bedrock_model_kwargs
from sow_generator.core.config import DEFAULT_MODEL, SowSettings
from sow_generator.core.session import build_boto_session


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("DATA_BUCKET", "bucket-x")
    monkeypatch.setenv("SOW_VERIFICATION_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    monkeypatch.setenv("SOW_EDITOR_TEMPERATURE", "0.5")
    monkeypatch.setenv("SOW_BEDROCK_NO_TEMPERATURE_MODEL_PATTERNS", "opus-4-1, gpt-oss")
    s = SowSettings.from_env()
    assert s.aws_region == "us-west-2" and s.storage_enabled and s.sow_s3_bucket == "bucket-x"
    assert s.sow_generation_model == DEFAULT_MODEL
    assert s.verification_model.startswith("us.anthropic.claude-haiku")
    assert s.sow_s3_root_prefix == "sow" and s.sow_s3_misc_folder == "misc"
    assert s.sow_bedrock_no_temperature_model_patterns == ("opus-4-1", "gpt-oss")
    assert s.company.name == "Ingram Micro"
    policy = temperature_policy(s)
    assert policy.temperature_for(ROLE_EDITOR, DEFAULT_MODEL, 0.2) == 0.5
    assert policy.temperature_for(ROLE_GENERATION, DEFAULT_MODEL, 0.2) == 0.2
    assert policy.temperature_for(ROLE_VERIFICATION, "us.anthropic.claude-opus-4-1-20250805-v1:0", 0.0) is None


def test_storage_disabled_without_bucket(monkeypatch):
    monkeypatch.delenv("DATA_BUCKET", raising=False)
    assert not SowSettings.from_env().storage_enabled


def test_bedrock_kwargs():
    kwargs = bedrock_model_kwargs(TemperaturePolicy(), role="X", model_id="m", default_temperature=0.3, max_tokens=5)
    assert kwargs == {"model_id": "m", "max_tokens": 5, "temperature": 0.3}
    off = bedrock_model_kwargs(TemperaturePolicy(send=False), role="X", model_id="m", default_temperature=0.3)
    assert "temperature" not in off
    assert BEDROCK_MAX_IMAGE_BYTES == 3932160


def test_session_profile_only_when_set():
    session = build_boto_session(region="eu-west-1")
    assert session.region_name == "eu-west-1"
