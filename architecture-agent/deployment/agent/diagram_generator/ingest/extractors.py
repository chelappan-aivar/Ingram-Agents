"""Text extraction for pdf / docx / txt / md, with a character budget shared across files.
Never logs file contents."""
from __future__ import annotations

import logging
from pathlib import Path

from diagram_generator.contracts import SourceDoc

log = logging.getLogger(__name__)

MIN_PDF_CHARS = 200
SCANNED_PDF_MESSAGE = "Scanned/image-only PDFs are not supported yet; please provide a text-based document"


class ExtractionError(Exception):
    pass


def _non_ws(s: str) -> int:
    return sum(1 for ch in s if not ch.isspace())


def _pdf_pdfplumber(path: Path) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n\n".join(parts)


def _pdf_pypdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages)


def extract_pdf(path: Path) -> str:
    text = ""
    try:
        text = _pdf_pdfplumber(path)
    except Exception as e:
        log.warning("pdfplumber failed (%s); trying pypdf", type(e).__name__)
    if _non_ws(text) < MIN_PDF_CHARS:
        try:
            alt = _pdf_pypdf(path)
            if _non_ws(alt) > _non_ws(text):
                text = alt
        except Exception as e:
            log.warning("pypdf failed (%s)", type(e).__name__)
    if _non_ws(text) < MIN_PDF_CHARS:
        raise ExtractionError(SCANNED_PDF_MESSAGE)
    return text


def extract_docx(path: Path) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    lines: list[str] = []
    # iterate body in document order so tables stay next to their surrounding text
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            if p.text.strip():
                style = (p.style.name if p.style is not None else "") or ""
                prefix = ""
                if style.startswith("Heading"):
                    digits = "".join(ch for ch in style if ch.isdigit())
                    prefix = "#" * (int(digits) if digits else 1) + " "
                elif style == "Title":
                    prefix = "# "
                lines.append(prefix + p.text.strip())
        elif tag == "tbl":
            t = Table(child, d)
            for row in t.rows:
                cells: list[str] = []
                prev = None
                for c in row.cells:
                    if prev is not None and c._tc is prev:  # merged cell repeats
                        continue
                    prev = c._tc
                    cells.append(" ".join(c.text.split()))
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    text = "\n".join(lines).strip()
    if not text:
        raise ExtractionError("The DOCX contains no extractable text")
    return text


def extract_text_file(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    if not text.strip():
        raise ExtractionError("The document is empty")
    return text


def extract_file(path: Path) -> str:
    """Full text of one file (no truncation). Raises ExtractionError with the file name in the message."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return extract_pdf(path)
        if ext == ".docx":
            return extract_docx(path)
        if ext in (".txt", ".md"):
            return extract_text_file(path)
    except ExtractionError as e:
        raise ExtractionError(f"{path.name}: {e}") from e
    except UnicodeDecodeError as e:
        raise ExtractionError(f"{path.name}: text files must be UTF-8 encoded") from e
    except Exception as e:  # corrupt document etc.
        raise ExtractionError(f"{path.name}: could not read the file ({type(e).__name__})") from e
    raise ExtractionError(f"{path.name}: unsupported file type '{ext}'")


def extract_sources(paths: list[Path], max_total_chars: int) -> list[SourceDoc]:
    """One SourceDoc per file. When the total text exceeds `max_total_chars`, every file gets a
    share of the budget proportional to its text length; `truncated` is set per file."""
    texts = [(Path(p), extract_file(Path(p))) for p in paths]
    total = sum(len(t) for _, t in texts)
    over = total > max_total_chars
    docs: list[SourceDoc] = []
    for path, text in texts:
        original = len(text)
        if over:
            budget = max_total_chars * original // total
            truncated = original > budget
            text = text[:budget]
        else:
            truncated = False
        docs.append(SourceDoc(file=path.name, text=text, chars_original=original, truncated=truncated))
    return docs
