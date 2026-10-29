"""Filesystem job store, stage constants and in-memory event bus."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

STAGES = ["QUEUED", "INGEST", "EXTRACTOR", "HARNESS_1", "ASSUMER", "HARNESS_2",
          "DIAGRAM_CODER", "HARNESS_3", "EXPORT", "PACKAGE", "COMPLETED", "FAILED"]
TERMINAL = {"COMPLETED", "FAILED"}

# Overall progress (0-100) at the *start* of each stage; a stage "completed" event reports the next stage's start.
STAGE_PROGRESS = {
    "QUEUED": 0, "INGEST": 2, "EXTRACTOR": 8, "HARNESS_1": 40, "ASSUMER": 46, "HARNESS_2": 66,
    "DIAGRAM_CODER": 72, "HARNESS_3": 86, "EXPORT": 93, "PACKAGE": 97, "COMPLETED": 100, "FAILED": 100,
}

ARTIFACT_TYPES = {
    "diagram.png": "image/png",
    "diagram.svg": "image/svg+xml",
    "diagram.drawio": "application/xml",
    "assumptions.json": "application/json",
    "unresolved.json": "application/json",
    "contradictions.json": "application/json",
    "spec.json": "application/json",
    "extraction.json": "application/json",
    "layout.json": "application/json",
    "report.json": "application/json",
    "metadata.json": "application/json",
    "harness.log.jsonl": "application/x-ndjson",
    "sources.json": "application/json",
    "logs.txt": "text/plain",
}
ARTIFACT_NAMES = list(ARTIFACT_TYPES)

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id or ""))


def make_event(job_id: str, stage: str, kind: str, message: str, counts: Optional[dict] = None,
               progress: int = 0) -> dict:
    """The event payload published on the bus."""
    return {"job_id": job_id, "stage": stage, "kind": kind, "message": message,
            "counts": counts or {}, "progress": progress, "ts": now_iso()}


@dataclass
class Job:
    job_id: str
    filenames: list[str] = field(default_factory=list)
    status: str = "QUEUED"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    error: Optional[dict] = None
    timings: dict[str, float] = field(default_factory=dict)
    report_status: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "job_id": self.job_id, "filenames": self.filenames, "status": self.status,
            "created_at": self.created_at, "updated_at": self.updated_at, "error": self.error,
            "timings": self.timings, "report_status": self.report_status,
        }


class JobStore:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self.load_existing()

    # --- paths
    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def input_paths(self, job_id: str) -> list[Path]:
        d = self.job_dir(job_id) / "input"
        return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []

    # --- persistence
    def _save(self, job: Job) -> None:
        d = self.job_dir(job.job_id)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job.to_json(), indent=2), encoding="utf-8")
        tmp.replace(d / "job.json")

    def load_existing(self) -> None:
        for d in self.jobs_dir.iterdir():
            jf = d / "job.json"
            if not (d.is_dir() and is_valid_job_id(d.name) and jf.exists()):
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                job = Job(
                    job_id=data["job_id"], filenames=list(data.get("filenames") or []),
                    status=data.get("status", "FAILED"),
                    created_at=data.get("created_at", now_iso()), updated_at=data.get("updated_at", now_iso()),
                    error=data.get("error"), timings=data.get("timings", {}) or {},
                    report_status=data.get("report_status"),
                )
            except Exception:
                log.warning("Skipping unreadable job.json in %s", d.name)
                continue
            if job.status not in TERMINAL:
                stage = job.status
                job.status = "FAILED"
                job.error = {"stage": stage, "message": "Job interrupted by a server restart"}
                job.updated_at = now_iso()
                self._jobs[job.job_id] = job
                self._save(job)
            else:
                self._jobs[job.job_id] = job

    # --- API
    def create(self, filenames: list[str]) -> Job:
        job = Job(job_id=uuid.uuid4().hex, filenames=list(filenames))
        d = self.job_dir(job.job_id)
        (d / "input").mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._jobs[job.job_id] = job
            self._save(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        if not is_valid_job_id(job_id):
            return None
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def update(self, job: Job, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(job, k, v)
            job.updated_at = now_iso()
            self._save(job)

    def artifacts(self, job_id: str) -> list[str]:
        d = self.job_dir(job_id)
        return [n for n in ARTIFACT_NAMES if (d / n).is_file() and (d / n).stat().st_size > 0]

    def view(self, job: Job) -> dict:
        v = job.to_json()
        v["artifacts"] = self.artifacts(job.job_id)
        return v


class EventBus:
    """Per-job history + live subscribers. publish() is thread-safe (pipeline runs in a thread);
    subscribers are asyncio queues bound to the event loop that subscribed. `add_listener` registers a
    plain callable (used by the CLI) that is called synchronously from the publishing thread."""

    DONE = object()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, list[dict]] = {}
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._finished: set[str] = set()
        self._listeners: list[Any] = []

    def add_listener(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def publish(self, job_id: str, event: dict) -> None:
        with self._lock:
            self._history.setdefault(job_id, []).append(event)
            subs = list(self._subs.get(job_id, []))
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:
                log.exception("Event listener failed")
        for loop, q in subs:
            self._push(loop, q, event)

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._finished.add(job_id)
            subs = list(self._subs.get(job_id, []))
        for loop, q in subs:
            self._push(loop, q, self.DONE)

    @staticmethod
    def _push(loop, q, item) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:  # loop closed
            pass

    def history(self, job_id: str) -> list[dict]:
        with self._lock:
            return list(self._history.get(job_id, []))

    def subscribe(self, job_id: str, loop: asyncio.AbstractEventLoop) -> tuple[list[dict], asyncio.Queue, bool]:
        """Atomically snapshot history and register a queue. Returns (history, queue, already_finished)."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            hist = list(self._history.get(job_id, []))
            finished = job_id in self._finished
            if not finished:
                self._subs.setdefault(job_id, []).append((loop, q))
        return hist, q, finished

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[job_id] = [(l, x) for (l, x) in self._subs.get(job_id, []) if x is not q]
