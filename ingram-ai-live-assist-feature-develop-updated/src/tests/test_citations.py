"""Turn citations during the live pass: #seq in the prompt, evidence on captured fields."""

from app.orchestrator import process_turns
from app.session_store import FieldState, SessionState
from app.transcript import TranscriptChunk
from tests.fake_model import FakeModel, ScriptedToolCall
from tests.test_orchestrator import _patch_agent_factory


def _turns(*texts, start=5):
    return [TranscriptChunk("cx", t, seq=start + i) for i, t in enumerate(texts)]


def test_format_line_includes_seq_when_known():
    assert TranscriptChunk("cx", "Hi", name="Alice", seq=17).format_line() == "[#17 | cx | Alice]: Hi"
    assert TranscriptChunk("cx", "Hi").format_line() == "[cx]: Hi"


def test_chunk_from_timeline_item_keeps_seq():
    chunk = TranscriptChunk.from_timeline_item(
        {"seq": 3, "speaker": "ba", "text": "Hello", "speaker_name": "Bo", "spoken_at": "2026-10-08T10:00:00Z"}
    )
    assert chunk.seq == 3 and chunk.format_line() == "[#3 | 2026-10-08T10:00:00Z | ba | Bo]: Hello"


async def test_evidence_keeps_only_seqs_from_this_chunk(session_store, monkeypatch):
    _patch_agent_factory(monkeypatch, FakeModel(tool_calls=[ScriptedToolCall(
        "record_captured_field",
        {"field_id": "business_goal", "value": "cut ticket times", "speaker": "cx",
         "evidence_seqs": [6, 99, 5, 6]},
    )]))
    await process_turns(session_id="c1", turns=_turns("We drown in tickets.", "Cut times."), store=session_store)

    assert session_store.require("c1").fields["business_goal"].evidence == [5, 6]


async def test_evidence_accumulates_across_runs(session_store, monkeypatch):
    for seqs, turns in (([5], _turns("a", start=5)), ([9], _turns("b", start=9))):
        _patch_agent_factory(monkeypatch, FakeModel(tool_calls=[ScriptedToolCall(
            "record_captured_field",
            {"field_id": "scope", "value": "v", "speaker": "cx", "evidence_seqs": seqs},
        )]))
        await process_turns(session_id="c2", turns=turns, store=session_store)

    assert session_store.require("c2").fields["scope"].evidence == [5, 9]


async def test_capture_without_evidence_still_works(session_store, monkeypatch):
    _patch_agent_factory(monkeypatch, FakeModel(tool_calls=[ScriptedToolCall(
        "record_captured_field", {"field_id": "scope", "value": "v", "speaker": "cx"},
    )]))
    await process_turns(session_id="c3", turns=_turns("x"), store=session_store)

    fs = session_store.require("c3").fields["scope"]
    assert fs.status == "captured" and fs.evidence == []


async def test_prompt_turns_carry_seq_and_suggestion_records_trigger(session_store, monkeypatch):
    from strands import Agent

    prompts = []

    class RecordingAgent(Agent):
        async def invoke_async(self, prompt, *a, **kw):
            prompts.append(prompt)
            return await super().invoke_async(prompt, *a, **kw)

    model = FakeModel(tool_calls=[ScriptedToolCall(
        "suggest_next_question", {"question": "When do you need it live?", "target_field_id": "target_timeline"},
    )])
    def make_agent(*, session_id, system_prompt, tools):
        return RecordingAgent(model=model, system_prompt=system_prompt, tools=tools)

    monkeypatch.setattr("app.orchestrator._make_agent", make_agent)
    await process_turns(session_id="c4", turns=_turns("We want to move.", start=12), store=session_store)

    assert prompts[0].startswith("[#12 | cx]:")
    assert session_store.require("c4").suggestions[-1]["trigger_seq"] == 12


def test_field_state_without_evidence_loads_from_old_items():
    fs = FieldState.from_item("scope", {"label": "Scope", "status": "captured", "value": "v"})
    assert fs.evidence == []
    state = SessionState.from_item({"session_id": "s", "fields": {"scope": {"label": "Scope", "evidence": [3]}}})
    assert state.fields["scope"].evidence == [3]
