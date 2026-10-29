"""ADR Generator's LLM entry point. The implementation lives in architecture_agent/llm.py (Strands Agents SDK)."""
from __future__ import annotations

from architecture_agent.llm import (CALL_FAILED, NO_TOOL_CALL, TRUNCATED, FakeLLM, FallbackClient, LLMClient,  # noqa: F401
                                    LLMError, StrandsClient, assistant_msg, user_msg)
from adr_generator.config import Settings


def _client(settings: Settings, model_id: str) -> StrandsClient:
    return StrandsClient(model_id, settings.aws_region, settings.aws_profile, settings.temperature_for(model_id),
                         settings.forced_tool_supported(model_id))


def make_llm(settings: Settings) -> LLMClient:
    """settings.model_id, wrapped so a call failure falls back to settings.fallback_model_id (if different)."""
    primary = _client(settings, settings.model_id)
    fb = settings.fallback_model_id
    fallback = _client(settings, fb) if fb and fb != primary.model_id else None
    return FallbackClient(primary, fallback)
