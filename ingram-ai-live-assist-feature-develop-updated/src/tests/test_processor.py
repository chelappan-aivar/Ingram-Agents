import asyncio

from app.assist_config import AssistConfig
from app.events import SessionStart, Turn
from app.orchestrator import ProcessResult, Suggestion
from app.processor import MAX_TURNS_PER_RUN, Processor
from app.session_store import SessionState


class FakeRun:
    """Stands in for process_turns: records batches, saves the cursor, optionally waits or fails."""

    def __init__(self, store, *, gate: asyncio.Event | None = None, fail_times: int = 0):
        self.store = store
        self.batches: list[list[str]] = []
        self.configs: list[AssistConfig] = []
        self.gate = gate
        self.fail_times = fail_times

    async def __call__(self, *, session_id, turns, store, last_seq, assist_config, **_kw):
        self.batches.append([t.text for t in turns])
        self.configs.append(assist_config)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("bedrock throttled")
        state = store.get(session_id) or SessionState.new(session_id)
        state.chunk_count += 1
        state.last_seq = last_seq
        store.save(state)
        result = ProcessResult(
            session_id=session_id, opportunity_type=None, opportunity_type_just_resolved=False,
            captured_fields=["scope"], gaps=[],
            suggestions=[Suggestion("Where does it run?", "current_environment", "Hosting", "why")],
            narration=["Captured scope."], run_number=state.chunk_count,
        )
        return result, None


class FakeTracker:
    def __init__(self):
        self.open: set[int] = set()
        self.started = 0

    def add_async_task(self, name, metadata=None):
        self.started += 1
        self.open.add(self.started)
        return self.started

    def complete_async_task(self, task_id):
        self.open.discard(task_id)
        return True


def _setup(timeline, session_id, config=AssistConfig()):
    timeline.start_session(SessionStart(session_id, "recording", None, config))


def _add(timeline, session_id, text):
    return timeline.append_turn(Turn(session_id, "recording", "cx", text))


async def test_burst_is_processed_in_one_run(timeline, session_store, session_id):
    _setup(timeline, session_id, AssistConfig(scope="guided"))
    run = FakeRun(session_store)
    tracker = FakeTracker()
    proc = Processor(timeline=timeline, store=session_store, tracker=tracker, debounce_seconds=0.01, run=run)

    for i in range(5):
        _add(timeline, session_id, f"t{i}")
        proc.schedule(session_id)
    await proc.wait_idle()

    assert run.batches == [["t0", "t1", "t2", "t3", "t4"]]
    assert run.configs[0].scope == "guided"
    assert tracker.started == 1 and tracker.open == set()

    record = next(i for i in timeline.items(session_id) if i["sk"] == "run#000001")
    assert record["status"] == "ok" and record["last_seq"] == 5 and record["turn_count"] == 5
    assert record["suggestions"][0]["question"] == "Where does it run?"
    assert record["narration"] == ["Captured scope."]
    assert record["settings"]["scope"] == "guided"


async def test_turns_during_a_run_go_to_the_next_run(timeline, session_store, session_id):
    _setup(timeline, session_id)
    gate = asyncio.Event()
    run = FakeRun(session_store, gate=gate)
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run)

    _add(timeline, session_id, "first")
    proc.schedule(session_id)
    await asyncio.sleep(0.01)  # Worker is now inside the run, waiting on the gate.
    _add(timeline, session_id, "second")
    _add(timeline, session_id, "third")
    proc.schedule(session_id)
    gate.set()
    await proc.wait_idle()

    assert run.batches == [["first"], ["second", "third"]]
    assert session_store.require(session_id).last_seq == 3


async def test_batches_are_capped(timeline, session_store, session_id):
    _setup(timeline, session_id)
    run = FakeRun(session_store)
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run)
    for i in range(MAX_TURNS_PER_RUN + 3):
        _add(timeline, session_id, f"t{i}")
    proc.schedule(session_id)
    await proc.wait_idle()

    assert [len(b) for b in run.batches] == [MAX_TURNS_PER_RUN, 3]


async def test_failed_run_is_recorded_and_retried_on_next_event(timeline, session_store, session_id):
    _setup(timeline, session_id)
    run = FakeRun(session_store, fail_times=1)
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run)

    _add(timeline, session_id, "a")
    proc.schedule(session_id)
    await proc.wait_idle()

    failed = next(i for i in timeline.items(session_id) if i["sk"] == "run#000001")
    assert failed["status"] == "error" and "bedrock throttled" in failed["error"]
    assert session_store.get(session_id) is None  # Cursor not advanced.

    _add(timeline, session_id, "b")
    proc.schedule(session_id)
    await proc.wait_idle()

    assert run.batches == [["a"], ["a", "b"]]
    retried = next(i for i in timeline.items(session_id) if i["sk"] == "run#000001")
    assert retried["status"] == "ok" and retried["last_seq"] == 2


async def test_nothing_pending_is_a_no_op(timeline, session_store, session_id):
    _setup(timeline, session_id)
    run = FakeRun(session_store)
    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run)
    proc.schedule(session_id)
    await proc.wait_idle()
    assert run.batches == [] and not proc.is_running(session_id)
