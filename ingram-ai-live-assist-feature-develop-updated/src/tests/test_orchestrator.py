import pytest

from app.orchestrator import _format_prompt, process_turns
from app.tools import MAX_AGENT_ADDED_GAPS
from app.transcript import TranscriptChunk
from tests.fake_model import FakeModel, ScriptedToolCall

GENERAL_REQUIRED = {
    "business_goal",
    "data_volumes",
    "security_compliance",
    "target_region",
    "desired_outcomes",
    "current_environment",
    "scope",
    "target_timeline",
}


def _patch_agent_factory(monkeypatch, model: FakeModel, captured_prompts: list | None = None):
    """Build the Agent with the fake model (no Bedrock, no AgentCore Memory)."""
    from strands import Agent
    from strands.tools.executors import SequentialToolExecutor

    def fake_make_agent(*, session_id, system_prompt, tools):
        if captured_prompts is not None:
            captured_prompts.append(system_prompt)
        return Agent(model=model, system_prompt=system_prompt, tools=tools,
                     tool_executor=SequentialToolExecutor())

    monkeypatch.setattr("app.orchestrator._make_agent", fake_make_agent)


def _call(name, tool_use_id="t1", **inp):
    return ScriptedToolCall(name=name, input=inp, tool_use_id=tool_use_id)


async def _run(session_store, monkeypatch, session_id, text, *, new=False, speaker="cx", calls=()):
    """Run one agent pass on a single turn. ``new`` documents intent only: the
    session state is created on the first run either way."""
    _patch_agent_factory(monkeypatch, FakeModel(tool_calls=list(calls)))
    result, _ = await process_turns(
        session_id=session_id,
        turns=[TranscriptChunk(speaker=speaker, text=text)],
        store=session_store,
    )
    return result


# --- general master guidance ------------------------------------------------

async def test_new_session_starts_on_general_guidance(session_store, monkeypatch):
    result = await _run(session_store, monkeypatch, "g1", "Thanks for joining.", new=True, speaker="ba")

    assert result.opportunity_type is None
    gap_ids = {g.field_id for g in result.gaps}
    assert GENERAL_REQUIRED <= gap_ids
    # required gaps listed before optional ones
    required_flags = [g.required for g in result.gaps]
    assert required_flags == sorted(required_flags, reverse=True)


async def test_unmatched_call_still_captures_and_suggests(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "g2",
        "Our support team drowns in repetitive tickets; response times are 2 days.",
        new=True,
        calls=[
            _call("update_problem_summary", "t1",
                  summary="Support team overwhelmed by repetitive tickets; 2-day response times."),
            _call("record_captured_field", "t2", field_id="business_goal",
                  value="repetitive tickets, 2-day response times", speaker="cx"),
            _call("suggest_next_question", "t3",
                  question="What would a good response time look like for you?",
                  target_field_id="desired_outcomes"),
        ],
    )

    assert result.opportunity_type is None
    assert result.problem_summary.startswith("Support team")
    assert result.captured_fields == ["business_goal"]
    assert "business_goal" not in {g.field_id for g in result.gaps}
    assert result.suggestion.target_label == "Desired outcome and success criteria"


async def test_prompt_lists_specialised_types_and_open_fields(session_store, monkeypatch):
    prompts: list = []
    _patch_agent_factory(monkeypatch, FakeModel(), prompts)
    await process_turns(session_id="g3", turns=[TranscriptChunk("cx", "hi")], store=session_store)

    prompt = prompts[0]
    assert "General discovery" in prompt
    assert "cloud_migration" in prompt and "general:" not in prompt
    assert "business_goal [required" in prompt
    assert "Stay in scope" in prompt


# --- specialised guidance layered on top -----------------------------------

