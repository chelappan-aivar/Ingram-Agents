"""Builds the full branded SOW document: cover, contents, body and acceptance page."""

import io

from docx import Document

from app.core.config import CompanyProfile
from app.modules.sow_generator.schemas import ArchImage
from app.modules.sow_generator.views.docx.body import add_body
from app.modules.sow_generator.views.docx.chrome import (
    add_acceptance_page,
    add_footer_page_numbers,
    add_header,
    add_toc,
)
from app.modules.sow_generator.views.docx.cover import add_cover_page
from app.modules.sow_generator.views.docx.images import ImageLibrary


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
    add_toc(document)
    add_body(document, sow_markdown, ImageLibrary(architecture_images))
    add_acceptance_page(document, customer_name, company)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
