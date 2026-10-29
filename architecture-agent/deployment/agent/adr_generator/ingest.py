"""Read one SRS file (md / markdown / txt / docx / pdf) into text, with validation and a character budget.
Never logs file contents."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".docx", ".pdf"}
MIN_PDF_CHARS = 200
SCANNED_PDF_MESSAGE = "Scanned/image-only PDFs are not supported yet; please provide a text-based document"


class InputError(Exception):
    """The SRS file cannot be used (missing, wrong type, too large, empty, unreadable)."""


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
        raise InputError(SCANNED_PDF_MESSAGE)
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
        raise InputError("The DOCX contains no extractable text")
    return text


def extract_text_file(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def read_srs(path: Path, settings) -> tuple[str, str, bool]:
    """Validate and read one SRS file. Returns (file name, text, truncated); text is cut to
    settings.max_text_chars (truncated=True). Raises InputError with the file name in the message."""
    path = Path(path)
    name = path.name
    if not path.is_file():
        raise InputError(f"{name}: file not found")
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise InputError(f"{name}: unsupported file type '{ext or name}'. Accepted: .md, .markdown, .txt, .docx, .pdf")
    size = path.stat().st_size
    if size == 0:
        raise InputError(f"{name}: the file is empty")
    if size > settings.max_upload_bytes:
        raise InputError(f"{name}: file is too large (max {settings.max_upload_bytes // (1024 * 1024)} MB)")
    try:
        if ext == ".pdf":
            text = extract_pdf(path)
        elif ext == ".docx":
            text = extract_docx(path)
        else:
            text = extract_text_file(path)
    except InputError as e:
        raise InputError(f"{name}: {e}") from e
    except UnicodeDecodeError as e:
        raise InputError(f"{name}: text files must be UTF-8 encoded") from e
    except Exception as e:  # corrupt document etc.
        raise InputError(f"{name}: could not read the file ({type(e).__name__})") from e
    if not text.strip():
        raise InputError(f"{name}: the document is empty")
    truncated = len(text) > settings.max_text_chars
    return name, text[:settings.max_text_chars], truncated
