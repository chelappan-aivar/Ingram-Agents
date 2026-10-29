"""read_srs: supported types, validation errors, truncation (no AWS)."""
from __future__ import annotations

import pytest

from adr_generator.ingest import InputError, read_srs
from tests.adr_generator.conftest import make_docx, make_settings


def test_md_and_txt(tmp_path, settings):
    md = tmp_path / "srs.md"
    md.write_text("# SRS\nThe system shall store orders.\n", encoding="utf-8")
    name, text, truncated = read_srs(md, settings)
    assert (name, truncated) == ("srs.md", False) and "store orders" in text
    txt = tmp_path / "srs.txt"
    txt.write_text("plain requirements", encoding="utf-8")
    assert read_srs(txt, settings) == ("srs.txt", "plain requirements", False)


def test_docx(tmp_path, settings):
    p = tmp_path / "srs.docx"
    make_docx(p)
    name, text, truncated = read_srs(p, settings)
    assert name == "srs.docx" and not truncated
    assert "# Orders architecture" in text and "| Component | Service | Notes |" in text
    assert "| db | Amazon RDS | multi-AZ |" in text


def test_unsupported_type(tmp_path, settings):
    p = tmp_path / "srs.csv"
    p.write_text("a,b", encoding="utf-8")
    with pytest.raises(InputError, match="unsupported file type '.csv'"):
        read_srs(p, settings)


def test_empty_file_and_blank_text(tmp_path, settings):
    p = tmp_path / "empty.md"
    p.write_bytes(b"")
    with pytest.raises(InputError, match="empty"):
        read_srs(p, settings)
    q = tmp_path / "blank.txt"
    q.write_text("  \n\n ", encoding="utf-8")
    with pytest.raises(InputError, match="empty"):
        read_srs(q, settings)


def test_truncation(tmp_path):
    s = make_settings(tmp_path, MAX_TEXT_CHARS="100")
    p = tmp_path / "big.txt"
    p.write_text("x" * 500, encoding="utf-8")
    name, text, truncated = read_srs(p, s)
    assert truncated is True and len(text) == 100


def test_too_large(tmp_path):
    s = make_settings(tmp_path)
    s = s.model_copy(update={"max_upload_mb": 0})
    p = tmp_path / "a.md"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(InputError, match="too large"):
        read_srs(p, s)


def test_missing_file(tmp_path, settings):
    with pytest.raises(InputError, match="not found"):
        read_srs(tmp_path / "nope.md", settings)


def test_non_utf8_text(tmp_path, settings):
    p = tmp_path / "bad.txt"
    p.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(InputError, match="UTF-8"):
        read_srs(p, settings)


def test_scanned_pdf(tmp_path, settings):
    from pypdf import PdfWriter

    p = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with p.open("wb") as f:
        w.write(f)
    with pytest.raises(InputError, match="Scanned/image-only"):
        read_srs(p, settings)
