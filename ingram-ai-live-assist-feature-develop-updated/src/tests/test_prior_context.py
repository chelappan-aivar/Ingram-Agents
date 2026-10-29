"""Long-term memory inside agent runs: prior context in the prompt, the recall tool,
preset opportunity types, and the processor's end-of-call write."""

from __future__ import annotations

from app.assist_config import AssistConfig
from app.events import SessionStart, Turn
from app.memory import MemoryScope, facts_namespace
from app.orchestrator import CallMemory, process_turns
from app.processor import Processor
from app.tools import MAX_RECALLS_PER_RUN
from app.transcript import TranscriptChunk
from tests.fake_model import FakeModel, ScriptedToolCall
from tests.test_orchestrator import _patch_agent_factory

SCOPE = MemoryScope("acme", "erp")
PRIOR = {
    "account": ["Acme runs VMware in Dallas"],
    "opportunity": ["ERP move must finish by June"],
    "previous_calls": [{"session_id": "s0", "title": "Kickoff", "ended_at": "2026-10-10T10:00:00Z",
                        "summary": "First look at the ERP move.", "decisions": ["Start with finance."],
                        "open_questions": ["Target region?"]}],
}


class Memory:
    """CallMemory stand-in: counts prior-context loads and searches."""

    def __init__(self, prior=PRIOR, facts=None, fail=False):
        self.prior, self.facts, self.fail = prior, facts or [], fail
        self.loads = 0
        self.searches: list[tuple] = []

    def load(self):
        self.loads += 1
        return self.prior

    def search(self, scope, query, level, top_k):
        self.searches.append((scope, query, level, top_k))
        if self.fail:
            raise ConnectionError("memory down")
        return [f for f in self.facts if level in ("both", f["level"])]

    def call_memory(self, with_search=True):
        return CallMemory(scope=SCOPE, load_prior=self.load, search=self.search if with_search else None)


async def _run(session_store, monkeypatch, *, sid="m1", calls=(), memory=None, config=None, prompts=None,
               preset=None):
    tool_names: list = []
    _patch_agent_factory(monkeypatch, FakeModel(tool_calls=list(calls)), prompts)
    import app.orchestrator as orch

    original = orch.build_tools

    def spy(**kw):
        tools = original(**kw)
        tool_names.append([t.tool_name for t in tools])
        return tools

    monkeypatch.setattr(orch, "build_tools", spy)
    result, _ = await process_turns(
        session_id=sid, turns=[TranscriptChunk("cx", "We need to move ERP.", seq=1)], store=session_store,
        assist_config=config, call_memory=memory.call_memory() if memory else None,
        preset_opportunity_type=preset,
    )
    return result, tool_names[-1]


async def test_prior_context_is_loaded_once_and_in_the_prompt(session_store, monkeypatch):
    mem, prompts = Memory(), []
    await _run(session_store, monkeypatch, memory=mem, prompts=prompts)
    await _run(session_store, monkeypatch, memory=mem, prompts=prompts)

    assert mem.loads == 1
    assert session_store.require("m1").prior_context == PRIOR
    prompt = prompts[-1]
    assert "Known from earlier calls with this customer" in prompt
    assert "- Acme runs VMware in Dallas" in prompt and "- Decided: Start with finance." in prompt
    assert "Never record a field as captured from this section" in prompt
    assert "confirms it instead of asking from scratch" in prompt  # Default mode: confirm.


async def test_unavailable_prior_context_is_retried_next_run(session_store, monkeypatch):
    mem = Memory(prior=None)
    await _run(session_store, monkeypatch, memory=mem)
    mem.prior = PRIOR
    await _run(session_store, monkeypatch, memory=mem)
    assert mem.loads == 2 and session_store.require("m1").prior_context == PRIOR


async def test_skip_mode_and_off_mode(session_store, monkeypatch):
    prompts = []
    await _run(session_store, monkeypatch, sid="skip", memory=Memory(), prompts=prompts,
               config=AssistConfig(use_prior_context="skip"))
    assert "Skip topics it already covers." in prompts[-1]

    mem = Memory()
    _, tools = await _run(session_store, monkeypatch, sid="off", memory=mem, prompts=prompts,
                          config=AssistConfig(use_prior_context="off"))
    assert mem.loads == 0 and "Known from earlier calls" not in prompts[-1]
    assert "recall_prior_context" not in tools


async def test_no_account_means_no_memory_section_or_tool(session_store, monkeypatch):
    prompts = []
    _, tools = await _run(session_store, monkeypatch, prompts=prompts)
    assert "recall_prior_context" not in tools and "Known from earlier calls" not in prompts[-1]


