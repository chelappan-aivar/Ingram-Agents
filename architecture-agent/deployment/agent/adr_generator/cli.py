"""Run the ADR Generator once: SRS file -> adr.md + spec.json, printing live status.

  python -m adr_generator.cli SRS_FILE [--out DIR]

Needs AWS credentials for real Bedrock calls. Paths are resolved.
"""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent

from adr_generator.config import Settings, get_settings
from adr_generator.contracts import RunMeta
from adr_generator.generator import generate
from adr_generator.ingest import InputError, read_srs
from adr_generator.llm.bedrock import LLMError, make_llm
from adr_generator.runs import persist_run
from adr_generator.scope import parse_scope, violations as scope_violations


def _load_env() -> None:
    env = ROOT_DIR / ".env"
    if env.is_file():
        try:
            from dotenv import load_dotenv  # optional

            load_dotenv(env)
        except ImportError:  # minimal fallback parser
            import os

            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def status_line(stage: str, message: str, counts: Optional[dict] = None) -> str:
    clock = datetime.now().strftime("%H:%M:%S")
    tail = f"  {counts}" if counts else ""
    return f"[{clock}] {stage:<10}  {message}{tail}"


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None, settings: Optional[Settings] = None, llm=None, out=print) -> int:
    ap = argparse.ArgumentParser(description="ADR Generator: SRS file -> adr.md + spec.json")
    ap.add_argument("srs", metavar="SRS_FILE")
    ap.add_argument("--out", metavar="DIR", help="data directory (default: DATA_DIR from settings)")
    ap.add_argument("--scope", metavar="FILE",
                    help="JSON file with the SOW's agreed scope: {\"in_scope\": [...], \"out_of_scope\": [...]}")
    args = ap.parse_args(argv)

    srs_path = Path(args.srs)
    if not srs_path.is_file():
        out(f"File not found: {srs_path}")
        return 2

    scope = None
    if args.scope:
        scope_path = Path(args.scope)
        if not scope_path.is_file():
            out(f"Scope file not found: {scope_path}")
            return 2
        try:
            scope = parse_scope({"scope": json.loads(scope_path.read_text(encoding="utf-8"))})
        except (ValueError, TypeError) as e:
            out(f"Scope file is not valid JSON Scope: {e}")
            return 2

    if settings is None:
        _load_env()
        get_settings.cache_clear()
        settings = get_settings()
    if args.out:
        settings = settings.model_copy(update={"data_dir": Path(args.out)})

    run_id = new_run_id()
    run_dir = settings.runs_dir / run_id
    (run_dir / "input").mkdir(parents=True)
    shutil.copyfile(srs_path, run_dir / "input" / srs_path.name)
    meta = RunMeta(run_id=run_id, source_srs=srs_path.name, model_id=settings.model_id)

    def fail(stage: str, reason: str) -> int:
        out(status_line("FAILED", f"{stage}: {reason}"))
        meta.status, meta.error = "failed", f"{stage}: {reason}"
        _write_json(run_dir / "metadata.json", meta.model_dump())
        out(f"Run folder: {run_dir}")
        return 1

    # --- INGEST
    out(status_line("INGEST", f"Reading {srs_path.name}"))
    try:
        name, text, truncated = read_srs(srs_path, settings)
    except InputError as e:
        return fail("INGEST", str(e))
    note = f" (truncated to {settings.max_text_chars} characters)" if truncated else ""
    out(status_line("INGEST", f"Read {name}{note}", {"chars": len(text)}))
    if scope:
        out(status_line("SCOPE", "Applying the SOW scope",
                        {"in_scope": len(scope.in_scope), "out_of_scope": len(scope.out_of_scope)}))

    # --- DESIGN
    try:
        llm = llm or make_llm(settings)
        out(status_line("DESIGN", f"Calling {llm.model_id} (one call, this can take a few minutes)"))
        design, raw = generate(llm, settings, name, text, scope)
    except LLMError as e:
        call = getattr(llm, "last_call", None) or {}
        meta.model_id = call.get("model_id") or meta.model_id
        meta.fallback_used = bool(call.get("fallback_used"))
        meta.input_tokens, meta.output_tokens = call.get("input_tokens", 0), call.get("output_tokens", 0)
        meta.seconds, meta.stop_reason = call.get("seconds", 0.0), call.get("stop_reason")
        return fail("DESIGN", f"{e} [{e.kind}]")
    call = llm.last_call
    counts = {
        "requirements": len(design.requirements), "decisions": len(design.decisions),
        "components": len(design.components), "relationships": len(design.relationships),
        "assumptions": len(design.assumptions), "assumed_components": sum(1 for c in design.components if c.assumed),
    }
    meta.model_id = call.get("model_id") or llm.model_id
    meta.fallback_used = bool(call.get("fallback_used"))
    meta.input_tokens, meta.output_tokens = call.get("input_tokens", 0), call.get("output_tokens", 0)
    meta.seconds, meta.stop_reason, meta.counts = call.get("seconds", 0.0), call.get("stop_reason"), counts
    out(status_line("DESIGN", f"Design received in {meta.seconds}s ({meta.input_tokens} in / "
                              f"{meta.output_tokens} out tokens, stop: {meta.stop_reason})", counts))

    # --- RENDER
    try:
        persist_run(run_dir, design, raw, meta, source_name=name)
    except Exception as e:  # a render bug must still leave design.json and metadata.json behind
        return fail("RENDER", f"{type(e).__name__}: {e}")
    out(status_line("RENDER", "Wrote adr.md and spec.json"))

    if scope:
        warnings = scope_violations(design, scope)
        _write_json(run_dir / "scope.json",
                    {**scope.model_dump(mode="json"), "violations": warnings})
        for warning in warnings:
            out(status_line("SCOPE", warning))

    out(status_line("DONE", f"Run {run_id} completed", counts))
    out("")
    out(f"Run folder: {run_dir}")
    out("Artifacts:")
    for artifact in ("adr.md", "spec.json", "design.json", "metadata.json"):
        out(f"  {run_dir / artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
