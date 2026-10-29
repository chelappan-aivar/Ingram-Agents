"""Run the Usage Estimator on one ADR run folder: design.json + input/<SRS> -> usage.json + usage_assumptions.md.

  python -m usage_estimator.cli RUN_DIR          (a folder written by adr_generator.cli, e.g. data/runs/<run_id>)

Writes into RUN_DIR: usage.json (the cost-calculator schema), usage_assumptions.md (warnings, workload profile,
how each figure was reached), usage.raw.json (the raw `submit_usage` tool input) and usage_metadata.json.
Needs AWS credentials for real Bedrock calls.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from adr_generator.cli import _load_env, status_line
from adr_generator.config import Settings, get_settings
from adr_generator.contracts import Design
from adr_generator.ingest import InputError, read_srs
from adr_generator.llm.bedrock import LLMError, make_llm
from usage_estimator.estimator import estimate
from usage_estimator.render import build_usage, render_assumptions
from usage_estimator.rules import Estimate, apply


@dataclass
class UsageRun:
    usage: dict                 # usage.json
    assumptions_md: str         # usage_assumptions.md
    raw: dict                   # the raw tool input
    estimate: Estimate
    call: dict                  # llm.last_call: model, tokens, seconds, stop reason, fallback


def run(llm, settings: Settings, design: Design, srs_name: str, srs_text: str) -> UsageRun:
    """One model call, then the code rules and both renderers. Raises LLMError when the call fails."""
    raw = estimate(llm, settings, design, srs_name, srs_text)
    est = apply(raw, design)
    call = dict(llm.last_call or {})
    md = render_assumptions(est, design, source_name=srs_name, run_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                            model_id=call.get("model_id") or llm.model_id)
    return UsageRun(build_usage(est), md, raw if isinstance(raw, dict) else {}, est, call)


def counts(result: UsageRun) -> dict:
    return {"entries": len(result.estimate.entries), "drivers": len(result.estimate.profile),
            "warnings": len(result.estimate.warnings)}


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None, settings: Optional[Settings] = None, llm=None, out=print) -> int:
    ap = argparse.ArgumentParser(description="Usage Estimator: ADR run folder -> usage.json + usage_assumptions.md")
    ap.add_argument("run_dir", metavar="RUN_DIR")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    srs_files = sorted(p for p in (run_dir / "input").glob("*") if p.is_file())
    if not (run_dir / "design.json").is_file() or len(srs_files) != 1:
        out(f"Not an ADR run folder (needs design.json and one file in input/): {run_dir}")
        return 2
    if settings is None:
        _load_env()
        get_settings.cache_clear()
        settings = get_settings()
    meta: dict = {"run_id": run_dir.name, "status": "completed", "error": None}

    def fail(stage: str, reason: str) -> int:
        out(status_line("FAILED", f"{stage}: {reason}"))
        _write_json(run_dir / "usage_metadata.json", meta | {"status": "failed", "error": f"{stage}: {reason}"})
        return 1

    out(status_line("INGEST", f"Reading design.json and {srs_files[0].name}"))
    try:
        design = Design.model_validate(json.loads((run_dir / "design.json").read_text(encoding="utf-8")))
        name, text, _ = read_srs(srs_files[0], settings)
    except (InputError, ValueError) as e:
        return fail("INGEST", str(e))

    try:
        llm = llm or make_llm(settings)
        out(status_line("USAGE", f"Calling {llm.model_id} (one call)"))
        result = run(llm, settings, design, name, text)
    except LLMError as e:
        meta |= getattr(llm, "last_call", None) or {}
        return fail("USAGE", f"{e} [{e.kind}]")
    c = result.call
    meta |= c | {"counts": counts(result)}
    out(status_line("USAGE", f"Estimate received in {c.get('seconds', 0.0)}s ({c.get('input_tokens', 0)} in / "
                             f"{c.get('output_tokens', 0)} out tokens, stop: {c.get('stop_reason')})", counts(result)))

    _write_json(run_dir / "usage.json", result.usage)
    (run_dir / "usage_assumptions.md").write_text(result.assumptions_md, encoding="utf-8")
    _write_json(run_dir / "usage.raw.json", result.raw)
    _write_json(run_dir / "usage_metadata.json", meta)
    out(status_line("DONE", "Wrote usage.json and usage_assumptions.md", counts(result)))
    for artifact in ("usage.json", "usage_assumptions.md", "usage.raw.json", "usage_metadata.json"):
        out(f"  {run_dir / artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
