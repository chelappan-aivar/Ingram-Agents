"""Renders the markdown SOW body: headings, bullets, tables, images and paragraphs."""

import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from sow_generator.views.docx.images import ImageLibrary, add_picture
from sow_generator.views.docx.tables import (
    add_table,
    apply_inline,
    is_table_separator,
    parse_table_row,
    should_justify,
)

# Markdown image: ![alt](target)
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")


_HEADING_LINE_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
_RULE_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
BODY_SPACE_AFTER = Pt(6)


_LOGO_PLACEHOLDER_RE = re.compile(
    r"^\[(?:aivar-logo|company-logo|client-logo)\]$",
    flags=re.IGNORECASE,
)


def add_body(document: Document, markdown: str, images: ImageLibrary) -> None:
    """Render the markdown SOW body (headings, bullets, tables, images, paragraphs)."""
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if _LOGO_PLACEHOLDER_RE.fullmatch(stripped):
            i += 1
            continue

        heading_match = _HEADING_LINE_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            document.add_heading(heading_match.group(2).strip(), level=level)
            i += 1
            continue

        image_match = _IMAGE_RE.match(stripped)
        if image_match:
            alt, target = image_match.group(1), image_match.group(2)
            raw = images.resolve(target)
            if raw is not None and add_picture(document, raw, alt):
                i += 1
                continue
            if alt.strip():  # unresolved reference falls back to its alt text
                _paragraph(document, alt)
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            rows = [parse_table_row(stripped)]
            i += 2  # skip header + separator
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(parse_table_row(lines[i].strip()))
                i += 1
            add_table(document, rows)
            continue

        if _RULE_RE.match(stripped):          # a markdown horizontal rule is not content
            i += 1
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            depth = min(len(bullet.group(1).replace("\t", "    ")) // 2, 2)
            style = "List Bullet" if depth == 0 else f"List Bullet {depth + 1}"
            para = document.add_paragraph(style=style)
            para.paragraph_format.space_after = Pt(3)
            apply_inline(para, bullet.group(2))
            i += 1
            continue

        numbered = _NUMBERED_RE.match(line)
        if numbered:
            # Word's automatic numbering does not restart per list, so keep the author's number.
            para = document.add_paragraph()
            para.paragraph_format.left_indent = Inches(0.35 + 0.25 * (len(numbered.group(1)) // 2))
            para.paragraph_format.first_line_indent = Inches(-0.3)
            para.paragraph_format.space_after = Pt(3)
            apply_inline(para, f"{numbered.group(2)}\t{numbered.group(3)}")
            para.paragraph_format.tab_stops.add_tab_stop(para.paragraph_format.left_indent)
            i += 1
            continue

        _paragraph(document, stripped)
        i += 1

    append_unused_images(document, images)


def _paragraph(document: Document, text: str) -> None:
    """Body prose. Justified unless it holds a URL or other long word that would stretch the line."""
    para = document.add_paragraph()
    if should_justify(text):
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    para.paragraph_format.space_after = BODY_SPACE_AFTER
    apply_inline(para, text)


def append_unused_images(document: Document, images: ImageLibrary) -> None:
    """Append any provided-but-unreferenced diagrams so none are silently dropped."""
    unused = images.unused()
    if not unused:
        return
    document.add_heading("Architecture Diagrams", level=1)
    for raw in unused:
        add_picture(document, raw, "")
