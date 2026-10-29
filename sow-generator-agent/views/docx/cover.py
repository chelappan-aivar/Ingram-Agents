"""The cover page: logo, title block and the Important Notice."""

import io

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from app.core.config import CompanyProfile
from app.modules.sow_generator.repositories.asset_repository import load_company_logo
from app.modules.sow_generator.views.docx.styles import HEADING_COLOR, TEXT_COLOR

IMPORTANT_NOTICE_TEXT = (
    "The enclosed material is proprietary. This material is presented for the "
    "purpose of evaluating services and may not be disclosed in any manner to "
    "anyone other than the addressee and employees or authorized representatives "
    "of Customer named here within."
)


def add_cover_logo(document: Document, raw: bytes | None) -> None:
    """Center the company logo at the top of the cover page when available."""
    if raw is None:
        return
    try:
        document.add_picture(io.BytesIO(raw), width=Inches(5.25))
    except Exception:
        return
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph()


def add_cover_page(
    document: Document, customer_name: str, doc_title: str, company: CompanyProfile
) -> None:
    """Add the title/for/customer cover block plus the Important Notice."""
    logo = load_company_logo(company.logo_path)
    has_logo = logo is not None
    add_cover_logo(document, logo)
    for _ in range(2 if has_logo else 3):
        document.add_paragraph()

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(doc_title)
    run.bold = True
    run.font.size = Pt(34)
    run.font.color.rgb = TEXT_COLOR

    for_line = document.add_paragraph()
    for_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = for_line.add_run("for")
    run.font.size = Pt(22)
    run.font.color.rgb = TEXT_COLOR

    customer = document.add_paragraph()
    customer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = customer.add_run(customer_name or "Customer")
    run.bold = True
    run.font.size = Pt(34)
    run.font.color.rgb = HEADING_COLOR

    for _ in range(8):
        document.add_paragraph()

    notice_heading = document.add_paragraph()
    run = notice_heading.add_run("Important Notice:")
    run.italic = True
    run.font.size = Pt(11)
    run.font.color.rgb = TEXT_COLOR

    notice_body = document.add_paragraph()
    notice_body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    run = notice_body.add_run(
        IMPORTANT_NOTICE_TEXT.replace("proprietary.", f"proprietary to {company.name}.")
    )
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = TEXT_COLOR

    document.add_page_break()
