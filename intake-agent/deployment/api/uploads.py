"""Uploaded CRM records (CSV, Excel) and seller notes (PDF, Word) -> plain-text records for the agent.

The browser sends each file base64-encoded in the POST /runs body. Lambda accepts a 6 MB
request, so the raw files are capped at MAX_TOTAL_BYTES (base64 adds a third). Files are
untrusted: sizes, rows, columns, PDF pages and the uncompressed size of DOCX / XLSX zips are
all capped, and anything that cannot be read raises UploadError with a message the UI shows as is.

    CRM records   up to MAX_CRM_FILES .csv / .xlsx files, first row = column headers, one record per row
    Seller notes  up to MAX_NOTES_FILES .pdf / .docx files (one record per file) and pasted text
"""

import base64
import binascii
import csv
import io
import re
import zipfile
from datetime import date, datetime, time
from typing import Any
from xml.etree import ElementTree

MAX_TOTAL_BYTES = 3 * 1024 * 1024        # raw bytes, all files together
MAX_NOTES_FILES = 5
MAX_CRM_FILES = 3
MAX_CRM_RECORDS = 50                     # CRM rows analysed per run, all CRM files together
MAX_PASTE_CHARS = 20_000                 # pasted seller notes
MAX_SCAN_ROWS = 5000                     # rows read to count what was left out
MAX_COLUMNS = 100
MAX_CELL_CHARS = 2000
MAX_RECORD_CHARS = 8000                  # one CRM row as text
MAX_NOTE_CHARS = 60_000                  # one seller note as text
MAX_PDF_PAGES = 150
MAX_ZIP_BYTES = 50 * 1024 * 1024         # uncompressed DOCX / XLSX content (zip-bomb guard)
NAME_RE = re.compile(r"[^\w .()\-]")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Header names (lower case, no punctuation) used to give each CRM row a readable title.
_ACCOUNT_HEADERS = ("account name", "account", "customer name", "customer", "company name", "company",
                    "organisation", "organization", "end customer", "client")
_OPPORTUNITY_HEADERS = ("opportunity name", "opportunity title", "opportunity", "deal name", "deal",
                        "project name", "project", "subject", "title", "name")


