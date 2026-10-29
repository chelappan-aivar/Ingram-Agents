"""Run the diagram pipeline once, printing live status.

  python -m diagram_generator.cli FILE [FILE ...] [--out DIR]

Needs AWS credentials for real Bedrock calls. Paths are resolved.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent

from diagram_generator.config import Settings, Step, get_settings
from diagram_generator.jobs import EventBus, JobStore, make_event
from diagram_generator.llm.bedrock import make_llm
from diagram_generator.pipeline import Pipeline


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


def format_event(ev: dict) -> str:
    ts = ev.get("ts", "")
    try:
        clock = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        clock = datetime.now().strftime("%H:%M:%S")
    counts = f"  {ev['counts']}" if ev.get("counts") else ""
    return f"[{clock}] {ev['stage']:<13}  {ev['message']}{counts}"


def main(argv: Optional[list[str]] = None, settings: Optional[Settings] = None,
         stages: Optional[list] = None, llm_factory: Optional[Callable[[Step], object]] = None,
         out=print, on_event: Optional[Callable[[dict], None]] = None) -> int:
    ap = argparse.ArgumentParser(description="Architecture Agent v2: documents -> draw.io diagram")
    ap.add_argument("files", nargs="+", metavar="FILE")
    ap.add_argument("--out", metavar="DIR", help="data directory (default: DATA_DIR from settings)")
    args = ap.parse_args(argv)

    paths = [Path(f) for f in args.files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        out(f"File(s) not found: {', '.join(missing)}")
        return 2

    if settings is None:
        _load_env()
        get_settings.cache_clear()
        settings = get_settings()
    if args.out:
        settings = settings.model_copy(update={"data_dir": Path(args.out)})
    llm_factory = llm_factory or (lambda step: make_llm(settings, step))

    store, bus = JobStore(settings.jobs_dir), EventBus()
    bus.add_listener(lambda ev: (out(format_event(ev)), on_event(ev) if on_event else None))
    names = [p.name for p in paths]
    job = store.create(names)
    for p in paths:
        shutil.copyfile(p, store.job_dir(job.job_id) / "input" / p.name)
    bus.publish(job.job_id, make_event(job.job_id, "QUEUED", "info", f"Job {job.job_id} created ({len(paths)} file(s))"))

    Pipeline(settings, store, bus, llm_factory, stages).run(job.job_id)

    view = store.view(store.get(job.job_id))
    job_dir = store.job_dir(job.job_id)
    out("")
    out(f"Job {view['job_id']}: {view['status']}")
    if view["error"]:
        out(f"Error in {view['error']['stage']}: {view['error']['message']}")
    out(f"Job directory: {job_dir}")
    out("Artifacts:")
    for name in view["artifacts"]:
        out(f"  {job_dir / name}")
    report = job_dir / "report.json"
    if report.is_file():
        out(f"Report status: {json.loads(report.read_text(encoding='utf-8')).get('status')}")
    return 0 if view["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
