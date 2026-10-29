"""The sequencer: the order, the handoffs, and what happens when a stage blocks."""
import copy

from fakes import ARCHITECTURE_RESULT, COST_RESULT, SOW_MARKDOWN, UPSTREAM, FakeAgents

from orchestrator.agents import JobPoller
from orchestrator.contracts import STAGES, PipelineState, Stage, Status
from orchestrator.pipeline import Pipeline, stage_order


def _pipeline(agents: FakeAgents, **kw) -> Pipeline:
    poller = JobPoller(agents.read_job, interval=0, timeout=5,
                       sleep=lambda _s: None, clock=_ticking())
    return Pipeline(agents, poller=poller, **kw)


def _ticking():
    """A monotonic clock that advances on every read, so a poll loop cannot hang a test."""
    counter = {"t": 0.0}

    def clock() -> float:
        counter["t"] += 0.5
        return counter["t"]

    return clock


def _state(**kw) -> PipelineState:
    kw.setdefault("upstream", copy.deepcopy(UPSTREAM))
    # Most tests exercise the agent stages, not the human SA checkpoint, so they sign it off up
    # front; the SA pause has its own coverage.
    kw.setdefault("sa_signed_off", True)
    return PipelineState(run_id="r1", template_id="aiml/ai-ml", **kw)


# ----------------------------------------------------------------- order
def test_the_stage_order_is_sow_sa_review_architecture_cost_review():
    assert stage_order() == ["sow_draft", "sa_review", "wa_review", "architecture", "cost",
                             "combined_review"]
    assert STAGES[0] == Stage.SOW_DRAFT
    assert STAGES.index(Stage.SA_REVIEW) < STAGES.index(Stage.WA_REVIEW)
    assert STAGES.index(Stage.WA_REVIEW) < STAGES.index(Stage.ARCHITECTURE)
    assert STAGES.index(Stage.ARCHITECTURE) < STAGES.index(Stage.COST)
    assert STAGES[-1] == Stage.COMBINED_REVIEW


def test_a_full_run_calls_the_agents_in_order():
    agents = FakeAgents()
    state = _pipeline(agents).run(_state())
    assert agents.actions() == [
        "sow:generate", "sow:wa_review", "architecture:run_pipeline", "cost:estimate", "sow:check",
    ]
    assert state.status == Status.COMPLETED
    assert state.progress() == 100
    assert all(state.run(s).status == Status.COMPLETED for s in STAGES)


def test_advance_runs_exactly_one_stage_so_a_worker_can_persist_between_them():
    agents = FakeAgents()
    pipeline, state = _pipeline(agents), _state()
    assert pipeline.advance(state).stage == Stage.SOW_DRAFT
    assert state.run(Stage.WA_REVIEW).status == Status.PENDING
    assert pipeline.advance(state).stage == Stage.SA_REVIEW      # signed off, so it passes through
    assert pipeline.advance(state).stage == Stage.WA_REVIEW
    assert state.next_stage() == Stage.ARCHITECTURE
    assert pipeline.advance(state) is not None and pipeline.advance(state) is not None
    assert pipeline.advance(state).stage == Stage.COMBINED_REVIEW
    assert pipeline.advance(state) is None          # nothing left
    assert state.finished


# ----------------------------------------------------------------- handoffs
def test_the_sow_generation_is_not_given_architecture_or_cost_blocks():
    """They do not exist yet; that is exactly why the combined review is a separate stage."""
    agents = FakeAgents()
    upstream = {**copy.deepcopy(UPSTREAM), "architecture": {"services": []}, "cost": {"currency": "USD"}}
    _pipeline(agents).run(_state(upstream=upstream))
    sent = agents.payload_for("sow:generate")["request"]["upstream"]
    assert "architecture" not in sent and "cost" not in sent
    assert sent["intake"]["customer_name"] == "Fabrikam Insurance"


def test_the_architecture_stage_receives_the_sows_scope():
    """Sparsh's requirement: architecture cannot run first, it needs the SOW's final scope."""
    agents = FakeAgents()
    _pipeline(agents).run(_state())
    payload = agents.payload_for("architecture:run_pipeline")
    assert payload["srs_text"] == SOW_MARKDOWN
    assert "Amazon Kinesis streaming ingestion" in payload["scope"]["out_of_scope"]
    assert payload["scope"]["source"] == "sow:OPP-2026-0517"


def test_the_cost_stage_receives_the_architectures_usage_figures():
    agents = FakeAgents()
    _pipeline(agents).run(_state())
    requirements = agents.payload_for("cost:estimate")["requirements"]
    assert "Claims FNOL Assistant" in requirements
    assert "invocations_per_month 18,000" in requirements
    assert "2 Availability Zones" in requirements


