"""Asking the AI to change a finished run: the SOW edit port, the pipeline's revise step, the API and the worker."""
import copy
import json
import sys
from pathlib import Path

import pytest
from fakes import COST_RESULT, DESIGN, SOW_MARKDOWN, UPSTREAM, USAGE_JSON, FakeAgents

from orchestrator import adapters, sow_edit
from orchestrator.agents import JobPoller
from orchestrator.contracts import STAGES, PipelineState, Stage, Status
from orchestrator.pipeline import Pipeline

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deployment" / "api"))


# ----------------------------------------------------------------- helpers
def _pipeline(agents: FakeAgents) -> Pipeline:
    counter = {"t": 0.0}

    def clock() -> float:
        counter["t"] += 0.5
        return counter["t"]

    return Pipeline(agents, poller=JobPoller(agents.read_job, interval=0, timeout=5,
                                             sleep=lambda _s: None, clock=clock))


def _finished_state() -> PipelineState:
    """A run that has completed every stage, the state a person asks for a change on.

    The SA checkpoint is signed off, as it would be on a run that reached the end.
    """
    state = PipelineState(run_id="r1", template_id="aiml/ai-ml", upstream=copy.deepcopy(UPSTREAM),
                          sow_markdown=SOW_MARKDOWN, sa_signed_off=True)
    _pipeline(FakeAgents()).run(state)
    assert state.status == Status.COMPLETED
    return state


def _ask(state: PipelineState, target: str, instruction: str) -> None:
    state.revision = {"target": target, "instruction": instruction, "requested_at": "t", "requested_by": "Web user"}


def _block_id(markdown: str, starts_with: str) -> str:
    return next(b.id for b in sow_edit.parse(markdown).blocks if b.text.startswith(starts_with))


def _sow_editor(proposals, reply="Updated the SOW."):
    agents = None

    def edit(payload):
        agents.jobs[payload["job_id"]] = {"status": "completed", "stage": "done", "progress": 100,
                                          "proposals": proposals, "assistant_text": reply}
        return {"status": "accepted"}

    agents = FakeAgents(**{"sow:edit": edit})
    return agents


def _revised_design() -> dict:
    design = copy.deepcopy(DESIGN)
    design["components"].append({"id": "C-05", "name": "Web Firewall", "service": "AWS WAF",
                                 "category": "security", "role": "Filters requests", "quantity": 1})
    return design


def _architect(**overrides) -> FakeAgents:
    base = {
        "architecture:revise_adr": {"status": "completed", "run_id": "rev-1", "design": _revised_design(),
                                    "adr_md": "# ADR v2", "spec_json": {"v": 2}, "scope_warnings": []},
        "architecture:estimate_usage": {"status": "completed", "usage_json": USAGE_JSON,
                                        "usage_assumptions_md": "# usage v2"},
        "architecture:generate_diagram": {"status": "completed", "job_id": "job-2", "report_status": "pass",
                                          "diagram": {"png_base64": "TkVX"}},
    }
    base.update(overrides)
    return FakeAgents(**base)


# ----------------------------------------------------------------- the SOW edit port
def test_block_ids_number_every_non_empty_line_and_whole_tables():
    md = "# Title\nIntro line\n\n- one\n- two\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n```\ncode\n```\nTail"
    model = sow_edit.parse(md)
    assert [(b.id, b.kind) for b in model.blocks] == [
        ("b1", "heading"), ("b2", "para"), ("b3", "bullet"), ("b4", "bullet"), ("b5", "table"),
        ("b6", "code"), ("b7", "para")]
    mapping, outline = sow_edit.sections_map_of(model)
    assert outline == ["Title"]
    assert mapping["Title"]["tables"] == [{"tag": "t1", "before_para_id": "b4", "data": [["a", "b"], ["1", "2"]]}]
    assert [p["block_id"] for p in mapping["Title"]["paragraphs"]] == ["b2", "b3", "b4", "b6", "b7"]


