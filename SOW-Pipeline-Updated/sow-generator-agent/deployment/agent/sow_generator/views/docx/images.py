"""Resolving markdown image references to decoded diagrams and placing them."""

import base64
import io
import logging
import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Emu, Inches, Pt

from sow_generator.schemas import ArchImage
from sow_generator.views.docx.styles import TEXT_COLOR

logger = logging.getLogger(__name__)


def decode_image(content_b64: str) -> bytes:
    """Decode a data-URL or raw base64 image into bytes."""
    payload = content_b64.partition(",")[2] if content_b64.startswith("data:") else content_b64
    return base64.b64decode(payload)


class ImageLibrary:
    """Resolves markdown image targets to decoded bytes, tracking what gets used."""

    def __init__(self, images: list[ArchImage] | None) -> None:
        self._ordered: list[bytes] = []
        self._by_name: dict[str, bytes] = {}
        self._used: set[int] = set()
        for img in images or []:
            try:
                raw = decode_image(img.content_b64)
            except ValueError:
                logger.warning("skipping an architecture image that is not valid base64")
                continue
            self._ordered.append(raw)
            if img.filename:
                self._by_name[img.filename.strip()] = raw

    def resolve(self, target: str) -> bytes | None:
        """Resolve an 'arch:N' index or a filename to image bytes."""
        target = target.strip()
        match = re.fullmatch(r"arch:(\d+)", target, flags=re.IGNORECASE)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(self._ordered):
                self._used.add(idx)
                return self._ordered[idx]
            return None
        raw = self._by_name.get(target)
        if raw is not None:
            self._used.add(self._ordered.index(raw))
        return raw

    def unused(self) -> list[bytes]:
        """Return images that were never referenced from the markdown, in order."""
        return [raw for i, raw in enumerate(self._ordered) if i not in self._used]


def add_picture(document: Document, raw: bytes, alt: str) -> bool:
    """Insert a centered, width-constrained picture; return False if it can't render."""
    section = document.sections[0]
    width, left, right = section.page_width, section.left_margin, section.right_margin
    usable = (
        Emu(width - left - right)
        if width is not None and left is not None and right is not None
        else Inches(6.5)
    )
    try:
        document.add_picture(io.BytesIO(raw), width=usable)
    except Exception:
        # An unreadable or unsupported image must not fail the whole export.
        logger.warning("an architecture image could not be placed in the document")
        return False
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    if alt.strip():
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = caption.add_run(alt.strip())
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = TEXT_COLOR
    return True