def test_the_combined_review_receives_both_blocks_including_the_calculator_link():
    agents = FakeAgents()
    _pipeline(agents).run(_state())
    upstream = agents.payload_for("sow:check")["request"]["upstream"]
    assert upstream["cost"]["source_link"] == "https://calculator.aws/#/estimate?id=abc123"
    assert [s["name"] for s in upstream["architecture"]["services"]] == \
        ["Amazon Bedrock", "Amazon S3", "Amazon RDS"]
    assert agents.payload_for("sow:check")["request"]["verify"] is True


def test_a_supplied_sow_skips_generation_but_still_gets_reviewed():
    """The common case: the architect already wrote the SOW by hand."""
    agents = FakeAgents()
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.SOW_DRAFT).status == Status.SKIPPED
    assert "supplied with the request" in state.run(Stage.SOW_DRAFT).detail
    assert "sow:generate" not in agents.actions()
    assert agents.actions()[0] == "sow:wa_review"
    assert state.status == Status.COMPLETED


# ----------------------------------------------------------------- the review gate
def test_a_high_wa_finding_blocks_before_the_diagram_is_drawn():
    """Ashwin's reason for the ordering: stop here, not after the diagram and the price."""
    agents = FakeAgents(**{"sow:wa_review": {
        "status": "completed",
        "review": {"can_proceed": False,
                   "summary": {"total": 1, "high": 1, "medium": 0, "low": 0},
                   "findings": [{"id": "wa_operations_compute_decision_open", "severity": "high",
                                 "title": "Competing compute options left undecided"}]},
    }})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))

    assert state.run(Stage.WA_REVIEW).status == Status.BLOCKED
    assert "Competing compute options" in state.run(Stage.WA_REVIEW).detail
    # the expensive stages never ran
    assert "architecture:run_pipeline" not in agents.actions()
    assert "cost:estimate" not in agents.actions()
    assert state.run(Stage.ARCHITECTURE).status == Status.PENDING
    assert state.status == Status.BLOCKED
    assert state.next_stage() is None          # a human has to act


def test_the_block_can_be_overridden_deliberately():
    agents = FakeAgents(**{"sow:wa_review": {
        "status": "completed",
        "review": {"summary": {"total": 1, "high": 1}, "findings": [
            {"id": "x", "severity": "high", "title": "Single AZ"}]},
    }})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN, stop_on_blocked=False))
    assert state.run(Stage.WA_REVIEW).status == Status.COMPLETED
    assert "architecture:run_pipeline" in agents.actions()
    assert state.status == Status.COMPLETED


def test_medium_and_low_findings_do_not_block():
    agents = FakeAgents(**{"sow:wa_review": {
        "status": "completed",
        "review": {"summary": {"total": 2, "high": 0, "medium": 1, "low": 1}, "findings": []},
    }})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.WA_REVIEW).status == Status.COMPLETED
    assert state.status == Status.COMPLETED


def test_a_failed_required_rule_in_the_combined_review_blocks():
    agents = FakeAgents()

    def check(payload):
        agents.jobs[payload["job_id"]] = {"status": "completed", "stage": "done", "check": {
            "rules": [{"id": "pricing-refers-to-cost-estimate",
                       "name": "Pricing refers to the cost estimate",
                       "severity": "required", "status": "fail"}],
            "issues": [], "summary": {"total": 1, "passed": 0, "failed": 1}}}
        return {"status": "accepted"}

    agents._overrides["sow:check"] = check
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.COMBINED_REVIEW).status == Status.BLOCKED
    assert "Pricing refers to the cost estimate" in state.run(Stage.COMBINED_REVIEW).detail


# ----------------------------------------------------------------- failures
def test_an_agent_failure_stops_the_pipeline_and_names_the_stage():
    agents = FakeAgents(**{"architecture:run_pipeline": {
        "status": "failed", "stage": "adr", "error": "ADR generation failed"}})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.ARCHITECTURE).status == Status.FAILED
    assert state.run(Stage.ARCHITECTURE).error == "ADR generation failed"
    assert "cost:estimate" not in agents.actions()
    assert state.status == Status.FAILED


def test_a_missing_calculator_url_fails_the_cost_stage_loudly():
    """The SOW points at this link instead of restating figures, so a missing link is fatal."""
    agents = FakeAgents(**{"cost:estimate": {"status": "completed", "estimate": {}, "services_count": 0}})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.COST).status == Status.FAILED
    assert "calculator_url" in state.run(Stage.COST).error
    assert "sow:check" not in agents.actions()


def test_an_unpriceable_architecture_fails_rather_than_guessing():
    agents = FakeAgents(**{"architecture:run_pipeline": {"status": "completed", "adr": {}}})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.COST).status == Status.FAILED
    assert "neither usage figures nor components" in state.run(Stage.COST).error


