"""Pipeline framework: runs a list of (STAGE_NAME, fn(ctx)) stages for one job.

The stages themselves live in diagram_generator/stages.py (orchestrator). Each stage emits its own
started/completed events through `ctx.emit`; this module sets the job status per stage, times it,
adds a generic "completed" event when a stage did not emit one, converts failures into FAILED,
and always writes metadata.json. `Pipeline.run(job_id)` is blocking; call it from a worker thread.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic_core import to_jsonable_python

from diagram_generator.config import APP_VERSION, Settings, Step
from diagram_generator.contracts import HarnessRecord
from diagram_generator.harness.log import append_record, summarize
from diagram_generator.jobs import STAGE_PROGRESS, STAGES, TERMINAL, EventBus, Job, JobStore, make_event, now_iso
from diagram_generator.llm.bedrock import LLMClient, LLMError

Stage = Callable[["StageContext"], None]


class StageFailure(Exception):
    """Raised by a stage to fail the job with a user-facing message."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def _progress_after(stage: str) -> int:
    """Progress when `stage` completes = start of the next stage (capped below 100 until COMPLETED)."""
    if stage in STAGES and stage not in TERMINAL:
        nxt = STAGES[STAGES.index(stage) + 1]
        return min(STAGE_PROGRESS.get(nxt, 99), 99)
    return STAGE_PROGRESS.get(stage, 0)


