"""Markdown tables and inline emphasis."""

import re

from docx.document import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Pt
from docx.text.paragraph import Paragraph


def parse_table_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell values."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def is_table_separator(line: str) -> bool:
    """Detect a markdown table header separator row (---|---)."""
    return bool(re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", line)) and "-" in line


def add_table(document: Document, rows: list[list[str]]) -> None:
    """Render a parsed markdown table as a bordered Word table."""
    if not rows:
        return
    cols = max(len(r) for r in rows)
    table = document.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c in range(cols):
            cell = table.cell(r, c)
            text = row[c] if c < len(row) else ""
            cell.text = ""
            run = cell.paragraphs[0].add_run(text)
            run.font.size = Pt(10)
            if r == 0:
                run.bold = True


def apply_inline(paragraph: Paragraph, text: str) -> None:
    """Render **bold** inline spans; everything else as plain runs."""
    for segment in re.split(r"(\*\*[^*]+\*\*)", text):
        if not segment:
            continue
        if segment.startswith("**") and segment.endswith("**"):
            run = paragraph.add_run(segment[2:-2])
            run.bold = True
        else:
            paragraph.add_run(segment)
