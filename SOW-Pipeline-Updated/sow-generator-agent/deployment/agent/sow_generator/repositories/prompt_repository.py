"""Loads the packaged prompts."""

from functools import cache

from sow_generator.repositories.resource_loader import read_resource_text


@cache
def load_common_system_prompt() -> str:
    """The shared generation system prompt, with its placeholders still in place."""
    return read_resource_text("prompts", "system_prompt.md")


@cache
def load_verification_prompt() -> str:
    return read_resource_text("prompts", "verification.txt").strip()


@cache
def load_editor_prompt() -> str:
    return read_resource_text("prompts", "agent_edit.txt").strip()


@cache
def load_wa_review_prompt() -> str:
    """The Well-Architected review prompt, run against the SOW before the diagram is generated."""
    return read_resource_text("prompts", "wa_review.txt").strip()