def test_an_edit_replaces_the_block_and_keeps_its_bullet_marker():
    bid = _block_id(SOW_MARKDOWN, "Availability target")
    out, applied, skipped = sow_edit.apply_proposals(SOW_MARKDOWN, [
        {"type": "edit_proposal", "block_id": bid, "replace": "Availability target 99.95% monthly"}])
    assert "- Availability target 99.95% monthly" in out and "99.9% monthly across" not in out
    assert applied == ["Edit"] and skipped == []


def test_a_new_section_goes_after_the_named_section_and_a_delete_removes_it():
    out, applied, _ = sow_edit.apply_proposals(SOW_MARKDOWN, [
        {"type": "insert_proposal", "after_heading": "Out of Scope", "markdown": "## Assumptions\n- Single region"},
        {"type": "delete_proposal", "heading": "In Scope"}])
    assert "## Assumptions" in out and "## In Scope" not in out
    assert out.index("## Out of Scope") < out.index("## Assumptions") < out.index("# Pricing")
    assert len(applied) == 2


def test_a_table_edit_rewrites_the_whole_table():
    md = "# Fees\n| Item | Price |\n| --- | --- |\n| Setup | 1 |\n"
    out, applied, _ = sow_edit.apply_proposals(md, [{
        "type": "edit_proposal", "table_tag": "t1",
        "replace": 'TABLE_JSON:[["Item","Price"],["Setup","2"]]'}])
    assert "| Setup | 2 |" in out and "| Setup | 1 |" not in out and applied == ["Edit table"]


def test_a_proposal_whose_target_is_gone_is_skipped_not_applied_blindly():
    bid = _block_id(SOW_MARKDOWN, "40 concurrent users")
    out, applied, skipped = sow_edit.apply_proposals(SOW_MARKDOWN, [
        {"type": "edit_proposal", "block_id": bid, "replace": "60 concurrent users, p95 800 ms"},
        {"type": "edit_proposal", "block_id": bid, "replace": "80 concurrent users"},     # same block again
        {"type": "edit_proposal", "block_id": "b999", "replace": "x"},
        {"type": "delete_proposal", "heading": "No such section"},
        {"type": "mystery"}])
    assert "60 concurrent users" in out and "80 concurrent users" not in out
    assert len(applied) == 1 and len(skipped) == 4


# ----------------------------------------------------------------- changing the SOW
def test_a_sow_change_edits_the_sow_and_sends_every_later_stage_back_to_pending():
    state = _finished_state()
    bid = _block_id(state.sow_markdown, "Availability target")
    agents = _sow_editor([{"type": "edit_proposal", "block_id": bid,
                           "replace": "Availability target 99.95% monthly across three Availability Zones"}])
    _ask(state, "sow", "Raise the availability target to 99.95% across three AZs")

    entry = _pipeline(agents).revise(state)

    assert entry["status"] == "applied" and "1 change(s) applied" in entry["summary"]
    assert "99.95% monthly across three" in state.sow_markdown
    assert state.revision == {} and state.revisions == [entry]
    assert state.run(Stage.SOW_DRAFT).status in (Status.COMPLETED, Status.SKIPPED)
    assert all(state.run(s).status == Status.PENDING and not state.run(s).output for s in STAGES[1:])
    # The SA checkpoint comes first after the draft; it is already signed off, so it passes through.
    assert state.next_stage() == Stage.SA_REVIEW

    request = agents.payload_for("sow:edit")["request"]
    assert request["instruction"].startswith("Raise the availability")
    assert request["customer_name"] == "Fabrikam Insurance" and request["template_id"] == "aiml/ai-ml"
    assert "Scope of Work" in request["document_outline"]
    block_ids = {p["block_id"] for sec in request["sections_map"].values() for p in sec["paragraphs"]}
    assert bid in block_ids


