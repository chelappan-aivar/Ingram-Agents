"""Builds Bedrock multimodal user messages from base64 images."""

import base64
import logging
import re
from typing import Any

from sow_generator.core.bedrock import BEDROCK_MAX_IMAGE_BYTES

logger = logging.getLogger(__name__)


def decode_b64(data_or_url: str) -> tuple[str, bytes]:
    media = "image/png"
    payload = data_or_url
    if data_or_url.startswith("data:"):
        header, _, payload = data_or_url.partition(",")
        m = re.match(r"data:([^;]+);base64", header)
        if m:
            media = m.group(1)
    raw = base64.b64decode(payload)
    if len(raw) > BEDROCK_MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image is {len(raw)} bytes; maximum is {BEDROCK_MAX_IMAGE_BYTES} bytes. "
            "Reload the add-in and reattach the image so it can be compressed."
        )
    return media, raw


def build_user_message(
    user_text: str,
    architecture_image_b64: str | None,
    extra_image_b64s: list[str] | None = None,
) -> str | list[dict[str, Any]]:
    """The text alone, or image blocks followed by the text when images are attached."""
    images: list[dict[str, Any]] = []
    if architecture_image_b64:
        media_type, raw = decode_b64(architecture_image_b64)
        fmt = media_type.split("/")[-1] or "png"
        images.append({"image": {"format": fmt, "source": {"bytes": raw}}})
    for b64 in extra_image_b64s or []:
        if not b64:
            continue
        try:
            media_type, raw = decode_b64(b64)
        except ValueError:
            # Supporting images are optional; skip one that is oversized or not valid base64.
            logger.warning("skipping an unusable supporting image")
            continue
        fmt = media_type.split("/")[-1] or "png"
        images.append({"image": {"format": fmt, "source": {"bytes": raw}}})
    if not images:
        return user_text
    return [*images, {"text": user_text}]