async def test_matching_type_layers_fields_and_keeps_captured(session_store, monkeypatch):
    await _run(session_store, monkeypatch, "s1", "We're on-prem with 60 VMs.", new=True,
               calls=[_call("record_captured_field", field_id="current_environment",
                            value="on-prem, 60 VMs", speaker="cx")])

    result = await _run(
        session_store, monkeypatch, "s1", "We want to move it all to AWS, ERP and SQL Server first.",
        calls=[
            _call("set_opportunity_type", "t1", opportunity_type="cloud_migration"),
            # field only exists once the type is set -- same turn must work
            _call("record_captured_field", "t2", field_id="workload_inventory",
                  value="ERP, SQL Server", speaker="cx"),
        ],
    )

    assert result.opportunity_type == "cloud_migration"
    assert result.opportunity_type_just_resolved is True
    assert result.captured_fields == ["workload_inventory"]
    captured_ids = {c.field_id for c in result.captured}
    assert {"current_environment", "workload_inventory"} <= captured_ids
    gap_ids = {g.field_id for g in result.gaps}
    assert "business_goal" in gap_ids  # general fields still apply
    assert "current_environment" not in gap_ids


async def test_opportunity_type_cannot_change_once_set(session_store, monkeypatch):
    await _run(session_store, monkeypatch, "s2", "Cloud migration.", new=True,
               calls=[_call("set_opportunity_type", opportunity_type="cloud_migration")])
    result = await _run(session_store, monkeypatch, "s2", "Also hardware.",
                        calls=[_call("set_opportunity_type", opportunity_type="hardware_refresh")])
    assert result.opportunity_type == "cloud_migration"
    assert result.opportunity_type_just_resolved is False


# --- dynamic gaps ------------------------------------------------------------

async def test_agent_can_add_gap_and_capture_it_later(session_store, monkeypatch):
    monkeypatch.setattr("app.tools.ENABLE_AGENT_GAPS", True)
    result = await _run(
        session_store, monkeypatch, "d1", "Everything has to keep syncing with our SAP system.", new=True,
        calls=[
            _call("add_gap", "t1", label="SAP integration", description="Which SAP modules and sync direction?",
                  reason="Customer said everything must keep syncing with SAP", required=True),
            _call("suggest_next_question", "t2", question="Which SAP modules need to stay in sync?",
                  target_field_id="custom_sap_integration"),
        ],
    )
    assert result.added_gaps == ["custom_sap_integration"]
    added = next(g for g in result.gaps if g.field_id == "custom_sap_integration")
    assert added.source == "agent" and added.required is True
    assert result.suggestion.target_field_id == "custom_sap_integration"

    result = await _run(session_store, monkeypatch, "d1", "Finance and procurement, both directions.",
                        calls=[_call("record_captured_field", field_id="custom_sap_integration",
                                     value="FI and MM, bi-directional", speaker="cx")])
    assert "custom_sap_integration" not in {g.field_id for g in result.gaps}


async def test_added_gaps_are_capped(session_store, monkeypatch):
    monkeypatch.setattr("app.tools.ENABLE_AGENT_GAPS", True)
    calls = [
        _call("add_gap", f"t{i}", label=f"Topic {i}", description="d", reason="r")
        for i in range(MAX_AGENT_ADDED_GAPS + 2)
    ]
    result = await _run(session_store, monkeypatch, "d2", "lots of things", new=True, calls=calls)
    assert len(result.added_gaps) == MAX_AGENT_ADDED_GAPS


async def test_dismiss_optional_but_not_required_guidance_field(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "d3", "Budget is covered by AWS funding, no constraints there.", new=True,
        calls=[
            _call("dismiss_gap", "t1", field_id="budget_range", reason="Funded by AWS programme"),
            _call("dismiss_gap", "t2", field_id="business_goal", reason="trying to skip it"),
        ],
    )
    gap_ids = {g.field_id for g in result.gaps}
    assert result.dismissed_gaps == ["budget_range"]
    assert "budget_range" not in gap_ids
    assert "business_goal" in gap_ids


async def test_suggestion_dropped_when_target_answered_same_turn(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "d4", "We need it done by Q3.", new=True,
        calls=[
            _call("record_captured_field", "t1", field_id="target_timeline", value="Q3", speaker="cx"),
            _call("suggest_next_question", "t2", question="What's your timeline?", target_field_id="target_timeline"),
        ],
    )
    assert result.suggestion is None