def test_after_a_sow_change_the_normal_sequence_rebuilds_everything_after_it():
    state = _finished_state()
    bid = _block_id(state.sow_markdown, "40 concurrent users")
    agents = _sow_editor([{"type": "edit_proposal", "block_id": bid, "replace": "60 concurrent users"}])
    _ask(state, "sow", "Raise concurrency to 60 users")
    pipeline = _pipeline(agents)
    pipeline.revise(state)
    pipeline.run(state)

    assert agents.actions() == ["sow:edit", "sow:wa_review", "architecture:run_pipeline", "cost:estimate", "sow:check"]
    assert state.status == Status.COMPLETED
    assert "60 concurrent users" in state.sow_markdown
    assert "https://calculator.aws/#/estimate?id=abc123" in state.sow_markdown


def test_a_change_that_fixes_a_blocked_review_unblocks_the_run():
    state = _finished_state()
    state.run(Stage.WA_REVIEW).status = Status.BLOCKED
    state.run(Stage.ARCHITECTURE).status = Status.PENDING
    assert state.status == Status.BLOCKED
    bid = _block_id(state.sow_markdown, "Availability target")
    agents = _sow_editor([{"type": "edit_proposal", "block_id": bid, "replace": "Availability target 99.99%"}])
    _ask(state, "sow", "Fix the availability finding")
    _pipeline(agents).revise(state)
    assert state.run(Stage.WA_REVIEW).status == Status.PENDING and state.next_stage() == Stage.SA_REVIEW


def test_a_sow_change_with_nothing_applicable_changes_nothing_and_re_runs_nothing():
    state = _finished_state()
    before = state.sow_markdown
    agents = _sow_editor([{"type": "edit_proposal", "block_id": "b999", "replace": "x"}], reply="")
    _ask(state, "sow", "Do something to the SOW")
    entry = _pipeline(agents).revise(state)

    assert entry["status"] == "no_change" and state.sow_markdown == before
    assert state.status == Status.COMPLETED and state.next_stage() is None
    assert entry["details"] and entry["details"][0].startswith("Not applied.")


def test_a_failed_sow_change_leaves_the_run_exactly_as_it_was():
    state = _finished_state()
    before_sow, before_costs = state.sow_markdown, copy.deepcopy(state.run(Stage.COST).output)
    agents = FakeAgents(**{"sow:edit": {"status": "failed", "error": "the editor is busy"}})
    _ask(state, "sow", "Add a section on training")
    entry = _pipeline(agents).revise(state)

    assert entry["status"] == "failed" and "editor is busy" in entry["error"]
    assert state.sow_markdown == before_sow and state.run(Stage.COST).output == before_costs
    assert state.status == Status.COMPLETED and state.revision == {}


def test_an_unexpected_error_is_recorded_not_raised_and_the_request_is_cleared():
    """Otherwise the pending request would be retried by every later worker invocation."""
    state = _finished_state()

    def boom(payload):
        raise RuntimeError("s3 unavailable")

    _ask(state, "sow", "Add a section on training")
    entry = _pipeline(FakeAgents(**{"sow:edit": boom})).revise(state)
    assert entry["status"] == "failed" and "RuntimeError" in entry["error"]
    assert state.revision == {}


# ----------------------------------------------------------------- changing the architecture
def test_an_architecture_change_revises_the_design_then_the_usage_then_the_diagram():
    state = _finished_state()
    sow_before = state.sow_markdown
    agents = _architect()
    _ask(state, "architecture", "Put AWS WAF in front of the API")
    entry = _pipeline(agents).revise(state)

    assert agents.actions() == ["architecture:revise_adr", "architecture:estimate_usage", "architecture:generate_diagram"]
    assert entry["status"] == "applied" and "1 component(s) added" in entry["summary"]
    assert entry["details"] == ["Added: Web Firewall (AWS WAF)"]

    output = state.run(Stage.ARCHITECTURE).output
    assert output["diagram"]["png_base64"] == "TkVX"
    assert output["adr"]["adr_md"] == "# ADR v2"
    assert any(c["service"] == "AWS WAF" for c in adapters.design_of(output)["components"])
    assert output["usage"]["usage_assumptions_md"] == "# usage v2"

    assert state.sow_markdown == sow_before
    assert state.run(Stage.ARCHITECTURE).status == Status.COMPLETED
    assert state.run(Stage.COST).status == Status.PENDING and state.run(Stage.COMBINED_REVIEW).status == Status.PENDING
    assert state.next_stage() == Stage.COST


