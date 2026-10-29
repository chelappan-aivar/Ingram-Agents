"""Header, footer, table of contents and the acceptance page."""

import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from sow_generator.core.config import CompanyProfile
from sow_generator.views.docx.styles import HEADING_COLOR


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


TOC_FIELD = 'TOC \\o "1-3" \\h \\z \\u'


def _field_run(paragraph, kind: str, instruction: str | None = None) -> None:
    """Append a run holding one field character (begin / separate / end), or the field instruction."""
    run = paragraph.add_run()
    if instruction is not None:
        element = run._r.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
        element.text = instruction
    else:
        element = run._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): kind})
    run._r.append(element)


def add_toc(document: Document, before, headings: list[tuple[int, str]]) -> None:
    """Insert the table of contents in front of `before` (a paragraph).

    The entries are written out, so the contents list is visible the moment the file opens, in any
    viewer. They sit inside a real Word TOC field, and `updateFields` asks Word to refresh the field
    on open, which adds the page numbers (a docx written without Word cannot know them).
    """
    text_width = int(document.sections[0].page_width - document.sections[0].left_margin
                     - document.sections[0].right_margin)
    created = []

    def new_paragraph():
        para = document.add_paragraph()
        created.append(para)
        return para

    title = new_paragraph()
    run = title.add_run("Contents")
    run.bold = True
    run.font.size = Pt(18)
    run.font.color.rgb = HEADING_COLOR
    title.paragraph_format.space_after = Pt(10)

    entries = headings or [(1, "")]
    for index, (level, text) in enumerate(entries):
        para = new_paragraph()
        fmt = para.paragraph_format
        fmt.left_indent = Inches(0.25 * (level - 1))
        fmt.space_after = Pt(3)
        fmt.tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        if index == 0:
            _field_run(para, "begin")
            _field_run(para, "", TOC_FIELD)
            _field_run(para, "separate")
        run = para.add_run(text)
        run.bold = level == 1
        run.font.size = Pt(11 if level == 1 else 10)
        if index == len(entries) - 1:
            _field_run(para, "end")

    for para in created:
        before._p.addprevious(para._p)

    settings = document.settings.element
    if settings.find(qn("w:updateFields")) is None:
        flag = settings.makeelement(qn("w:updateFields"), {qn("w:val"): "true"})
        anchor = next((settings.find(qn(tag)) for tag in
                       ("w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat", "w:docVars",
                        "w:rsids", "m:mathPr", "w:themeFontLang", "w:clrSchemeMapping")
                       if settings.find(qn(tag)) is not None), None)
        if anchor is not None:
            anchor.addprevious(flag)
        else:
            settings.append(flag)


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
