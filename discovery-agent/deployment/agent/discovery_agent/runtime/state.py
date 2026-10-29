"""Run configuration, shared run state, checkpoints and status file for one orchestrated run."""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..analysis import pipeline
from ..data.documents import Document
from ..data.storage import Storage
from ..rendering import readme, word

# The only legal order. write_narrative is non-fatal but still occupies its place in the sequence.
STEPS = [
    "load_documents",
    "extract_explicit_needs",
    "find_hidden_signals",
    "merge_and_rank",
    "write_narrative",
    "render_outputs",
    "publish_outputs",
]
PREREQS = {
    "load_documents": [],
    "extract_explicit_needs": ["load_documents"],
    "find_hidden_signals": ["load_documents", "extract_explicit_needs"],
    "merge_and_rank": ["extract_explicit_needs", "find_hidden_signals"],
    "write_narrative": ["merge_and_rank"],
    "render_outputs": ["merge_and_rank", "write_narrative"],
    "publish_outputs": ["render_outputs"],
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$")


class PreconditionError(Exception):
    """A tool was called before its prerequisite steps completed."""


@dataclass
class RunConfig:
    engagement: str
    run_id: str
    input_prefix: str
    output_prefix: str
    narrative: bool = True
    resume: bool = False
    instructions: str = ""  # optional per-run reviewer guidance (user-driven); recorded for traceability
    clarify: bool = False   # opt-in: pause after merge to ask the reviewer about gaps/conflicts (#2)
    resume_with_answers: bool = False  # phase 2: resume a paused run, folding in the reviewer's answers

    @classmethod
    def from_payload(cls, payload: dict) -> "RunConfig":
        engagement = str(payload.get("engagement", "")).strip()
        if not _SAFE_NAME.match(engagement):
            raise ValueError("payload.engagement is required (letters, digits, space, . _ -)")
        run_id = str(payload.get("run_id") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"))
        if not _SAFE_NAME.match(run_id):
            raise ValueError("payload.run_id has invalid characters")
        folder = engagement.replace(" ", "_")
        options = payload.get("options", {}) or {}
        instructions = str(options.get("instructions") or payload.get("instructions") or "").strip()
        if len(instructions) > 4000:  # a guard: this is guidance, not a document
            raise ValueError("payload.instructions is too long (max 4000 characters)")
        slash = lambda p: p if p.endswith("/") else p + "/"  # noqa: E731
        return cls(
            engagement=engagement,
            run_id=run_id,
            input_prefix=slash(payload.get("input_prefix") or f"input/{folder}/"),
            output_prefix=slash(payload.get("output_prefix") or f"output/{folder}/"),
            narrative=bool(options.get("narrative", True)),
            resume=bool(options.get("resume", False)),
            instructions=instructions,
            clarify=bool(options.get("clarify", False)),
            resume_with_answers=bool(options.get("resume_with_answers", False)),
        )

    @property
    def work_prefix(self) -> str:
        return f"work/{self.engagement.replace(' ', '_')}/{self.run_id}/"

    @property
    def output_dir(self) -> str:
        return f"{self.output_prefix}{self.run_id}/"


@dataclass
class Stages:
    """The callables the tools use. Real implementations by default; tests substitute stubs."""

    prepare_corpus: Callable = pipeline.prepare_corpus  # builds the corpus, trimming small talk when over budget
    build_corpus: Callable = pipeline.build_corpus
    extract_explicit: Callable = pipeline.extract_explicit
    find_hidden: Callable = pipeline.find_hidden
    merge_report: Callable = pipeline.merge_report
    write_narrative: Callable = pipeline.write_narrative
    resolve_clarifications: Callable = pipeline.resolve_clarifications
    render_markdown: Callable = readme.render_markdown
    markdown_to_docx: Callable = word.markdown_to_docx


@dataclass
class RunState:
    config: RunConfig
    storage: Storage
    stages: Stages = field(default_factory=Stages)
    docs: list[Document] = field(default_factory=list)
    corpus: str = ""
    explicit: dict | None = None
    hidden: dict | None = None
    report: dict | None = None
    narrative: dict | None = None
    files: list[str] = field(default_factory=list)
    output_keys: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    step_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed: tuple[str, str] | None = None  # (step, reason)

    # ── ordering ────────────────────────────────────────────────────────────
    def require(self, step: str) -> None:
        missing = [p for p in PREREQS[step] if p not in self.completed]
        if missing:
            raise PreconditionError(f"{step} requires {', '.join(missing)} first")

    def mark_done(self, step: str, **info: Any) -> None:
        if step not in self.completed:
            self.completed.append(step)
        self.step_info[step] = {"status": "done", **info}

    def fail(self, step: str, reason: str) -> None:
        self.failed = (step, reason)
        self.step_info[step] = {"status": "failed", "reason": reason}

    # ── checkpoints (work area, one JSON per step) ──────────────────────────
    def _work_key(self, name: str) -> str:
        return f"{self.config.work_prefix}{name}"

    def checkpoint(self, step: str, payload: Any) -> None:
        self.storage.write_bytes(self._work_key(f"{step}.json"), json.dumps(payload, ensure_ascii=False).encode())

    def restore(self, step: str) -> Any | None:
        key = self._work_key(f"{step}.json")
        return json.loads(self.storage.read_bytes(key)) if self.storage.exists(key) else None

    def write_work_file(self, name: str, data: bytes) -> None:
        self.storage.write_bytes(self._work_key(f"files/{name}"), data)

    def read_work_file(self, name: str) -> bytes:
        return self.storage.read_bytes(self._work_key(f"files/{name}"))

    # ── clarification round (#2): questions written in phase 1, answers read in phase 2 ─────────────────────────
    def write_clarifications(self, questions: list[dict]) -> None:
        """Phase 1 writes the gaps/conflicts the reviewer should resolve."""
        self.storage.write_bytes(self._work_key("clarifications.json"),
                                 json.dumps({"questions": questions}, ensure_ascii=False).encode())

    def read_clarifications(self) -> list[dict]:
        key = self._work_key("clarifications.json")
        if not self.storage.exists(key):
            return []
        return json.loads(self.storage.read_bytes(key)).get("questions", [])

    def write_answers(self, answers: dict) -> None:
        """The API writes the reviewer's answers {question_id: answer_text} before resuming phase 2."""
        self.storage.write_bytes(self._work_key("answers.json"),
                                 json.dumps({"answers": answers}, ensure_ascii=False).encode())

    def read_answers(self) -> dict:
        key = self._work_key("answers.json")
        if not self.storage.exists(key):
            return {}
        return json.loads(self.storage.read_bytes(key)).get("answers", {})

    # ── status file ─────────────────────────────────────────────────────────
    def status_doc(self, state: str, error: str | None = None, questions: list[dict] | None = None) -> dict:
        doc = {
            "run_id": self.config.run_id,
            "engagement": self.config.engagement,
            "state": state,  # running | awaiting_clarification | succeeded | failed
            "instructions": self.config.instructions,  # traceability: what the user asked for this run
            "completed_steps": list(self.completed),
            "steps": {s: self.step_info[s] for s in STEPS if s in self.step_info},
            "error": error,
            "output_keys": list(self.output_keys),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if state == "awaiting_clarification":
            doc["clarification"] = {"questions": questions or [], "answered": False}
        return doc

    def write_status(self, state: str = "running", error: str | None = None, questions: list[dict] | None = None) -> dict:
        doc = self.status_doc(state, error, questions)
        self.storage.write_bytes(f"{self.config.output_dir}status.json", json.dumps(doc, indent=2).encode())
        return doc