def test_an_empty_sow_draft_fails_the_first_stage():
    agents = FakeAgents()

    def generate(payload):
        agents.jobs[payload["job_id"]] = {"status": "completed", "stage": "done", "markdown": "   "}
        return {"status": "accepted"}

    agents._overrides["sow:generate"] = generate
    state = _pipeline(agents).run(_state())
    assert state.run(Stage.SOW_DRAFT).status == Status.FAILED
    assert "empty draft" in state.run(Stage.SOW_DRAFT).error


def test_a_failed_background_job_surfaces_its_error():
    agents = FakeAgents()

    def generate(payload):
        agents.jobs[payload["job_id"]] = {"status": "failed", "stage": "generating",
                                          "error": "model throttled"}
        return {"status": "accepted"}

    agents._overrides["sow:generate"] = generate
    state = _pipeline(agents).run(_state())
    assert state.run(Stage.SOW_DRAFT).status == Status.FAILED
    assert "model throttled" in state.run(Stage.SOW_DRAFT).error


# ----------------------------------------------------------------- scope warnings
def test_out_of_scope_components_are_reported_but_do_not_stop_the_run():
    """A scope breach needs a human decision, not a crash: carry it into the review."""
    result = copy.deepcopy(ARCHITECTURE_RESULT)
    result["scope_warnings"] = ["C-05 Event Stream (Amazon Kinesis) appears to cover "
                                "\"Amazon Kinesis streaming ingestion\", which the SOW lists as out of scope."]
    agents = FakeAgents(**{"architecture:run_pipeline": result})
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert state.run(Stage.ARCHITECTURE).status == Status.COMPLETED
    assert "outside the agreed scope" in state.run(Stage.ARCHITECTURE).detail
    upstream = agents.payload_for("sow:check")["request"]["upstream"]
    assert any("out of scope" in a for a in upstream["architecture"]["assumptions"])


# ----------------------------------------------------------------- state reporting
def test_state_json_is_stable_for_the_ui():
    agents = FakeAgents()
    state = _pipeline(agents).run(_state())
    payload = state.to_json()
    assert payload["status"] == "completed" and payload["progress"] == 100
    assert [s["stage"] for s in payload["stages"]] == stage_order()
    assert all(s["label"] and s["agent"] for s in payload["stages"])
    assert payload["has_sow"] is True
    assert payload["scope"]["out_of_scope"]


def test_progress_reflects_completed_stages_only():
    agents = FakeAgents()
    pipeline, state = _pipeline(agents), _state()
    assert state.progress() == 0
    pipeline.advance(state)
    assert state.progress() == 17          # 1 of 6 stages
    pipeline.advance(state)
    assert state.progress() == 33          # 2 of 6 stages


def test_the_wa_model_pass_is_off_by_default_and_opt_in():
    agents = FakeAgents()
    _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert agents.payload_for("sow:wa_review")["request"]["verify"] is False

    agents2 = FakeAgents()
    _pipeline(agents2, wa_verify=True).run(_state(sow_markdown=SOW_MARKDOWN))
    assert agents2.payload_for("sow:wa_review")["request"]["verify"] is True


def test_each_stage_gets_its_own_long_enough_session():
    agents = FakeAgents()
    _pipeline(agents).run(_state())
    sessions = [session for _, _, _, session in agents.calls]
    assert all(len(s) >= 33 for s in sessions)
    assert len(set(sessions)) == len(sessions)       # no stage shares another's session


# ----------------------------------------------------------------- the link reaches the SOW
def test_the_real_calculator_link_is_written_into_the_sow_before_the_final_review():
    """The SOW is drafted before the estimate exists; the link must be written back afterwards."""
    agents = FakeAgents()
    state = _pipeline(agents).run(_state(sow_markdown=SOW_MARKDOWN))
    assert "[cost calculator link]" not in state.sow_markdown
    assert "https://calculator.aws/#/estimate?id=abc123" in state.sow_markdown
    reviewed = agents.payload_for("sow:check")["request"]["sow_markdown"]
    assert "https://calculator.aws/#/estimate?id=abc123" in reviewed
    assert state.run(Stage.COST).output["sow_link"] == "placeholder"


def test_the_template_id_reaches_the_wa_review_so_an_assessment_gets_its_profile():
    agents = FakeAgents()
    state = PipelineState(run_id="r1", template_id="cva/full", upstream=copy.deepcopy(UPSTREAM),
                          sow_markdown=SOW_MARKDOWN, sa_signed_off=True)
    _pipeline(agents).run(state)
    assert agents.payload_for("sow:wa_review")["request"]["template_id"] == "cva/full"