async def test_record_unknown_field_is_ignored(session_store, monkeypatch):
    result = await _run(session_store, monkeypatch, "d5", "hello", new=True,
                        calls=[_call("record_captured_field", field_id="nope", value="x", speaker="cx")])
    assert result.captured_fields == []


# --- validation --------------------------------------------------------------

async def test_empty_turns_rejected_before_touching_store(session_store, monkeypatch):
    _patch_agent_factory(monkeypatch, FakeModel())
    with pytest.raises(ValueError):
        await process_turns(session_id="v1", turns=[], store=session_store)
    assert session_store.get("v1") is None


async def test_cursor_and_narration_recorded(session_store, monkeypatch):
    _patch_agent_factory(monkeypatch, FakeModel(final_text="Noted the hosting."))
    result, _ = await process_turns(
        session_id="cur1", turns=[TranscriptChunk("cx", "On-prem.")], store=session_store, last_seq=7
    )

    assert result.narration == ["Noted the hosting."]
    assert result.run_number == 1
    assert session_store.require("cur1").last_seq == 7


def test_format_prompt_shows_summary_and_dismissed():
    from app.orchestrator import _seed_guidance_fields
    from app.session_store import SessionState

    state = SessionState.new("p")
    _seed_guidance_fields(state)
    state.problem_summary = "Wants faster support."
    state.set_field_not_applicable("budget_range", "AWS funded")
    prompt = _format_prompt(state, ["cloud_migration"])
    assert "Wants faster support." in prompt
    assert "- budget_range: AWS funded" in prompt


# --- suggestion history -------------------------------------------------------

async def test_suggestions_are_persisted_across_turns(session_store, monkeypatch):
    await _run(
        session_store, monkeypatch, "h1", "Our tickets pile up.", new=True,
        calls=[_call("suggest_next_question", "t1",
                     question="What does a typical ticket backlog look like?",
                     target_field_id="business_goal")],
    )
    # A turn with no suggestion must not add an entry.
    await _run(session_store, monkeypatch, "h1", "Mm-hm.", speaker="ba")
    await _run(
        session_store, monkeypatch, "h1", "About 400 open tickets.",
        calls=[_call("suggest_next_question", "t1",
                     question="What outcome would make this a success?",
                     target_field_id="desired_outcomes")],
    )

    state = session_store.require("h1")
    assert state.chunk_count == 3
    assert [s["turn"] for s in state.suggestions] == [1, 3]
    assert state.suggestions[0]["question"] == "What does a typical ticket backlog look like?"
    assert state.suggestions[0]["target_field_id"] == "business_goal"
    assert state.suggestions[0]["trigger_speaker"] == "cx"
    assert state.suggestions[1]["trigger_text"] == "About 400 open tickets."


async def test_multiple_turns_sent_together_in_one_invocation(session_store, monkeypatch):
    seen: list = []
    from strands import Agent
    from strands.tools.executors import SequentialToolExecutor

    class RecordingAgent(Agent):
        async def invoke_async(self, prompt, *a, **kw):
            seen.append(prompt)
            return await super().invoke_async(prompt, *a, **kw)

    def fake_make_agent(*, session_id, system_prompt, tools):
        model = FakeModel(tool_calls=[_call("suggest_next_question", "t1",
                                            question="Any deadline?", target_field_id="target_timeline")])
        return RecordingAgent(model=model, system_prompt=system_prompt, tools=tools,
                              tool_executor=SequentialToolExecutor())

    monkeypatch.setattr("app.orchestrator._make_agent", fake_make_agent)
    await process_turns(
        session_id="b1",
        turns=[TranscriptChunk("ba", "What hurts most?"), TranscriptChunk("cx", "Slow tickets.")],
        store=session_store,
    )

    assert seen == ["[ba]: What hurts most?\n[cx]: Slow tickets."]
    state = session_store.require("b1")
    assert state.chunk_count == 1  # one request, however many turns it carried
    assert state.suggestions[0]["trigger_text"] == "[ba]: What hurts most?\n[cx]: Slow tickets."