class StageContext:
    def __init__(self, pipe: "Pipeline", job: Job, input_paths: list[Path], log: logging.Logger):
        self._pipe = pipe
        self._log = log
        self.job = job
        self.job_id = job.job_id
        self.job_dir = pipe.store.job_dir(job.job_id)
        self.settings = pipe.settings
        self.input_paths = input_paths
        self.state: dict[str, Any] = {}
        self.current_stage = "QUEUED"
        self.progress = 0
        self.llm_calls: list[dict] = []
        self._completed: set[str] = set()

    # --- events
    def emit(self, stage: str, kind: str, message: str, counts: Optional[dict] = None,
             progress: Optional[int] = None) -> None:
        """kind: "started" | "completed" | "info" | "warning" (the pipeline itself also uses "failed")."""
        if progress is None:
            progress = _progress_after(stage) if kind == "completed" else STAGE_PROGRESS.get(stage, self.progress)
        if stage == "FAILED":
            progress = self.progress
        else:
            progress = max(progress, self.progress)
            self.progress = progress
        if kind == "completed":
            self._completed.add(stage)
        self._log.info("[%s] %s: %s %s", stage, kind, message, counts or "")
        self._pipe.bus.publish(self.job_id, make_event(self.job_id, stage, kind, message, counts, progress))

    def harness(self, record: HarnessRecord) -> None:
        """Append to harness.log.jsonl and emit a one-line summary with the output counts."""
        append_record(self.job_dir, record)
        self.emit(self.current_stage, "info" if record.ok else "warning", summarize(record),
                  counts=dict(record.out_counts))

    # --- models
    def llm(self, step: Step) -> LLMClient:
        return self._pipe.llm_factory(step)

    def record_llm_call(self, step: str, info: dict) -> None:
        self.llm_calls.append({"step": step, **info})
        total_in = sum(c.get("input_tokens") or 0 for c in self.llm_calls)
        total_out = sum(c.get("output_tokens") or 0 for c in self.llm_calls)
        self.emit(self.current_stage, "info",
                  f"{step}: {info.get('input_tokens') or 0:,} in / {info.get('output_tokens') or 0:,} out tokens "
                  f"in {info.get('seconds') or 0:.1f} s; job total {total_in:,} in / {total_out:,} out")

    # --- files
    def write_json(self, name: str, obj: Any) -> Path:
        path = self.job_dir / name
        path.write_text(json.dumps(to_jsonable_python(obj), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.job_dir / name
        path.write_text(text, encoding="utf-8")
        return path


class Pipeline:
    def __init__(self, settings: Settings, store: JobStore, bus: EventBus,
                 llm_factory: Callable[[Step], LLMClient], stages: Optional[list[tuple[str, Stage]]] = None):
        self.settings = settings
        self.store = store
        self.bus = bus
        self.llm_factory = llm_factory
        self.stages = stages

    # ---------------------------------------------------------------- helpers
    def _logger(self, job_id: str) -> tuple[logging.Logger, logging.Handler]:
        lg = logging.getLogger(f"aa.job.{job_id}")
        lg.setLevel(logging.INFO)
        h = logging.FileHandler(self.store.job_dir(job_id) / "logs.txt", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(h)
        return lg, h

    def _fail(self, ctx: StageContext, stage: str, message: str) -> None:
        if stage in TERMINAL or stage == "QUEUED":
            stage = "INGEST"
        self.store.update(ctx.job, status="FAILED", error={"stage": stage, "message": message})
        ctx.emit("FAILED", "failed", message)

    def _add_timing(self, job: Job, stage: str, seconds: float) -> None:
        t = dict(job.timings)
        t[stage.lower()] = round(t.get(stage.lower(), 0.0) + seconds, 3)
        self.store.update(job, timings=t)

    def _write_metadata(self, ctx: StageContext, status: Optional[str] = None) -> None:
        job, s = ctx.job, self.settings
        calls = ctx.llm_calls
        models: dict[str, str] = {}
        for c in calls:
            models[c["step"]] = c.get("model_id")
        data = {
            "job_id": job.job_id,
            "filenames": job.filenames,
            "status": status or job.status,
            "error": job.error,
            "report_status": job.report_status,
            "app_version": APP_VERSION,
            "created_at": job.created_at,
            "finished_at": now_iso(),
            "configured_models": {"extractor": s.extractor_model_id, "assumer": s.assumer_model_id,
                                  "coder": s.coder_model_id, "fallback": s.fallback_model_id},
            "models_used": models,
            "fallbacks": [{"step": c["step"], "model_id": c.get("model_id"), "primary_error": c.get("primary_error")}
                          for c in calls if c.get("fallback_used")],
            "tokens": {"input": sum(c.get("input_tokens") or 0 for c in calls),
                       "output": sum(c.get("output_tokens") or 0 for c in calls)},
            "llm_calls": calls,
            "timings": dict(job.timings),
        }
        (ctx.job_dir / "metadata.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ---------------------------------------------------------------- run
    def run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None or job.status in TERMINAL:
            return
        log, handler = self._logger(job_id)
        ctx = StageContext(self, job, self.store.input_paths(job_id), log)
        try:
            self._run_stages(ctx)
        except StageFailure as f:
            self._fail(ctx, f.stage, f.message)
        except LLMError as e:
            self._fail(ctx, ctx.current_stage, f"Model call failed ({e.kind}): {e}")
        except Exception as e:  # unexpected
            log.exception("Unexpected pipeline error")
            self._fail(ctx, ctx.current_stage, f"Internal error: {type(e).__name__}: {e}")
        finally:
            try:
                self._write_metadata(ctx)
            except Exception:
                log.exception("Could not write metadata.json")
            log.removeHandler(handler)
            handler.close()
            self.bus.finish(job_id)

    def _run_stages(self, ctx: StageContext) -> None:
        stages = self.stages
        if stages is None:
            from diagram_generator.stages import default_stages  # orchestrator-owned, imported lazily

            stages = default_stages()
        if not ctx.input_paths:
            raise StageFailure("INGEST", "Uploaded files are missing from the job directory")
        for name, fn in stages:
            ctx.current_stage = name
            self.store.update(ctx.job, status=name)
            t0 = time.monotonic()
            try:
                fn(ctx)
            finally:
                self._add_timing(ctx.job, name, time.monotonic() - t0)
            if name not in ctx._completed:
                ctx.emit(name, "completed", f"{name} done")
        report = ctx.job_dir / "report.json"
        report_status = None
        if report.is_file():
            try:
                report_status = json.loads(report.read_text(encoding="utf-8")).get("status")
            except Exception:
                pass
        ctx.job.report_status = report_status
        self._write_metadata(ctx, status="COMPLETED")  # before COMPLETED so the artifact exists when clients see it
        self.store.update(ctx.job, status="COMPLETED", report_status=report_status)
        ctx.current_stage = "COMPLETED"
        ctx.emit("COMPLETED", "completed", f"Diagram ready ({report_status})" if report_status else "Done",
                 progress=100)