async def test_recall_tool_searches_this_calls_scope_and_skips_known_facts(session_store, monkeypatch):
    facts = [{"level": "account", "text": "Acme runs VMware in Dallas"},  # Already in the prompt.
             {"level": "account", "text": "Acme has a 3-year Oracle licence"}]
    mem = Memory(facts=facts)
    result, tools = await _run(session_store, monkeypatch, memory=mem, calls=[
        ScriptedToolCall("recall_prior_context", {"query": "licences", "level": "account"}, "r1"),
    ])

    assert "recall_prior_context" in tools
    scope, query, level, _ = mem.searches[0]
    assert scope == SCOPE and (query, level) == ("licences", "account")
    assert result.memory_lookups == [{"query": "licences", "level": "account", "hits": 1, "ok": True,
                                      "ms": result.memory_lookups[0]["ms"]}]


async def test_recall_tool_is_capped_per_run(session_store, monkeypatch):
    mem = Memory()
    calls = [ScriptedToolCall("recall_prior_context", {"query": f"q{i}"}, f"r{i}")
             for i in range(MAX_RECALLS_PER_RUN + 1)]
    result, _ = await _run(session_store, monkeypatch, memory=mem, calls=calls)
    assert len(mem.searches) == MAX_RECALLS_PER_RUN and len(result.memory_lookups) == MAX_RECALLS_PER_RUN


async def test_recall_failure_does_not_fail_the_run(session_store, monkeypatch):
    mem = Memory(fail=True)
    result, _ = await _run(session_store, monkeypatch, memory=mem, calls=[
        ScriptedToolCall("recall_prior_context", {"query": "hosting"}, "r1"),
        ScriptedToolCall("record_captured_field",
                         {"field_id": "scope", "value": "ERP", "speaker": "cx", "evidence_seqs": [1]}, "c1"),
    ])
    assert result.memory_lookups[0]["ok"] is False
    assert result.captured_fields == ["scope"]


async def test_preset_opportunity_type_is_locked_on_first_run(session_store, monkeypatch):
    result, _ = await _run(session_store, monkeypatch, preset="cloud_migration")
    assert result.opportunity_type == "cloud_migration"
    state = session_store.require("m1")
    assert state.opportunity_type_locked and "current_environment" in state.fields


async def test_unknown_preset_type_is_ignored(session_store, monkeypatch):
    result, _ = await _run(session_store, monkeypatch, preset="no_such_type")
    assert result.opportunity_type is None


# --- processor ------------------------------------------------------------------------

SID = "test-call-0001-abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret (test session id)


class RecordingRun:
    def __init__(self, store):
        self.store, self.kwargs = store, []

    async def __call__(self, **kw):
        from tests.test_processor import FakeRun

        self.kwargs.append(kw)
        return await FakeRun(self.store)(**kw)


async def test_processor_passes_scope_and_writes_memory_after_minutes(
    timeline, session_store, memory, memory_client,
):
    timeline.start_session(SessionStart(SID, "manual", "Call", AssistConfig(), "acme", "erp", "cloud_migration"))
    timeline.append_turn(Turn(SID, "manual", "cx", "hello"))
    run = RecordingRun(session_store)

    async def minutes(session_id, *, timeline, store, regenerate):
        timeline.claim_minutes(session_id)
        timeline.put_minutes(session_id, {"status": "ok", "generated_at": "2026-10-15T10:00:00Z", "minutes": {
            "summary": "S", "attendees": [], "key_points": [], "decisions": [], "action_items": [],
            "risks": [], "open_questions": [], "next_steps": []}})
        return "ok"

    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, run=run,
                     minutes=minutes, memory=memory)
    proc.schedule(SID)
    await proc.wait_idle()

    kw = run.kwargs[0]
    assert kw["call_memory"].scope == MemoryScope("acme", "erp")
    assert kw["preset_opportunity_type"] == "cloud_migration"
    memory_client.add_record(facts_namespace("acct-acme"), "Acme hosts on VMware")
    assert [f["text"] for f in kw["call_memory"].search(SCOPE, "hosts", "both", 5)] == ["Acme hosts on VMware"]

    timeline.end_session(SID, "done")
    proc.schedule(SID)
    await proc.wait_idle()
    assert {e["actorId"] for e in memory_client.events} == {"opp-acme--erp"}


async def test_memory_write_failure_keeps_minutes(timeline, session_store, memory, memory_client):
    timeline.start_session(SessionStart(SID, "manual", "Call", AssistConfig(), "acme"))
    timeline.end_session(SID, "done")
    memory_client.fail = True

    async def minutes(session_id, *, timeline, store, regenerate):
        timeline.claim_minutes(session_id)
        timeline.put_minutes(session_id, {"status": "ok", "generated_at": "t", "minutes": {
            "summary": "S", "attendees": [], "decisions": [], "action_items": [], "open_questions": []}})
        return "ok"

    proc = Processor(timeline=timeline, store=session_store, debounce_seconds=0, minutes=minutes, memory=memory)
    proc.schedule(SID)
    await proc.wait_idle()
    assert timeline.get_minutes(SID)["status"] == "ok" and "memory_written_for" not in timeline.get_minutes(SID)