async def test_system_prompt_saved_per_invocation_under_session_folder(
    session_store, monkeypatch, prompt_log_dir
):
    await _run(session_store, monkeypatch, "p1", "Hello.", new=True)
    await _run(session_store, monkeypatch, "p1", "Our tickets pile up.")
    # A hostile session id must not escape the log directory.
    await _run(session_store, monkeypatch, "../evil", "hi", new=True)

    folder = prompt_log_dir / "p1"
    assert sorted(f.name for f in folder.glob("*_system_prompt.txt")) == [
        "0001_system_prompt.txt",
        "0002_system_prompt.txt",
    ]
    assert "Stay in scope" in (folder / "0001_system_prompt.txt").read_text()
    assert (prompt_log_dir / "_evil").is_dir()
    assert not (prompt_log_dir.parent / "evil").exists()


async def test_message_history_saved_per_invocation(session_store, monkeypatch, prompt_log_dir):
    import json

    await _run(session_store, monkeypatch, "m1", "Our tickets pile up.", new=True)

    doc = json.loads((prompt_log_dir / "m1" / "0001_messages.json").read_text())
    assert doc["invocation"] == 1
    assert doc["loaded_before_invoke"] == 0
    assert doc["total_after_invoke"] == len(doc["messages"]) >= 2
    assert doc["messages"][0]["role"] == "user"
    assert "Our tickets pile up." in json.dumps(doc["messages"][0])


async def test_suggestion_text_is_cleaned_of_ai_style_punctuation(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "t1", "We need it soon.", new=True,
        calls=[_call("suggest_next_question", "t1",
                     question="What\u2019s the deadline \u2014 and who signs off\u2026 in 5\u201310 days?",
                     target_field_id="target_timeline")],
    )

    q = result.suggestion.question
    assert "\u2014" not in q and "\u2013" not in q and "\u2019" not in q and "\u2026" not in q
    assert q == "What's the deadline, and who signs off... in 5-10 days?"
    assert session_store.require("t1").suggestions[0]["question"] == q


async def test_timestamp_speaker_name_and_text_reach_the_message_history(
    session_store, monkeypatch, prompt_log_dir
):
    import json

    _patch_agent_factory(monkeypatch, FakeModel())
    await process_turns(
        session_id="n1",
        turns=[
            TranscriptChunk("ba", "Tell me more.", name="John", timestamp="2026-10-08T10:00:00Z"),
            TranscriptChunk("cx", "We want the cloud.", name="Alice", timestamp="2026-10-08T10:00:18Z"),
        ],
        store=session_store,
    )

    doc = json.loads((prompt_log_dir / "n1" / "0001_messages.json").read_text())
    first_user = json.dumps(doc["messages"][0])
    assert "[2026-10-08T10:00:00Z | ba | John]: Tell me more." in first_user
    assert "[2026-10-08T10:00:18Z | cx | Alice]: We want the cloud." in first_user


# --- suggestion rules: what / when / how -------------------------------------

async def test_add_gap_tool_is_off_by_default():
    from app.tools import build_tools

    names = {t.tool_name for t in build_tools(
        scratch={}, known_fields={}, available_opportunity_types=[],
        opportunity_type_locked=False, agent_added_count=0,
        load_specialised_fields=lambda _t: [],
    )}
    assert "add_gap" not in names and "suggest_next_question" in names


async def test_suggestion_outside_approved_topics_is_rejected(session_store, monkeypatch):
    # workload_inventory exists only in the cloud_migration checklist: tracked, never asked.
    result = await _run(
        session_store, monkeypatch, "r1", "We run about 60 VMs.", new=True,
        calls=[
            _call("set_opportunity_type", "t1", opportunity_type="cloud_migration"),
            _call("suggest_next_question", "t2", question="Which apps need to move?",
                  target_field_id="workload_inventory"),
        ],
    )
    assert result.suggestion is None


