"""Prompt text for the agent. ``orchestrator.py`` assembles it with the call's state."""

from app.prompts.core import CLOSING, CORE_PROMPT
from app.prompts.suggestions import suggestion_rules

__all__ = ["CLOSING", "CORE_PROMPT", "suggestion_rules"]
