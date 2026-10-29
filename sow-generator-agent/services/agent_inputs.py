"""Transport-neutral request-to-agent input assembly shared by every controller."""

import logging
import re

from app.modules.sow_generator.errors import SowInputError
from app.modules.sow_generator.models import EditInputs, GenerationInputs
from app.modules.sow_generator.schemas import AgentEditRequest, GenerateRequest, TranscriptFile
from app.modules.sow_generator.services.transcript_extraction import (
    extract_transcript_from_file,
)

logger = logging.getLogger(__name__)

_UNSAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def safe_file_stem(customer_name: str) -> str:
    """Reduce a customer name to a filename-safe stem."""
    return _UNSAFE_FILE_CHARS.sub("_", customer_name) or "SOW"


def collect_transcripts(files: list[TranscriptFile] | None) -> list[str]:
    """Extract readable text from uploaded transcript files."""
    out: list[str] = []
    for f in files or []:
        try:
            extracted = extract_transcript_from_file(f.name, f.content_b64)
        except ValueError as exc:
            # Extraction raises ValueError with a user-safe reason (unsupported or empty file).
            raise SowInputError(f"Failed to extract transcript from {f.name!r}: {exc}") from exc
        except Exception as exc:
            logger.exception("transcript extraction failed")
            raise SowInputError(f"Failed to extract transcript from {f.name!r}.") from exc
        if extracted:
            out.append(extracted)
    return out


def collect_architecture_images(request: GenerateRequest) -> list[str]:
    """Gather architecture images from every input channel, de-duplicated in order."""
    images = ([request.architecture_image_b64] if request.architecture_image_b64 else []) + list(
        request.architecture_image_b64s or []
    )
    if request.upstream and request.upstream.architecture:
        images += [
            img.content_b64
            for img in request.upstream.architecture.diagram_images
            if img.content_b64
        ]
    # The frontend may send the same diagram through more than one channel.
    return list(dict.fromkeys(images))


def build_generation_inputs(request: GenerateRequest) -> GenerationInputs:
    """Map a GenerateRequest onto the inputs of the generation agent."""
    architecture = collect_architecture_images(request)
    return GenerationInputs(
        customer_name=request.customer_name,
        template_id=request.template_id,
        project_details=request.project_details,
        transcripts=collect_transcripts(request.transcript_files),
        clarification_answers=request.clarification_answers,
        architecture_image_b64=architecture[0] if architecture else None,
        project_detail_image_b64s=[
            image.content_b64 for image in request.project_detail_images if image.content_b64
        ],
        extra_arch_image_b64s=architecture[1:],
        arch_image_filenames=request.arch_image_filenames,
        upstream=request.upstream,
    )


def build_edit_inputs(request: AgentEditRequest) -> EditInputs:
    """Map an AgentEditRequest onto the inputs of the editor agent."""
    return EditInputs(
        instruction=request.instruction,
        selected_text=request.selected_text,
        heading_breadcrumb=request.heading_breadcrumb,
        document_outline=request.document_outline,
        sections_map=request.sections_map,
        customer_name=request.customer_name,
        template_id=request.template_id,
        chat_history=[message.model_dump() for message in request.chat_history],
        resolving_issue=request.resolving_issue.model_dump() if request.resolving_issue else None,
    )
