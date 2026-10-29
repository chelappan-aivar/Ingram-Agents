"""Markdown tables and inline formatting (bold, italic, code, links)."""

import re

from docx.document import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor
from docx.text.paragraph import Paragraph

LINK_COLOR = RGBColor(0x05, 0x63, 0xC1)
HEADER_FILL = "1F3A5F"          # same navy as HEADING_COLOR
HEADER_TEXT = RGBColor(0xFF, 0xFF, 0xFF)
BAND_FILL = "F2F5F9"

# One inline token: [text](url), **bold**, `code`, *italic*, or a bare URL.
_INLINE_RE = re.compile(
    r"(\[[^\]]+\]\([^)\s]+\)"
    r"|\*\*[^*]+\*\*"
    r"|`[^`]+`"
    r"|(?<![*\w])\*[^*\s][^*]*\*(?![*\w])"
    r"|https?://[^\s<>\"]+)"
)
_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")
_TRAILING_PUNCT = ".,;:!?)]}'\""

# A paragraph holding a word longer than this is not justified: Word would stretch the gaps on the
# line above it to push the word onto the next line (a long URL does exactly that).
UNBREAKABLE_LIMIT = 30


def parse_table_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell values."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def is_table_separator(line: str) -> bool:
    """Detect a markdown table header separator row (---|---)."""
    return bool(re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", line)) and "-" in line


def visible_text(text: str) -> str:
    """The text a reader sees: markdown markers removed, link targets dropped."""
    text = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", text)
    return re.sub(r"\*\*|`", "", text)


def should_justify(text: str) -> bool:
    """Justify ordinary prose only. URLs and other long unbreakable words leave ugly gaps."""
    if re.search(r"https?://", text):
        return False
    return all(len(word) <= UNBREAKABLE_LIMIT for word in visible_text(text).split())


# ----------------------------------------------------------------------------- inline
def add_hyperlink(paragraph: Paragraph, url: str, text: str, *, size: Pt | None = None) -> None:
    """Append a clickable external link."""
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    link.set(qn("w:history"), "1")
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), str(LINK_COLOR))
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.append(color)
    props.append(underline)
    if size is not None:
        half_points = OxmlElement("w:sz")
        half_points.set(qn("w:val"), str(int(size.pt * 2)))
        props.append(half_points)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def apply_inline(paragraph: Paragraph, text: str, *, size: Pt | None = None) -> None:
    """Render **bold**, *italic*, `code`, [links](url) and bare URLs; everything else as plain runs."""

    def plain(value: str, **flags: bool) -> None:
        run = paragraph.add_run(value)
        if size is not None:
            run.font.size = size
        run.bold = flags.get("bold") or None
        run.italic = flags.get("italic") or None
        if flags.get("code"):
            run.font.name = "Consolas"

    for segment in _INLINE_RE.split(text):
        if not segment:
            continue
        link = _LINK_RE.match(segment)
        if link:
            add_hyperlink(paragraph, link.group(2), link.group(1), size=size)
        elif segment.startswith("**") and segment.endswith("**") and len(segment) > 4:
            plain(segment[2:-2], bold=True)
        elif segment.startswith("`") and segment.endswith("`") and len(segment) > 2:
            plain(segment[1:-1], code=True)
        elif segment.startswith("*") and segment.endswith("*") and len(segment) > 2:
            plain(segment[1:-1], italic=True)
        elif re.match(r"https?://", segment):
            url = segment.rstrip(_TRAILING_PUNCT)
            add_hyperlink(paragraph, url, url, size=size)
            if len(url) < len(segment):
                plain(segment[len(url):])
        else:
            plain(segment)


# ----------------------------------------------------------------------------- tables
def _shade(cell, fill: str) -> None:
    props = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    props.append(shading)


def _row_flag(row, tag: str) -> None:
    props = row._tr.get_or_add_trPr()
    props.append(OxmlElement(tag))


def _column_widths(rows: list[list[str]], cols: int, total: int) -> list[int]:
    """Share the page width by how much text each column holds, within sensible bounds."""
    weights = []
    for c in range(cols):
        longest = max((len(visible_text(r[c])) for r in rows if c < len(r)), default=1)
        weights.append(min(max(longest, 8), 48))
    scale = total / sum(weights)
    widths = [int(w * scale) for w in weights]
    widths[-1] += total - sum(widths)
    return widths


def add_table(document: Document, rows: list[list[str]]) -> None:
    """Render a parsed markdown table: navy header that repeats across pages, banded rows, fitted columns."""
    if not rows:
        return
    cols = max(len(r) for r in rows)
    section = document.sections[-1]
    total = int(section.page_width - section.left_margin - section.right_margin)
    widths = _column_widths(rows, cols, total)

    table = document.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for r, row in enumerate(rows):
        _row_flag(table.rows[r], "w:cantSplit")
        if r == 0:
            _row_flag(table.rows[r], "w:tblHeader")
        for c in range(cols):
            cell = table.cell(r, c)
            cell.width = Emu(widths[c])
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(2)
            paragraph.paragraph_format.space_after = Pt(2)
            apply_inline(paragraph, row[c] if c < len(row) else "", size=Pt(10))
            if r == 0:
                _shade(cell, HEADER_FILL)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = HEADER_TEXT
            elif r % 2 == 0:
                _shade(cell, BAND_FILL)
    # Space after the table so the next paragraph does not touch its border.
    document.add_paragraph().paragraph_format.space_after = Pt(4)
