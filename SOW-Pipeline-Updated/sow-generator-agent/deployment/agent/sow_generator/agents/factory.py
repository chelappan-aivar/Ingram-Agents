"""Builds Strands agents on Bedrock; the single place tests replace to avoid real model calls."""

from typing import Any, cast

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.types.content import SystemContentBlock

from sow_generator.core.config import SowSettings
from sow_generator.core.bedrock import (
    BEDROCK_BOTO_CONFIG,
    RoleTemperature,
    TemperaturePolicy,
    bedrock_model_kwargs,
)
from sow_generator.core.session import build_boto_session

ROLE_GENERATION = "GENERATION"
ROLE_VERIFICATION = "VERIFICATION"
ROLE_EDITOR = "EDITOR"


def temperature_policy(settings: SowSettings) -> TemperaturePolicy:
    """The Bedrock temperature rules configured through the environment."""
    return TemperaturePolicy(
        send=settings.sow_bedrock_send_temperature,
        value=settings.sow_bedrock_temperature,
        no_temperature_patterns=settings.sow_bedrock_no_temperature_model_patterns,
        roles={
            ROLE_GENERATION: RoleTemperature(
                settings.sow_generation_send_temperature, settings.sow_generation_temperature
            ),
            ROLE_VERIFICATION: RoleTemperature(
                settings.sow_verification_send_temperature, settings.sow_verification_temperature
            ),
            ROLE_EDITOR: RoleTemperature(
                settings.sow_editor_send_temperature, settings.sow_editor_temperature
            ),
        },
    )


def build_agent(
    settings: SowSettings,
    *,
    role: str,
    model_id: str,
    default_temperature: float,
    max_tokens: int,
    system_prompt: str | list[dict[str, Any]],
    tools: list[Any] | None = None,
    cache_tools: str | None = None,
) -> Agent:
    """A streaming Bedrock agent for one role (generation, verification or editor)."""
    model_options: dict[str, Any] = {
        "streaming": True,
        "max_tokens": max_tokens,
        "boto_client_config": BEDROCK_BOTO_CONFIG,
        # The session carries the region and profile; Strands rejects region_name alongside it.
        "boto_session": build_boto_session(
            region=settings.aws_region, profile=settings.aws_profile
        ),
    }
    if cache_tools is not None:
        model_options["cache_tools"] = cache_tools
    model = BedrockModel(
        **bedrock_model_kwargs(
            temperature_policy(settings),
            role=role,
            model_id=model_id,
            default_temperature=default_temperature,
            **model_options,
        )
    )
    agent_options: dict[str, Any] = {}
    if tools is not None:
        agent_options["tools"] = tools
    prompt = cast("str | list[SystemContentBlock]", system_prompt)
    return Agent(model=model, system_prompt=prompt, callback_handler=None, **agent_options)
