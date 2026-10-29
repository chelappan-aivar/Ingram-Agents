"""Header, footer, table of contents and the acceptance page."""

import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

from app.core.config import CompanyProfile
from app.modules.sow_generator.views.docx.styles import HEADING_COLOR


def header_title(customer_name: str, company: CompanyProfile) -> str:
    """Mirror the Word header title format: Company_Customer_SOW."""
    short = company.short_name
    trimmed = (customer_name or "").strip()
    if not trimmed:
        return f"{short}_SOW"
    slug = re.sub(r"\s+", "_", trimmed)
    return f"{short}_{slug}_SOW"


def add_header(document: Document, customer_name: str, company: CompanyProfile) -> None:
    """Add a right-aligned header title to the primary section header."""
    header = document.sections[0].header
    para = header.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = para.add_run(header_title(customer_name, company))
    run.font.size = Pt(9)
    run.font.color.rgb = HEADING_COLOR


def add_footer_page_numbers(document: Document) -> None:
    """Add a centred auto page-number field to the footer."""
    footer = document.sections[0].footer
    para = footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run()
    fld_begin = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "begin"})
    instr = run._r.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
    instr.text = "PAGE"
    fld_end = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "end"})
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def add_toc(document: Document) -> None:
    """Insert a Word TOC field (levels 1-3); update in Word via right-click → Update Field."""
    heading = document.add_paragraph()
    run = heading.add_run("Contents")
    run.bold = True
    run.font.size = Pt(18)
    run.font.color.rgb = HEADING_COLOR

    para = document.add_paragraph()
    run = para.add_run()
    fld_begin = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "begin"})
    instr = run._r.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "separate"})
    fld_text = run._r.makeelement(qn("w:t"), {})
    fld_text.text = "Right-click to update the Table of Contents."
    fld_end = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "end"})
    for el in (fld_begin, instr, fld_sep, fld_text, fld_end):
        run._r.append(el)

    document.add_page_break()


def add_acceptance_page(document: Document, customer_name: str, company: CompanyProfile) -> None:
    """Add the Acceptance and Authorization heading plus a signature table."""
    document.add_page_break()
    document.add_heading("Acceptance and Authorization", level=1)

    preamble = document.add_paragraph()
    preamble.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    preamble.add_run(
        "IN WITNESS WHEREOF, the parties below have executed this Statement of Work "
        "as of the latest of the signature dates recorded below."
    )

    rows = [
        [f"For {customer_name or 'Customer'}", f"For {company.name}"],
        ["Signature: ____________________", "Signature: ____________________"],
        ["Name:", "Name:"],
        ["Title:", "Title:"],
        ["Date:", "Date:"],
    ]
    table = document.add_table(rows=len(rows), cols=2)
    for r, row in enumerate(rows):
        for c in range(2):
            cell = table.cell(r, c)
            cell.text = ""
            run = cell.paragraphs[0].add_run(row[c])
            run.font.size = Pt(11)
            run.bold = r == 0