def test_the_architecture_revision_is_bound_by_the_sow_scope_and_sees_the_instruction():
    state = _finished_state()
    agents = _architect()
    _ask(state, "architecture", "Put AWS WAF in front of the API")
    _pipeline(agents).revise(state)

    payload = agents.payload_for("architecture:revise_adr")
    assert payload["instruction"] == "Put AWS WAF in front of the API"
    assert payload["design"]["title"] == "Claims FNOL Assistant"
    assert payload["srs_text"] == state.sow_markdown
    assert "Amazon Kinesis streaming ingestion" in payload["scope"]["out_of_scope"]
    assert agents.payload_for("architecture:generate_diagram")["design"]["components"][-1]["service"] == "AWS WAF"


def test_a_failed_redraw_does_not_leave_a_new_design_next_to_the_old_diagram():
    state = _finished_state()
    before = copy.deepcopy(state.run(Stage.ARCHITECTURE).output)
    agents = _architect(**{"architecture:generate_diagram": {"status": "failed", "error": "drawing failed"}})
    _ask(state, "architecture", "Add a second region")
    entry = _pipeline(agents).revise(state)

    assert entry["status"] == "failed" and "drawing failed" in entry["error"]
    assert state.run(Stage.ARCHITECTURE).output == before
    assert state.status == Status.COMPLETED and state.next_stage() is None


def test_when_usage_cannot_be_re_estimated_the_old_usage_is_dropped_so_it_is_not_priced():
    state = _finished_state()
    agents = _architect(**{"architecture:estimate_usage": {"status": "failed", "error": "usage model timed out"}})
    _ask(state, "architecture", "Put AWS WAF in front of the API")
    entry = _pipeline(agents).revise(state)

    assert entry["status"] == "applied"
    assert state.run(Stage.ARCHITECTURE).output["usage"] == {}
    text = adapters.cost_requirements(state.run(Stage.ARCHITECTURE).output)
    assert "AWS WAF" in text and "invocations_per_month" not in text


def test_an_architecture_change_needs_an_existing_design():
    state = _finished_state()
    state.run(Stage.ARCHITECTURE).output = {}
    _ask(state, "architecture", "Add a cache")
    entry = _pipeline(_architect()).revise(state)
    assert entry["status"] == "failed" and "no architecture design" in entry["error"]


# ----------------------------------------------------------------- changing the cost estimate
def test_a_cost_change_prices_the_current_architecture_with_the_adjustment_and_swaps_the_link():
    state = _finished_state()
    new_url = "https://calculator.aws/#/estimate?id=second"
    agents = FakeAgents(**{"cost:estimate": {**COST_RESULT, "calculator_url": new_url, "services_count": 3}})
    _ask(state, "cost", "Assume a 3-year Savings Plan for Fargate")
    entry = _pipeline(agents).revise(state)

    requirements = agents.payload_for("cost:estimate")["requirements"]
    assert "Amazon Bedrock" in requirements and "3-year Savings Plan" in requirements
    assert requirements.index("Adjustment requested") > requirements.index("Amazon Bedrock")

    assert entry["status"] == "applied"
    assert state.run(Stage.COST).output["calculator_url"] == new_url
    assert new_url in state.sow_markdown and "id=abc123" not in state.sow_markdown
    assert state.run(Stage.ARCHITECTURE).status == Status.COMPLETED
    assert state.next_stage() == Stage.COMBINED_REVIEW


