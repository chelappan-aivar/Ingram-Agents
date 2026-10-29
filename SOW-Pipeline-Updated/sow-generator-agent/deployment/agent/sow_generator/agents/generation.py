"""The SOW generation agent: streams markdown chunks followed by a usage report."""

import logging
from collections.abc import AsyncIterator
from typing import cast

from strands.types.content import ContentBlock

from sow_generator.core.config import SowSettings
from sow_generator.agents.factory import ROLE_GENERATION, build_agent
from sow_generator.agents.multimodal import build_user_message
from sow_generator.agents.prompt_builder import build_full_system_prompt
from sow_generator.models import (
    GenerationEvent,
    GenerationInputs,
    TextChunk,
    UsageReport,
)
from sow_generator.repositories.template_repository import load_template

logger = logging.getLogger(__name__)

_MAX_TOKENS = 12000
_DEFAULT_TEMPERATURE = 0.2


def _architecture_instruction(image_count: int, filenames: list[str]) -> str:
    """How the model should reference the attached architecture images in the draft."""
    if image_count > 1 and filenames:
        names = ", ".join(f'"{name}"' for name in filenames)
        return (
            f" {image_count} architecture images are attached ({names}). "
            "Under the Architecture section, create one ### subsection per image using a "
            "descriptive name derived from the filename and what you see in the image, in the "
            "same order as the images. Immediately under each subsection, on its own line, emit "
            "the image reference `![<descriptive alt>](arch:N)` where N is the 1-based image "
            "number in that same order."
        )
    if image_count == 1:
        return (
            " One architecture image is attached. Where the sample shows an architecture/diagram "
            "placeholder, emit the image reference `![Architecture Diagram](arch:1)` on its own "
            "line."
        )
    return ""


async def stream_sow_generation(
    settings: SowSettings, inputs: GenerationInputs, *, model_id: str | None = None
) -> AsyncIterator[GenerationEvent]:
    """Yield the SOW markdown as chunks, then one usage report."""
    template = load_template(inputs.template_id)
    system_prompt = build_full_system_prompt(
        client_name=inputs.customer_name,
        category=template.category,
        sample=template.sample,
        project_details=inputs.project_details,
        transcripts=inputs.transcripts,
        clarification_answers=inputs.clarification_answers,
        company=settings.company,
        upstream=inputs.upstream,
    )

    architecture_count = (1 if inputs.architecture_image_b64 else 0) + len(
        inputs.extra_arch_image_b64s
    )
    arch_instruction = _architecture_instruction(architecture_count, inputs.arch_image_filenames)
    user_message = (
        f"Produce the SOW body now. First line MUST be `# About {settings.company.name}`. "
        "Do NOT emit a cover page, Important Notice, Table of Contents, or "
        "Acceptance & Authorization section — the host renders those. Match the "
        f"sample's structure, length, and static content faithfully.{arch_instruction}"
    )
    extra_images = [*inputs.extra_arch_image_b64s, *inputs.project_detail_image_b64s]
    message = build_user_message(user_message, inputs.architecture_image_b64, extra_images or None)

    agent = build_agent(
        settings,
        role=ROLE_GENERATION,
        model_id=model_id or settings.sow_generation_model,
        default_temperature=_DEFAULT_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        system_prompt=system_prompt,
    )

    started = False
    accumulated = ""
    input_tokens = 0
    output_tokens = 0
    try:
        async for event in agent.stream_async(cast("str | list[ContentBlock]", message)):
            if "message" in event:
                msg = event["message"]
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    usage = (msg.get("metadata") or {}).get("usage") or {}
                    input_tokens += usage.get("inputTokens") or 0
                    output_tokens += usage.get("outputTokens") or 0
            if "data" not in event:
                continue
            text = event["data"]
            if not text:
                continue

            if started:
                yield TextChunk(text)
                continue
            # Drop any preamble the model emits before the first heading.
            accumulated += text
            if "#" in accumulated:
                started = True
                accumulated = accumulated[accumulated.find("#") :]
                yield TextChunk(accumulated)
    except Exception:
        # Bedrock/Strands can raise at stream close even when the content was fully delivered,
        # so only a failure before any content was streamed is re-raised.
        if not started:
            raise
        logger.warning("generation stream raised after content was delivered", exc_info=True)

    yield UsageReport(input_tokens=input_tokens, output_tokens=output_tokens)
