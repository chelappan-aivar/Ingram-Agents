"""Harness log: one JSON line per harness record in job_dir/harness.log.jsonl, plus a short summary."""
from __future__ import annotations

from pathlib import Path

from diagram_generator.contracts import HarnessRecord

LOG_NAME = "harness.log.jsonl"


def append_record(job_dir: Path, record: HarnessRecord) -> Path:
    path = Path(job_dir) / LOG_NAME
    with path.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return path


def read_records(job_dir: Path) -> list[HarnessRecord]:
    path = Path(job_dir) / LOG_NAME
    if not path.exists():
        return []
    return [HarnessRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(record: HarnessRecord) -> str:
    """One human line for live status, e.g. 'harness_1 ok: in files=2 chars=18000 -> out components=14 ...'."""
    def fmt(d: dict) -> str:
        return " ".join(f"{k}={v}" for k, v in d.items()) or "-"
    status = "ok" if record.ok else "FAILED"
    extra = []
    if record.dropped:
        extra.append(f"dropped={len(record.dropped)}")
    if record.warnings:
        extra.append(f"warnings={len(record.warnings)}")
    if record.errors:
        extra.append(f"errors={len(record.errors)}")
    tail = (" | " + " ".join(extra)) if extra else ""
    att = f" (attempt {record.attempt})" if record.attempt > 1 else ""
    return f"{record.step}{att} {status}: in {fmt(record.in_counts)} -> out {fmt(record.out_counts)}{tail}"