def test_a_cost_change_can_recover_a_failed_cost_stage():
    state = _finished_state()
    state.run(Stage.COST).status, state.run(Stage.COST).error = Status.FAILED, "boom"
    state.run(Stage.COMBINED_REVIEW).status = Status.PENDING
    assert state.next_stage() is None
    _ask(state, "cost", "Price it on-demand only")
    _pipeline(FakeAgents()).revise(state)
    assert state.run(Stage.COST).status == Status.COMPLETED and state.run(Stage.COST).error == ""
    assert state.next_stage() == Stage.COMBINED_REVIEW


def test_a_cost_change_that_returns_no_link_is_refused_and_keeps_the_old_estimate():
    state = _finished_state()
    before = copy.deepcopy(state.run(Stage.COST).output)
    agents = FakeAgents(**{"cost:estimate": {"status": "completed", "estimate": {}, "services_count": 0}})
    _ask(state, "cost", "Assume reserved instances")
    entry = _pipeline(agents).revise(state)
    assert entry["status"] == "failed" and "calculator_url" in entry["error"]
    assert state.run(Stage.COST).output == before


def test_the_cost_adjustment_always_fits_the_cost_agents_input_limit():
    long_base = "x" * adapters.COST_MAX_CHARS
    text = adapters.with_cost_adjustment(long_base, "use spot")
    assert len(text) <= adapters.COST_MAX_CHARS and text.endswith("use spot")


# ----------------------------------------------------------------- persistence and the payload
def test_a_pending_request_and_the_history_survive_a_save_and_load(tmp_path):
    from test_store import FakeS3, FakeTable
    import common
    import store

    table, s3 = FakeTable(), FakeS3()
    mp = pytest.MonkeyPatch()
    try:
        for module in (common, store):
            mp.setattr(module, "table", lambda: table)
        mp.setattr(common, "s3", lambda: s3)
        mp.setattr(common, "bucket", lambda: "b")
        state = _finished_state()
        state.run_id = "e" * 32
        _ask(state, "cost", "Assume reserved instances")
        state.revisions = [{"target": "sow", "instruction": "x", "status": "applied", "summary": "ok", "details": []}]
        store.save(state, refresh_meta=False)
        assert ("RUN#" + state.run_id, "META") not in table.items          # META untouched when asked not to
        loaded = store.load(state.run_id)
        assert loaded.revision["target"] == "cost" and loaded.revisions[0]["summary"] == "ok"
        assert loaded.to_json()["revision"]["instruction"] == "Assume reserved instances"
    finally:
        mp.undo()


def test_the_state_payload_lists_the_pending_change_and_the_history():
    state = _finished_state()
    assert state.to_json()["revision"] is None and state.to_json()["revisions"] == []
    _ask(state, "sow", "Add training")
    state.revisions.append({"target": "cost", "status": "applied"})
    payload = state.to_json()
    assert payload["revision"] == {"target": "sow", "instruction": "Add training", "requested_at": "t"}
    assert payload["revisions"] == [{"target": "cost", "status": "applied"}]


# ----------------------------------------------------------------- the API
def _event(body=None, run_id="a" * 32):
    return {"pathParameters": {"id": run_id}, "body": json.dumps(body or {}),
            "requestContext": {"http": {"sourceIp": "1.2.3.4"}}}


