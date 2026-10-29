"""Renders the markdown SOW body: headings, bullets, tables, images and paragraphs."""

import re

from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.modules.sow_generator.views.docx.images import ImageLibrary, add_picture
from app.modules.sow_generator.views.docx.tables import (
    add_table,
    apply_inline,
    is_table_separator,
    parse_table_row,
)

# Markdown image: ![alt](target)
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")


_HEADING_LINE_RE = re.compile(r"^(#{1,3})\s+(.*)$")


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
                para = document.add_paragraph()
                para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                apply_inline(para, alt)
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

        if re.match(r"^[-*]\s+", stripped):
            para = document.add_paragraph(style="List Bullet")
            apply_inline(para, re.sub(r"^[-*]\s+", "", stripped))
            i += 1
            continue

        para = document.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        apply_inline(para, stripped)
        i += 1

    append_unused_images(document, images)


def append_unused_images(document: Document, images: ImageLibrary) -> None:
    """Append any provided-but-unreferenced diagrams so none are silently dropped."""
    unused = images.unused()
    if not unused:
        return
    document.add_heading("Architecture Diagrams", level=1)
    for raw in unused:
        add_picture(document, raw, "")