async def test_suggestion_without_a_target_topic_is_rejected(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "r2", "We want to move to AWS.", new=True,
        calls=[_call("suggest_next_question", "t1", question="Who is your account manager?")],
    )
    assert result.suggestion is None


async def test_cannot_ask_about_a_topic_answered_in_the_same_chunk(session_store, monkeypatch):
    result = await _run(
        session_store, monkeypatch, "r3", "We'll deploy in us-east-1.", new=True,
        calls=[
            _call("record_captured_field", "t1", field_id="target_region", value="us-east-1", speaker="cx"),
            _call("suggest_next_question", "t2", question="Which region do you want?",
                  target_field_id="target_region"),
        ],
    )
    assert result.suggestion is None


async def test_prompt_lists_askable_topics_by_category_and_recent_suggestions(session_store, monkeypatch):
    from app.orchestrator import _seed_guidance_fields
    from app.session_store import SessionState

    state = SessionState.new("p")
    _seed_guidance_fields(state)
    state.set_field_captured("business_goal", "retire the data center", "cx")
    state.chunk_count = 4
    state.add_suggestion(
        question="Which systems are part of this?", target_field_id="scope",
        target_label="Scope", trigger_speaker="cx", trigger_text="x",
    )
    state.suggestions[-1]["turn"] = 3

    prompt = _format_prompt(state, ["cloud_migration"])

    assert "## Topics you may ask about" in prompt
    assert "- Business: desired_outcomes" in prompt  # business_goal is captured, so not listed
    assert "business_goal," not in prompt.split("## Topics you may ask about")[1].split("##")[0]
    assert "- Commercial: budget_range" in prompt
    assert "1 request(s) ago, topic scope" in prompt
    assert "Which systems are part of this?" in prompt
    assert "WHAT to ask" in prompt and "WHEN to ask" in prompt and "HOW to ask" in prompt


async def test_specialised_fields_are_marked_capture_only_in_prompt(session_store, monkeypatch):
    from app.orchestrator import _seed_guidance_fields
    from app.session_store import SessionState

    state = SessionState.new("p2")
    state.opportunity_type = "cloud_migration"
    _seed_guidance_fields(state)

    prompt = _format_prompt(state, [])

    line = next(l for l in prompt.splitlines() if l.startswith("- workload_inventory"))
    assert "capture only, do not ask" in line


# --- assist settings: scope, frequency, count, instructions ------------------

async def _run_with(session_store, monkeypatch, session_id, text, config, *, new=True, calls=()):
    from app.assist_config import AssistConfig

    _patch_agent_factory(monkeypatch, FakeModel(tool_calls=list(calls)))
    result, _ = await process_turns(
        session_id=session_id,
        turns=[TranscriptChunk("cx", text)],
        store=session_store,
        assist_config=AssistConfig(**config),
    )
    return result


async def test_guided_scope_allows_follow_up_without_target(session_store, monkeypatch):
    result = await _run_with(
        session_store, monkeypatch, "c1", "Reviews take three weeks.", {"scope": "guided"},
        calls=[_call("suggest_next_question", "t1", question="Where do most of those days go?",
                     rationale="Customer gave a duration but not the cause.")],
    )
    assert result.suggestion.question == "Where do most of those days go?"
    assert result.suggestion.target_field_id is None
    assert result.suggestion.rationale == "Customer gave a duration but not the cause."
    assert session_store.require("c1").suggestions[0]["rationale"].startswith("Customer gave")


async def test_open_scope_allows_specialised_fields(session_store, monkeypatch):
    result = await _run_with(
        session_store, monkeypatch, "c2", "We run about 60 VMs.", {"scope": "open"},
        calls=[
            _call("set_opportunity_type", "t1", opportunity_type="cloud_migration"),
            _call("suggest_next_question", "t2", question="Which apps need to move first?",
                  target_field_id="workload_inventory"),
        ],
    )
    assert result.suggestion.target_field_id == "workload_inventory"


