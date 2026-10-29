"""Builds the full branded SOW document: cover, contents, body and acceptance page."""

import io
import re

from docx import Document
from docx.shared import Pt

from sow_generator.core.config import CompanyProfile
from sow_generator.schemas import ArchImage
from sow_generator.views.docx.body import add_body
from sow_generator.views.docx.chrome import (
    add_acceptance_page,
    add_footer_page_numbers,
    add_header,
    add_toc,
)
from sow_generator.views.docx.cover import add_cover_page
from sow_generator.views.docx.images import ImageLibrary
from sow_generator.views.docx.styles import HEADING_COLOR


def style_headings(document) -> None:
    """Heading styles in the brand navy, with room above and below."""
    sizes = {1: 18, 2: 15, 3: 13, 4: 12}
    for level, size in sizes.items():
        style = document.styles[f"Heading {level}"]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = HEADING_COLOR
        style.paragraph_format.space_before = Pt(18 if level == 1 else 12)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True


def document_headings(document) -> list[tuple[int, str]]:
    """(level, text) for every Heading 1-3 paragraph, in order: what the contents page lists."""
    found = []
    for para in document.paragraphs:
        match = re.fullmatch(r"Heading ([1-3])", para.style.name or "")
        if match and para.text.strip():
            found.append((int(match.group(1)), para.text.strip()))
    return found


def build_sow_docx(
    customer_name: str,
    sow_markdown: str,
    doc_title: str = "Statement of Work",
    architecture_images: list[ArchImage] | None = None,
    *,
    company: CompanyProfile,
) -> bytes:
    """Build the full branded SOW document and return .docx bytes."""
    document = Document()
    add_header(document, customer_name, company)
    add_footer_page_numbers(document)
    add_cover_page(document, customer_name, doc_title, company)
    style_headings(document)
    # The contents page is written last, from the headings that really ended up in the document
    # (including "Architecture Diagrams" and the acceptance page), and slotted in before this marker.
    toc_marker = document.add_page_break()
    add_body(document, sow_markdown, ImageLibrary(architecture_images))
    add_acceptance_page(document, customer_name, company)
    add_toc(document, toc_marker, document_headings(document))

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
