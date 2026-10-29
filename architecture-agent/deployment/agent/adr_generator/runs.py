"""Persist one run's artifacts to disk, in the exact on-disk format `adr_generator.cli` produces:

  run_dir/design.json     raw tool input (the model's `submit_design` call, unvalidated)
  run_dir/adr.md          rendered low-level ADR
  run_dir/spec.json       rendered cost/spec JSON
  run_dir/metadata.json   RunMeta.model_dump()

Pure I/O helper so the CLI and the AgentCore entrypoint write runs the same way.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from adr_generator.contracts import Design, RunMeta
from adr_generator.render.adr_md import render_adr
from adr_generator.render.spec_json import build_spec


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def persist_run(run_dir: Path, design: Design, raw: dict, meta: RunMeta, source_name: str) -> None:
    """Write design.json, adr.md, spec.json and metadata.json into `run_dir` (must already exist).

    `meta` is written as-is (metadata.json = meta.model_dump()); the caller is responsible for
    populating its token/timing/counts fields from `llm.last_call` before calling this, exactly as
    adr_generator.cli does. `source_name` is the SRS file name used in both renderers."""
    now = datetime.now(timezone.utc)
    _write_json(run_dir / "design.json", raw)
    (run_dir / "adr.md").write_text(
        render_adr(design, source_name=source_name, run_date=now.strftime("%Y-%m-%d")), encoding="utf-8")
    _write_json(run_dir / "spec.json", build_spec(design, source_name=source_name, generated_at=now.isoformat()))
    _write_json(run_dir / "metadata.json", meta.model_dump())