async def test_open_scope_still_rejects_captured_targets(session_store, monkeypatch):
    result = await _run_with(
        session_store, monkeypatch, "c3", "We'll use eu-west-1.", {"scope": "open"},
        calls=[
            _call("record_captured_field", "t1", field_id="target_region", value="eu-west-1", speaker="cx"),
            _call("suggest_next_question", "t2", question="Which region?", target_field_id="target_region"),
        ],
    )
    assert result.suggestions == []


async def test_max_suggestions_is_enforced(session_store, monkeypatch):
    calls = [
        _call("suggest_next_question", f"t{i}", question=f"Question number {i}?", target_field_id=fid)
        for i, fid in enumerate(["scope", "current_environment", "data_volumes"])
    ]
    result = await _run_with(
        session_store, monkeypatch, "c4", "Lots to cover.", {"max_suggestions": 2}, calls=calls,
    )
    assert [s.target_field_id for s in result.suggestions] == ["scope", "current_environment"]
    assert len(session_store.require("c4").suggestions) == 2


async def test_one_suggestion_per_topic_per_request(session_store, monkeypatch):
    result = await _run_with(
        session_store, monkeypatch, "c5", "Hi.", {"max_suggestions": 3},
        calls=[
            _call("suggest_next_question", "t1", question="Where does it run?", target_field_id="current_environment"),
            _call("suggest_next_question", "t2", question="What hosts it?", target_field_id="current_environment"),
        ],
    )
    assert len(result.suggestions) == 1


async def test_repeated_question_is_rejected_across_requests(session_store, monkeypatch):
    ask = [_call("suggest_next_question", "t1", question="What systems do you use today?",
                 target_field_id="current_environment")]
    first = await _run_with(session_store, monkeypatch, "c6", "Hello.", {}, calls=ask)
    second = await _run_with(session_store, monkeypatch, "c6", "We have a few tools.", {}, new=False,
                             calls=[_call("suggest_next_question", "t1",
                                          question="what SYSTEMS do you use today",
                                          target_field_id="current_environment")])
    assert first.suggestion is not None
    assert second.suggestion is None


async def test_prompt_reflects_settings(session_store, monkeypatch):
    from app.assist_config import AssistConfig
    from app.orchestrator import _seed_guidance_fields
    from app.session_store import SessionState

    state = SessionState.new("p3")
    _seed_guidance_fields(state)

    checklist = _format_prompt(state, [], AssistConfig())
    assert "## Topics you may ask about" in checklist
    assert "at most one question" in checklist
    assert "Additional instructions" not in checklist

    guided = _format_prompt(
        state, [], AssistConfig("guided", "proactive", 3, "Focus on how work is done today.")
    )
    assert "## Checklist topics still open" in guided
    assert "follow-up that clarifies" in guided
    assert "up to 3 questions" in guided
    assert "Suggest on every request" in guided
    assert "Focus on how work is done today." in guided
    # Topic hints come from the checklist config, not the prompt template.
    assert "- target_region: ask after hosting and data are known" in guided
    assert '"Which AWS region do you want to use?"' in guided


async def test_result_carries_config_used(session_store, monkeypatch):
    result = await _run_with(session_store, monkeypatch, "c7", "Hello.", {"scope": "open", "max_suggestions": 2})
    assert result.config.scope == "open" and result.config.max_suggestions == 2


async def test_empty_prompt_log_dir_disables_dumps(session_store, monkeypatch, tmp_path):
    monkeypatch.setattr("app.orchestrator.PROMPT_LOG_DIR", "")
    monkeypatch.chdir(tmp_path)

    await _run(session_store, monkeypatch, "off1", "Hello.", new=True)

    assert list(tmp_path.iterdir()) == []


def test_real_agent_does_not_print_model_output(monkeypatch):
    """Stdout goes to CloudWatch in the runtime; model output must not."""
    from app.orchestrator import _make_agent

    monkeypatch.setattr("app.orchestrator.DEFAULT_MEMORY_ID", None)
    monkeypatch.setattr("app.orchestrator.DEFAULT_REGION", "us-east-1")
    agent = _make_agent(session_id="s", system_prompt="p", tools=[])
    assert getattr(agent.callback_handler, "__name__", "") == "null_callback_handler"