@pytest.fixture
def api(monkeypatch):
    import handler

    state = _finished_state()
    seen = {"slots": [], "reserved": [], "saved": [], "updates": [], "workers": []}
    monkeypatch.setattr(handler, "_meta", lambda rid: {"run_id": rid, "status": "completed"})
    monkeypatch.setattr(handler, "load", lambda rid: state)
    monkeypatch.setattr(handler, "_take_slot", lambda *a, **k: seen["slots"].append(a[0]["pk"].split("#")[0]))
    monkeypatch.setattr(handler, "_reserve_for_revision", lambda rid: seen["reserved"].append(rid))
    monkeypatch.setattr(handler, "save", lambda s, **k: seen["saved"].append(k))
    monkeypatch.setattr(handler, "update", lambda key, **f: seen["updates"].append(f))
    monkeypatch.setattr(handler, "log", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_start_worker", lambda rid: seen["workers"].append(rid))
    return handler, state, seen


def test_a_valid_change_is_queued_and_the_worker_started(api):
    handler, state, seen = api
    response = handler.revise_run(_event({"target": "architecture", "instruction": "  Add a WAF   in front "}))

    assert response["statusCode"] == 202 and json.loads(response["body"])["target"] == "architecture"
    assert state.revision["target"] == "architecture" and state.revision["instruction"] == "Add a WAF in front"
    assert seen["saved"] == [{"refresh_meta": False}], "the run must stay reserved, not flip back to completed"
    assert seen["updates"][0]["stage"] == "architecture" and seen["workers"] == ["a" * 32]
    assert seen["slots"] == ["REVIP", "REVDAY"] and seen["reserved"] == ["a" * 32]


@pytest.mark.parametrize("body,status", [
    ({"target": "everything", "instruction": "Change it all"}, 400),
    ({"target": "sow", "instruction": "hi"}, 400),
    ({"target": "sow", "instruction": "x" * 2001}, 400),
    ({"instruction": "Change the SOW please"}, 400),
])
def test_a_bad_request_is_refused_before_any_quota_or_state_is_touched(api, body, status):
    handler, state, seen = api
    with pytest.raises(handler.ApiError) as err:
        handler.revise_run(_event(body))
    assert err.value.status == status
    assert seen["slots"] == [] and seen["reserved"] == [] and not state.revision


def test_a_run_that_has_not_finished_cannot_be_changed(api):
    handler, state, seen = api
    state.runs[Stage.COST] = type(state.run(Stage.COST))(Stage.COST)       # cost back to pending
    with pytest.raises(handler.ApiError) as err:
        handler.revise_run(_event({"target": "sow", "instruction": "Add a training section"}))
    assert err.value.status == 409 and "not finished" in str(err.value)
    assert seen["workers"] == []


def test_a_second_change_while_one_is_pending_is_refused(api):
    handler, state, _ = api
    _ask(state, "sow", "Add training")
    with pytest.raises(handler.ApiError) as err:
        handler.revise_run(_event({"target": "cost", "instruction": "Price it on-demand"}))
    assert err.value.status == 409


def test_a_run_is_capped_at_a_number_of_changes(api):
    handler, state, _ = api
    state.revisions = [{"status": "applied"}] * 25
    with pytest.raises(handler.ApiError) as err:
        handler.revise_run(_event({"target": "sow", "instruction": "Add one more section"}))
    assert err.value.status == 409 and "25 changes" in str(err.value)


def test_the_architecture_and_cost_cannot_be_changed_before_there_is_a_design(api):
    handler, state, _ = api
    state.run(Stage.ARCHITECTURE).output = {}
    for target in ("architecture", "cost"):
        with pytest.raises(handler.ApiError) as err:
            handler.revise_run(_event({"target": target, "instruction": "Change the design"}))
        assert err.value.status == 409 and "no architecture design" in str(err.value)


def test_a_run_stopped_by_a_blocked_stage_can_still_be_changed(api):
    """That is the point: the review stopped the run, and this is how a person fixes the SOW."""
    handler, state, seen = api
    state.run(Stage.WA_REVIEW).status = Status.BLOCKED
    for later in STAGES[STAGES.index(Stage.WA_REVIEW) + 1:]:
        state.runs[later] = type(state.run(later))(later)
    assert state.status == Status.BLOCKED and state.next_stage() is None
    assert handler.revise_run(_event({"target": "sow", "instruction": "Fix the high findings"}))["statusCode"] == 202


def test_the_architecture_route_lays_out_the_design_the_agent_prepared(api):
    handler, state, _ = api
    body = json.loads(handler.get_run_architecture(_event())["body"])

    assert body["title"] == "Claims FNOL Assistant" and body["region"] == "us-east-2"
    assert body["zones"] == ["us-east-2a", "us-east-2b"]
    assert [c["service"] for c in body["components"]][1:3] == ["Amazon Bedrock", "Amazon S3"]
    assert body["adr_md"] == "# Architecture Decision Record" and body["usage_assumptions_md"] == "# Usage estimate"
    assert body["assumptions"] == ["18,000 FNOLs per month on average."]
    assert [r["kind"] for r in body["requirements"]] == ["functional", "non_functional", "constraint"]
    assert "png_base64" not in json.dumps(body), "the PNG is served by /diagram, not here"

    state.run(Stage.ARCHITECTURE).output = {}
    with pytest.raises(handler.ApiError) as err:
        handler.get_run_architecture(_event())
    assert err.value.status == 404
    assert "GET /runs/{id}/architecture" in handler.ROUTES and "POST /runs/{id}/revise" in handler.ROUTES


# ----------------------------------------------------------------- the worker
@pytest.fixture
def worker(monkeypatch):
    import worker as w

    seen = {"claimed": [], "scheduled": [], "saved": 0, "logs": []}
    monkeypatch.setattr(w, "_claim", lambda rid, stage: seen["claimed"].append(stage) or True)
    monkeypatch.setattr(w, "save", lambda s: seen.__setitem__("saved", seen["saved"] + 1))
    monkeypatch.setattr(w, "log", lambda rid, stage, msg, level="info": seen["logs"].append((stage, level, msg)))
    monkeypatch.setattr(w, "_schedule_next", lambda rid: seen["scheduled"].append(rid))
    return w, seen


def test_the_worker_applies_a_pending_change_then_schedules_the_stages_it_made_stale(worker, monkeypatch):
    w, seen = worker
    state = _finished_state()
    bid = _block_id(state.sow_markdown, "40 concurrent users")
    agents = _sow_editor([{"type": "edit_proposal", "block_id": bid, "replace": "60 concurrent users"}])
    monkeypatch.setattr(w, "_build_pipeline", lambda rid: _pipeline(agents))
    _ask(state, "sow", "Raise concurrency to 60 users")

    result = w._revise("r" * 32, state)

    assert result == {"status": "running", "target": "sow"}
    assert seen["claimed"] == ["sow_draft"] and seen["saved"] == 1 and seen["scheduled"] == ["r" * 32]


def test_the_worker_does_not_schedule_anything_when_the_change_failed(worker, monkeypatch):
    w, seen = worker
    state = _finished_state()
    agents = FakeAgents(**{"sow:edit": {"status": "failed", "error": "editor down"}})
    monkeypatch.setattr(w, "_build_pipeline", lambda rid: _pipeline(agents))
    _ask(state, "sow", "Add a training section")

    assert w._revise("r" * 32, state)["status"] == "revision_failed"
    assert seen["scheduled"] == [] and seen["saved"] == 1
    assert any(level == "error" and "editor down" in msg for _, level, msg in seen["logs"])


def test_a_large_edit_result_is_read_back_from_s3(monkeypatch):
    import worker as w

    class Table:
        def get_item(self, Key):                  # noqa: N803
            return {"Item": {"status": "completed", "proposals": None, "proposals_key": "sow/jobs/j/proposals.json"}}

    proposals = [{"type": "delete_proposal", "heading": "Out of Scope"}]
    monkeypatch.setenv("SOW_DATA_BUCKET", "sow-bucket")
    monkeypatch.setattr(w, "sow_jobs", lambda: Table())
    monkeypatch.setattr(w, "_s3_text", lambda key: json.dumps(proposals))
    assert w.read_sow_job("j")["proposals"] == proposals

