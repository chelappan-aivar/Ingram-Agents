"""Upload validation and multi-file text extraction."""
from __future__ import annotations

import pytest

from diagram_generator.ingest.extractors import SCANNED_PDF_MESSAGE, ExtractionError, extract_sources
from diagram_generator.ingest.validate import UploadError, safe_filename, validate_upload
from tests.diagram_generator.conftest import make_blank_pdf, make_docx, make_pdf, make_settings


def test_validate_ok_all_types(tmp_path):
    s = make_settings(tmp_path)
    p = tmp_path / "x.docx"
    make_docx(p)
    validate_upload("a.md", b"# hi", s)
    validate_upload("a.txt", b"hi", s)
    validate_upload("a.pdf", make_pdf(["hello"]), s)
    validate_upload("a.docx", p.read_bytes(), s)


@pytest.mark.parametrize("name,data,status", [
    ("a.exe", b"x", 415), ("noext", b"x", 415), ("a.md", b"", 400),
    ("a.pdf", b"not a pdf", 415), ("a.docx", b"PKnot a zip", 415), ("a.docx", b"hello", 415),
    ("a.md", b"\xff\xfe\x00bad", 415),
])
def test_validate_rejects(tmp_path, name, data, status):
    with pytest.raises(UploadError) as e:
        validate_upload(name, data, make_settings(tmp_path))
    assert e.value.status_code == status


def test_validate_per_file_size(tmp_path):
    s = make_settings(tmp_path, MAX_UPLOAD_MB=1)
    validate_upload("a.md", b"a" * 1024, s)
    with pytest.raises(UploadError) as e:
        validate_upload("a.md", b"a" * (1024 * 1024 + 1), s)
    assert e.value.status_code == 413


def test_safe_filename():
    assert safe_filename("../../etc/pass wd.md") == "pass wd.md"
    assert safe_filename(None) == "upload"


def test_extract_sources_within_budget(tmp_path, fixtures_dir):
    paths = [fixtures_dir / "multi_a_ingest.md", fixtures_dir / "multi_b_analytics.md"]
    docs = extract_sources(paths, 150000)
    assert [d.file for d in docs] == ["multi_a_ingest.md", "multi_b_analytics.md"]
    assert all(not d.truncated and d.chars_original == len(d.text) > 0 for d in docs)
    assert "IoT Core" in docs[0].text and "QuickSight" in docs[1].text


def test_extract_sources_budget_shared_proportionally(tmp_path):
    big, small = tmp_path / "big.md", tmp_path / "small.md"
    big.write_text("b" * 3000, encoding="utf-8")
    small.write_text("s" * 1000, encoding="utf-8")
    docs = extract_sources([big, small], 2000)
    assert [len(d.text) for d in docs] == [1500, 500]
    assert all(d.truncated for d in docs) and [d.chars_original for d in docs] == [3000, 1000]
    assert sum(len(d.text) for d in docs) <= 2000


def test_extract_sources_only_big_file_truncated_flag_per_file(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_text("a" * 100, encoding="utf-8")
    b.write_text("b" * 100, encoding="utf-8")
    docs = extract_sources([a, b], 150)
    assert [len(d.text) for d in docs] == [75, 75] and all(d.truncated for d in docs)
    docs = extract_sources([a, b], 200)
    assert not any(d.truncated for d in docs)


def test_scanned_pdf_fails_with_v1_message(fixtures_dir, tmp_path):
    for pdf in (fixtures_dir / "e_scanned.pdf", None):
        path = pdf
        if path is None:
            path = tmp_path / "blank.pdf"
            path.write_bytes(make_blank_pdf())
        with pytest.raises(ExtractionError) as e:
            extract_sources([path], 1000)
        assert SCANNED_PDF_MESSAGE in str(e.value) and path.name in str(e.value)


def test_scanned_pdf_in_multi_file_fails_whole_call(fixtures_dir):
    with pytest.raises(ExtractionError):
        extract_sources([fixtures_dir / "a_clean.md", fixtures_dir / "e_scanned.pdf"], 100000)


def test_fixture_formats_extract(fixtures_dir):
    docs = extract_sources([fixtures_dir / n for n in ("a_clean.md", "b_prose.pdf", "c_tables.docx", "d_vague.md")], 10**7)
    assert all(d.chars_original > 200 for d in docs)
    assert "|" in docs[2].text  # docx tables rendered as pipe rows


def test_text_pdf_and_empty_and_bad_files(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([f"Line {i} describes an Application Load Balancer in front of ECS" for i in range(12)]))
    assert "Load Balancer" in extract_sources([pdf], 10000)[0].text
    empty = tmp_path / "e.md"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(ExtractionError):
        extract_sources([empty], 100)
    bad = tmp_path / "b.txt"
    bad.write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(ExtractionError, match="UTF-8"):
        extract_sources([bad], 100)