class UploadError(ValueError):
    pass


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _decode_files(files: object, max_files: int) -> list[tuple[str, bytes]]:
    if not isinstance(files, list) or not files:
        raise UploadError("Choose a file to upload.")
    if len(files) > max_files:
        raise UploadError(f"Upload at most {max_files} file{'s' if max_files > 1 else ''} at a time.")
    decoded, total = [], 0
    for f in files:
        if not isinstance(f, dict):
            raise UploadError("Each file needs a name and content.")
        name = NAME_RE.sub("", str(f.get("name") or "file"))[:120].strip() or "file"
        try:
            data = base64.b64decode(str(f.get("content_base64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UploadError(f"{name}: the upload was damaged. Try again.") from exc
        if not data:
            raise UploadError(f"{name}: the file is empty.")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise UploadError(f"Files are larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB in total.")
        decoded.append((name, data))
    return decoded


def _check_zip(z: zipfile.ZipFile, name: str) -> None:
    if sum(i.file_size for i in z.infolist()) > MAX_ZIP_BYTES:
        raise UploadError(f"{name}: the file is too large to read.")


# ---------------------------------------------------------------- CRM records: CSV / Excel

def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()[:MAX_CELL_CHARS]


def _csv_rows(data: bytes, name: str) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")   # Excel's "CSV" on Windows
    if "\x00" in text:
        raise UploadError(f"{name}: this does not look like a CSV text file.")
    try:
        dialect = csv.Sniffer().sniff(text[:20_000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    try:
        for row in csv.reader(io.StringIO(text), dialect):
            rows.append([_cell(c) for c in row[:MAX_COLUMNS]])
            if len(rows) > MAX_SCAN_ROWS:
                break
    except csv.Error as exc:
        raise UploadError(f"{name}: the CSV could not be read ({exc}).") from exc
    return rows


def _xlsx_rows(data: bytes, name: str) -> list[list[str]]:
    from openpyxl import load_workbook   # imported here so a broken wheel only breaks Excel uploads
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _check_zip(z, name)
            if "xl/workbook.xml" not in z.namelist():
                raise UploadError(f"{name}: this is not an Excel workbook (.xlsx).")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except UploadError:
        raise
    except Exception as exc:   # openpyxl raises many types for damaged files
        raise UploadError(f"{name}: this Excel file could not be read ({type(exc).__name__}). "
                          "Old .xls files are not supported; save it as .xlsx or .csv.") from exc
    try:
        sheets = [wb.active] + [ws for ws in wb.worksheets if ws is not wb.active]
        for ws in sheets:
            if ws is None:
                continue
            rows = []
            for row in ws.iter_rows(values_only=True, max_col=MAX_COLUMNS):
                rows.append([_cell(c) for c in row])
                if len(rows) > MAX_SCAN_ROWS:
                    break
            if sum(1 for r in rows if any(r)) >= 2:   # headers + at least one record
                return rows
        return []
    finally:
        wb.close()


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()


def _title_column(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    normed = [_norm_header(h) for h in headers]
    for c in candidates:
        if c in normed:
            return normed.index(c)
    return None


def crm_records(name: str, data: bytes) -> tuple[list[dict[str, str]], dict[str, Any]]:
    lower = name.lower()
    if lower.endswith(".csv"):
        rows, kind = _csv_rows(data, name), "csv"
    elif lower.endswith(".xlsx") and data.startswith(b"PK"):
        rows, kind = _xlsx_rows(data, name), "xlsx"
    else:
        raise UploadError(f"{name}: CRM records must be a CSV (.csv) or Excel (.xlsx) file.")

    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        raise UploadError(f"{name}: no records found. The first row must be the column headers.")
    seen: dict[str, int] = {}
    headers = []
    for i, h in enumerate(rows[0]):
        h = h or f"Column {i + 1}"
        seen[h] = seen.get(h, 0) + 1
        headers.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    account_col = _title_column(headers, _ACCOUNT_HEADERS)
    opp_col = _title_column(headers, _OPPORTUNITY_HEADERS)

    records, data_rows = [], rows[1:]
    for number, row in enumerate(data_rows[:MAX_CRM_RECORDS], start=2):   # spreadsheet row number
        pairs = [(headers[i] if i < len(headers) else f"Column {i + 1}", v) for i, v in enumerate(row) if v]
        text = "\n".join(f"{h}: {v}" for h, v in pairs)[:MAX_RECORD_CHARS]
        parts = [row[c] for c in (account_col, opp_col) if c is not None and c < len(row) and row[c]]
        title = " – ".join(dict.fromkeys(parts)) or f"Row {number}"
        records.append({"title": f"{title[:180]} ({name}, row {number})", "text": text, "file_name": name})
    summary = {"name": name, "type": kind, "bytes": len(data), "rows": len(data_rows),
               "records": len(records), "columns": len(headers),
               "truncated": len(data_rows) > MAX_CRM_RECORDS or len(rows) > MAX_SCAN_ROWS}
    return records, summary


# ---------------------------------------------------------------- seller notes: PDF / Word

def _pdf_text(data: bytes, name: str) -> tuple[str, int]:
    from pypdf import PdfReader          # imported here so a broken wheel only breaks PDF uploads
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UploadError(f"{name}: the PDF is password-protected. Remove the password and upload it again.")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise UploadError(f"{name}: {pages} pages; the limit is {MAX_PDF_PAGES}.")
        parts = []
        for number, page in enumerate(reader.pages, 1):
            text = _clean(page.extract_text() or "")
            if text:
                parts.append(f"[page {number}]\n{text}")
    except UploadError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise UploadError(f"{name}: this PDF could not be read ({type(exc).__name__}).") from exc
    return "\n\n".join(parts), pages


def _paragraph(p) -> str:
    out = []
    for node in p.iter():
        if node.tag == f"{_W}t" and node.text:
            out.append(node.text)
        elif node.tag == f"{_W}tab":
            out.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _docx_text(data: bytes, name: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _check_zip(z, name)
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except UploadError:
        raise
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise UploadError(f"{name}: this is not a readable Word (.docx) file. Old .doc files are not supported.") from exc
    lines = []
    body = root.find(f"{_W}body")
    for block in (body if body is not None else []):
        if block.tag == f"{_W}p":
            lines.append(_paragraph(block))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = [" ".join(_paragraph(p) for p in cell.iter(f"{_W}p")).strip() for cell in row.iter(f"{_W}tc")]
                lines.append(" | ".join(c for c in cells if c))
    return _clean("\n".join(lines))


def seller_note(name: str, data: bytes) -> tuple[dict[str, str], dict[str, Any]]:
    lower, pages = name.lower(), None
    if lower.endswith(".pdf") and data.startswith(b"%PDF"):
        text, pages = _pdf_text(data, name)
        kind = "pdf"
    elif lower.endswith(".docx") and data.startswith(b"PK"):
        text, kind = _docx_text(data, name), "docx"
    else:
        raise UploadError(f"{name}: seller notes must be a PDF (.pdf) or Word (.docx) file.")
    if not text:
        raise UploadError(f"{name}: no text found. Scanned or image-only documents are not supported yet.")
    truncated = len(text) > MAX_NOTE_CHARS
    title = re.sub(r"\.(pdf|docx)$", "", name, flags=re.IGNORECASE)
    record = {"title": title[:200], "text": text[:MAX_NOTE_CHARS], "file_name": name}
    return record, {"name": name, "type": kind, "bytes": len(data), "pages": pages,
                    "chars": len(record["text"]), "records": 1, "truncated": truncated}


# ---------------------------------------------------------------- entry point

def parse_inputs(crm_files: object, notes_files: object, notes_text: object = "") -> tuple[list[dict], list[dict]]:
    """CRM files, seller-note files and pasted notes -> (documents for the agent, summary per input).

    Each document is {"source": "crm" | "notes", "title", "text", "file_name"}. All files share
    MAX_TOTAL_BYTES; CRM rows across all CRM files are capped at MAX_CRM_RECORDS.
    """
    crm_files = crm_files or []
    notes_files = notes_files or []
    if not isinstance(crm_files, list) or not isinstance(notes_files, list):
        raise UploadError("files must be lists")
    if len(crm_files) > MAX_CRM_FILES:
        raise UploadError(f"Add at most {MAX_CRM_FILES} CRM files.")
    if len(notes_files) > MAX_NOTES_FILES:
        raise UploadError(f"Add at most {MAX_NOTES_FILES} seller-note files.")
    decoded = _decode_files(crm_files + notes_files, MAX_CRM_FILES + MAX_NOTES_FILES) if crm_files or notes_files else []
    names = [n for n, _ in decoded]
    if len(set(names)) != len(names):
        raise UploadError("Two files have the same name. Rename one and add it again.")

    documents, summaries, crm_left = [], [], MAX_CRM_RECORDS
    for name, data in decoded[:len(crm_files)]:
        records, summary = crm_records(name, data)
        if len(records) > crm_left:
            records, summary["truncated"] = records[:crm_left], True
        summary["records"] = len(records)
        crm_left -= len(records)
        documents += [{"source": "crm", **r} for r in records]
        summaries.append({"source": "crm", **summary})
    for name, data in decoded[len(crm_files):]:
        record, summary = seller_note(name, data)
        documents.append({"source": "notes", **record})
        summaries.append({"source": "notes", **summary})

    text = _clean(str(notes_text or ""))
    if len(text) > MAX_PASTE_CHARS:
        raise UploadError(f"Pasted notes are longer than {MAX_PASTE_CHARS:,} characters.")
    if text:
        documents.append({"source": "notes", "title": "Pasted notes", "text": text, "file_name": "Pasted notes"})
        summaries.append({"source": "notes", "name": "Pasted notes", "type": "text", "chars": len(text),
                          "records": 1, "truncated": False})
    return documents, summaries
