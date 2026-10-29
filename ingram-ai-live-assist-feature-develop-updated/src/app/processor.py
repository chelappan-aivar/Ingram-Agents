"""Background worker that runs the agent on turns as they arrive.

The entry point stores a turn and calls ``schedule``. One worker per call then
drains pending turns in batches: turns that arrive during a run go into the
next one. The runtime routes a session id to one microVM, so the worker is the
only writer of that call's session state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from app.assist_config import AssistConfig, InvalidAssistConfigError
from functools import partial

from app.account_store import AccountStore
from app.call_memory import load_prior_context, remember_call
from app.events import STATUS_ENDED
from app.guidance import guidance_for_meta
from app.memory import LongTermMemory, MemoryScope
from app.minutes import generate_minutes
from app.orchestrator import CallMemory, ProcessResult, process_turns
from app.prequestions import generate_prequestions
from app.session_store import SessionStore
from app.timeline_store import TimelineStore
from app.transcript import TranscriptChunk

MAX_TURNS_PER_RUN = 20
DEFAULT_DEBOUNCE_SECONDS = float(os.environ.get("PROCESS_DEBOUNCE_SECONDS", "1.0"))
MAX_ERROR_CHARS = 500

logger = logging.getLogger(__name__)


class TaskTracker(Protocol):
    """Reports background work to the runtime (BedrockAgentCoreApp implements this)."""

    def add_async_task(self, name: str, metadata: dict | None = None) -> int: ...

    def complete_async_task(self, task_id: int) -> bool: ...


class _Outcome(Enum):
    IDLE = "idle"
    RAN = "ran"
    FAILED = "failed"


class Processor:
    def __init__(
        self,
        *,
        timeline: TimelineStore,
        store: SessionStore,
        tracker: TaskTracker | None = None,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        run=process_turns,
        minutes=generate_minutes,
        memory: LongTermMemory | None = None,
        accounts: AccountStore | None = None,
        prequestions=generate_prequestions,
    ) -> None:
        self._timeline = timeline
        self._store = store
        self._tracker = tracker
        self._debounce = debounce_seconds
        self._run = run
        self._minutes = minutes
        self._memory = memory
        self._accounts = accounts
        self._prequestions = prequestions
        self._prep_tasks: set[asyncio.Task] = set()
        self._workers: dict[str, asyncio.Task] = {}
        self._dirty: set[str] = set()
        self._regenerate: set[str] = set()

    def schedule(self, session_id: str, *, regenerate_minutes: bool = False) -> None:
        """Ensure pending turns for a call get processed. Call from the event loop.

        Once an ended call is drained, the worker writes its minutes (once, unless
        ``regenerate_minutes`` asks for a new version).
        """
        if regenerate_minutes:
            self._regenerate.add(session_id)
        if session_id in self._workers:
            self._dirty.add(session_id)  # The running worker re-checks before exiting.
            return
        self._workers[session_id] = asyncio.get_running_loop().create_task(self._work(session_id))

    def prepare_questions(self, session_id: str) -> None:
        """Write the call's pre-questions in the background. Call from the event loop.

        Independent of the turn worker: it never delays a turn. It is reported to the runtime as async work, so
        the session stays alive until it finishes. Each call is prepared at most once (see the claim).
        """
        task = asyncio.get_running_loop().create_task(self._prepare_questions(session_id))
        self._prep_tasks.add(task)
        task.add_done_callback(self._prep_tasks.discard)

    async def _prepare_questions(self, session_id: str) -> None:
        task_id = (
            self._tracker.add_async_task("prepare_questions", {"session_id": session_id}) if self._tracker else None
        )
        try:
            await self._prequestions(session_id, timeline=self._timeline)
        except Exception:  # noqa: BLE001 - pre-questions are a help; a failure must not touch the call.
            logger.exception("pre-questions crashed session=%r", session_id)
        finally:
            if task_id is not None:
                self._tracker.complete_async_task(task_id)

    def is_running(self, session_id: str) -> bool:
        return session_id in self._workers

    async def wait_idle(self) -> None:
        """Wait until no worker is running. Used by tests and shutdown."""
        while self._workers or self._prep_tasks:
            await asyncio.gather(*self._workers.values(), *self._prep_tasks, return_exceptions=True)

    async def _work(self, session_id: str) -> None:
        task_id = (
            self._tracker.add_async_task("process_turns", {"session_id": session_id})
            if self._tracker
            else None
        )
        try:
            if self._debounce > 0:
                await asyncio.sleep(self._debounce)  # Coalesce a burst of turns.
            outcome = await self._drain(session_id)
            if outcome is _Outcome.FAILED and self._has_ended(session_id):
                # No later event will retry an ended call's last batch, so retry once now.
                outcome = await self._drain(session_id)
            # Awaiting past this point is safe only for ended calls, which take no new turns.
            while outcome is _Outcome.IDLE and self._has_ended(session_id):
                regenerate = session_id in self._regenerate
                self._regenerate.discard(session_id)
                status = await self._minutes(
                    session_id, timeline=self._timeline, store=self._store, regenerate=regenerate
                )
                if status == "ok":
                    await self._remember(session_id)
                if session_id not in self._regenerate:  # Requested again while generating.
                    break
        except Exception:
            logger.exception("worker crashed session=%r", session_id)
        finally:
            # No await between the last pending-turns check and this removal, so
            # a turn stored meanwhile either was seen or will start a new worker.
            self._workers.pop(session_id, None)
            self._dirty.discard(session_id)
            if task_id is not None:
                self._tracker.complete_async_task(task_id)

    async def _drain(self, session_id: str) -> _Outcome:
        """Run batches until no turns are pending or a run fails."""
        while True:
            self._dirty.discard(session_id)
            outcome = await self._run_once(session_id)
            if outcome is _Outcome.RAN:
                continue
            # IDLE: done unless a turn was scheduled meanwhile. FAILED: stop; the
            # next event for a live call retries the same batch.
            if outcome is _Outcome.IDLE and session_id in self._dirty:
                continue
            return outcome

    async def _remember(self, session_id: str) -> None:
        """Write the call to long-term memory. Failures are logged; the minutes stand."""
        if self._memory is None:
            return
        try:
            written = await asyncio.to_thread(
                remember_call, session_id, timeline=self._timeline, store=self._store, memory=self._memory
            )
        except Exception:  # noqa: BLE001
            logger.exception("memory write failed session=%r", session_id)
            return
        if written:
            logger.info("memory written session=%r", session_id)

    def _call_memory(self, session_id: str, meta: dict) -> CallMemory | None:
        scope = MemoryScope.from_meta(meta)
        if scope is None:
            return None
        memory = self._memory
        return CallMemory(
            scope=scope,
            load_prior=partial(load_prior_context, session_id, scope, memory=memory, timeline=self._timeline,
                               accounts=self._accounts),
            search=(
                (lambda sc, query, level, top_k: [f.to_dict() for f in memory.search(sc, query, level, top_k)])
                if memory is not None else None
            ),
        )

    def _has_ended(self, session_id: str) -> bool:
        meta = self._timeline.get_meta(session_id) or {}
        return meta.get("status") == STATUS_ENDED

    async def _run_once(self, session_id: str) -> _Outcome:
        state = self._store.get(session_id)
        cursor = state.last_seq if state else 0
        pending = self._timeline.pending_turns(session_id, cursor, MAX_TURNS_PER_RUN)
        if not pending:
            return _Outcome.IDLE

        meta = self._timeline.get_meta(session_id) or {}
        config = _config_from(meta)
        first_seq, last_seq = pending[0]["seq"], pending[-1]["seq"]
        base = {
            "first_seq": first_seq,
            "last_seq": last_seq,
            "turn_count": len(pending),
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "settings": config.to_dict(),
        }
        started = time.monotonic()

        try:
            result, _ = await self._run(
                session_id=session_id,
                turns=[TranscriptChunk.from_timeline_item(t) for t in pending],
                store=self._store,
                last_seq=last_seq,
                assist_config=config,
                call_memory=self._call_memory(session_id, meta),
                preset_opportunity_type=meta.get("preset_opportunity_type"),
                guidance=guidance_for_meta(meta),
            )
        except Exception as e:
            logger.exception("run failed session=%r seq=%d-%d", session_id, first_seq, last_seq)
            self._timeline.put_run(session_id, first_seq, {
                **base,
                "status": "error",
                "duration_ms": _elapsed_ms(started),
                "error": f"{type(e).__name__}: {e}"[:MAX_ERROR_CHARS],
            })
            return _Outcome.FAILED

        record = {**base, **_result_fields(result), "status": "ok", "duration_ms": _elapsed_ms(started)}
        self._timeline.put_run(session_id, first_seq, record)
        logger.info(
            "run done session=%r run=%d seq=%d-%d turns=%d ms=%d captured=%d suggestions=%d",
            session_id, result.run_number, first_seq, last_seq, len(pending),
            record["duration_ms"], len(result.captured_fields), len(result.suggestions),
        )
        return _Outcome.RAN


def _config_from(meta: dict) -> AssistConfig:
    try:
        return AssistConfig.from_payload(meta.get("assist_config"))
    except InvalidAssistConfigError:
        logger.warning("invalid stored assist_config, using defaults")
        return AssistConfig()


def _result_fields(result: ProcessResult) -> dict[str, Any]:
    return {
        "run_number": result.run_number,
        "narration": result.narration,
        "suggestions": [asdict(s) for s in result.suggestions],
        "captured_fields": result.captured_fields,
        "added_gaps": result.added_gaps,
        "dismissed_gaps": result.dismissed_gaps,
        "opportunity_type": result.opportunity_type,
        "opportunity_type_just_resolved": result.opportunity_type_just_resolved,
        "memory_lookups": result.memory_lookups,
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
