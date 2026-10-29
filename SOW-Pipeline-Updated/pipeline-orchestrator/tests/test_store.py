"""Persisting a run between worker invocations, including the S3 offload for large payloads."""
import copy
import sys
from pathlib import Path

import pytest
from fakes import ARCHITECTURE_RESULT, COST_RESULT, SOW_MARKDOWN, UPSTREAM

from orchestrator.contracts import STAGES, PipelineState, Stage, Status

API = Path(__file__).resolve().parents[1] / "deployment" / "api"
sys.path.insert(0, str(API))


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, Item):            # noqa: N803  (boto3 kwarg casing)
        self.items[(Item["pk"], Item["sk"])] = copy.deepcopy(Item)

    def get_item(self, Key):             # noqa: N803
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,  # noqa: N803
                    ExpressionAttributeValues, **kw):
        item = self.items.setdefault((Key["pk"], Key["sk"]), dict(Key))
        for placeholder, name in ExpressionAttributeNames.items():
            item[name] = ExpressionAttributeValues[placeholder.replace("#", ":")]


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **kw):   # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):               # noqa: N803
        class Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        return {"Body": Body(self.objects[Key])}


@pytest.fixture
def store(monkeypatch):
    import common
    import store as store_module

    fake_table, fake_s3 = FakeTable(), FakeS3()
    monkeypatch.setattr(common, "table", lambda: fake_table)
    monkeypatch.setattr(common, "s3", lambda: fake_s3)
    monkeypatch.setattr(common, "bucket", lambda: "test-bucket")
    monkeypatch.setattr(store_module, "table", lambda: fake_table)
    store_module._fake = (fake_table, fake_s3)
    return store_module


def _state() -> PipelineState:
    state = PipelineState(run_id="a" * 32, template_id="aiml/ai-ml",
                          upstream=copy.deepcopy(UPSTREAM), sow_markdown=SOW_MARKDOWN,
                          scope={"in_scope": ["A"], "out_of_scope": ["B"], "source": "sow:OPP-1"})
    state.run(Stage.SOW_DRAFT).status = Status.COMPLETED
    state.sa_signed_off = True
    state.run(Stage.SA_REVIEW).status = Status.COMPLETED
    state.run(Stage.WA_REVIEW).status = Status.COMPLETED
    state.run(Stage.ARCHITECTURE).status = Status.COMPLETED
    state.run(Stage.ARCHITECTURE).output = copy.deepcopy(ARCHITECTURE_RESULT)
    state.run(Stage.COST).status = Status.COMPLETED
    state.run(Stage.COST).output = copy.deepcopy(COST_RESULT)
    return state


def test_a_saved_state_round_trips(store):
    original = _state()
    store.save(original)
    loaded = store.load(original.run_id)

    assert loaded.run_id == original.run_id
    assert loaded.template_id == "aiml/ai-ml"
    assert loaded.sow_markdown == SOW_MARKDOWN
    assert loaded.scope["out_of_scope"] == ["B"]
    assert loaded.upstream["intake"]["customer_name"] == "Fabrikam Insurance"
    assert loaded.next_stage() == Stage.COMBINED_REVIEW
    assert loaded.run(Stage.COST).output["calculator_url"].endswith("id=abc123")
    assert loaded.run(Stage.ARCHITECTURE).output["adr"]["design"]["title"] == "Claims FNOL Assistant"


def test_a_large_payload_is_offloaded_to_s3_and_fetched_back(store):
    state = _state()
    # A real diagram PNG is far over the inline limit.
    state.run(Stage.ARCHITECTURE).output["diagram"]["png_base64"] = "A" * 300_000
    store.save(state)

    _, fake_s3 = store._fake
    assert any("architecture-output" in key for key in fake_s3.objects)
    loaded = store.load(state.run_id)
    assert loaded.run(Stage.ARCHITECTURE).output["diagram"]["png_base64"] == "A" * 300_000


def test_small_payloads_stay_on_the_item(store):
    state = PipelineState(run_id="b" * 32, upstream=copy.deepcopy(UPSTREAM))
    store.save(state)
    _, fake_s3 = store._fake
    assert fake_s3.objects == {}


def test_meta_fields_summarise_the_run_for_the_ui(store):
    state = _state()
    fields = store.meta_fields(state)
    assert fields["stage"] == "combined_review"
    assert fields["progress"] == 83          # 5 of 6 stages complete
    assert fields["calculator_url"].endswith("id=abc123")
    assert fields["stage_status"]["architecture"] == "completed"
    assert fields["stage_status"]["combined_review"] == "pending"


def test_meta_fields_carry_the_first_error(store):
    state = _state()
    state.run(Stage.COST).status = Status.FAILED
    state.run(Stage.COST).error = "the cost agent returned no calculator_url"
    assert store.meta_fields(state)["error"] == "the cost agent returned no calculator_url"
    assert store.meta_fields(state)["status"] == "failed"


def test_loading_an_unknown_run_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.load("c" * 32)


# ----------------------------------------------------------------- the override
def test_reopen_clears_a_block_and_records_that_it_was_overridden(store):
    state = _state()
    state.run(Stage.WA_REVIEW).status = Status.BLOCKED
    state.run(Stage.WA_REVIEW).detail = "1 high finding."
    assert state.status == Status.BLOCKED

    store.reopen(state)
    assert state.run(Stage.WA_REVIEW).status == Status.COMPLETED
    assert "Overridden by the operator" in state.run(Stage.WA_REVIEW).detail
    assert state.stop_on_blocked is False
    assert state.next_stage() == Stage.COMBINED_REVIEW


def test_reopen_leaves_a_failed_stage_alone(store):
    """A failed stage produced no output, so nothing downstream could use it anyway."""
    state = _state()
    state.run(Stage.COST).status = Status.FAILED
    state.run(Stage.COST).error = "boom"
    store.reopen(state)
    assert state.run(Stage.COST).status == Status.FAILED
    assert state.next_stage() is None


# ----------------------------------------------------------------- the UI payload
def test_the_ui_payload_carries_the_wa_findings_and_the_calculator_link(store):
    import handler

    state = _state()
    state.run(Stage.WA_REVIEW).output = {"review": {
        "summary": {"total": 1, "high": 1, "medium": 0, "low": 0},
        "findings": [{"id": "wa_reliability_single_az", "severity": "high", "pillar": "reliability",
                      "title": "Workload scoped to a single Availability Zone",
                      "detail": "d", "suggestion": "s", "evidence": "e"}],
    }}
    payload = handler._state_for_ui(state)

    wa = next(s for s in payload["stages"] if s["stage"] == "wa_review")
    assert wa["summary"]["high"] == 1
    assert wa["findings"][0]["title"] == "Workload scoped to a single Availability Zone"

    cost = next(s for s in payload["stages"] if s["stage"] == "cost")
    assert cost["calculator_url"].endswith("id=abc123")

    # the stage list the UI renders stays in pipeline order
    assert [s["stage"] for s in payload["stages"]] == \
        ["sow_draft", "sa_review", "wa_review", "architecture", "cost", "combined_review"]


def test_the_ui_payload_lists_failed_rules_from_the_combined_review(store):
    import handler

    state = _state()
    state.run(Stage.COMBINED_REVIEW).output = {"check": {
        "summary": {"total": 2, "passed": 1, "failed": 1},
        "rules": [{"id": "pricing-refers-to-cost-estimate", "name": "Pricing refers to the cost estimate",
                   "severity": "required", "status": "fail"},
                  {"id": "scope-section-present", "name": "Scope section present",
                   "severity": "required", "status": "pass"}],
    }}
    combined = next(s for s in handler._state_for_ui(state)["stages"]
                    if s["stage"] == "combined_review")
    assert [r["id"] for r in combined["failed_rules"]] == ["pricing-refers-to-cost-estimate"]


def test_the_ui_payload_is_empty_but_valid_before_anything_runs(store):
    import handler

    payload = handler._state_for_ui(PipelineState(run_id="d" * 32))
    wa = next(s for s in payload["stages"] if s["stage"] == "wa_review")
    assert wa["findings"] == [] and wa["summary"] == {}
    assert payload["status"] == "pending" and payload["progress"] == 0


# ----------------------------------------------------------------- the combined SOW export
def _export_event(run_id="a" * 32):
    return {"pathParameters": {"id": run_id}, "requestContext": {"http": {"sourceIp": "1.2.3.4"}}}


@pytest.fixture
def export_env(monkeypatch, store):
    import handler

    state = _state()
    state.run(Stage.ARCHITECTURE).output["diagram"]["png_base64"] = "UE5H"
    state.sow_markdown = SOW_MARKDOWN.replace("[cost calculator link]", "https://calculator.aws/#/estimate?id=abc123")
    monkeypatch.setenv("SOW_DATA_BUCKET", "sow-bucket")
    monkeypatch.setenv("SOW_AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/sow")
    monkeypatch.setattr(handler, "_meta", lambda rid: {"run_id": rid, "title": "Fabrikam Insurance"})
    monkeypatch.setattr(handler, "load", lambda rid: state)
    monkeypatch.setattr(handler, "log", lambda *a, **k: None)
    monkeypatch.setattr(handler, "_presign", lambda key, name: f"https://s3.example/{key}?name={name}")
    calls = []

    def caller(agent, payload, *, session):
        calls.append((agent, payload, session))
        return {"status": "completed", "key": "sow/OPP/Fabrikam_SOW.docx", "filename": "Fabrikam_SOW.docx"}

    monkeypatch.setattr(handler, "_export_caller", lambda: caller)
    return handler, state, calls


def test_the_combined_sow_is_exported_with_the_diagram_and_the_real_cost_link(export_env):
    import json

    handler, state, calls = export_env
    response = handler.export_run(_export_event())
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["download_url"].startswith("https://s3.example/sow/OPP/") and body["with_diagram"] is True

    agent, payload, session = calls[0]
    assert agent == "sow" and payload["action"] == "export_docx" and len(session) >= 33
    request = payload["request"]
    assert request["customer_name"] == "Fabrikam Insurance" and request["persist_output"] is True
    assert "https://calculator.aws/#/estimate?id=abc123" in request["sow_markdown"]
    assert request["architecture_images"][0]["content_b64"] == "data:image/png;base64,UE5H"


def test_export_waits_for_the_cost_stage_because_the_sow_has_no_link_before_it(export_env):
    handler, state, _ = export_env
    state.run(Stage.COST).status = Status.PENDING
    with pytest.raises(handler.ApiError) as err:
        handler.export_run(_export_event())
    assert err.value.status == 409 and "cost" in str(err.value).lower()


def test_export_without_a_sow_is_refused(export_env):
    handler, state, _ = export_env
    state.sow_markdown = "  "
    with pytest.raises(handler.ApiError) as err:
        handler.export_run(_export_event())
    assert err.value.status == 409


def test_export_reports_when_the_stack_is_not_configured(export_env, monkeypatch):
    handler, _, _ = export_env
    monkeypatch.delenv("SOW_DATA_BUCKET")
    with pytest.raises(handler.ApiError) as err:
        handler.export_run(_export_event())
    assert err.value.status == 501


def test_export_refuses_a_document_stored_outside_the_sow_prefix(export_env, monkeypatch):
    handler, _, _ = export_env
    monkeypatch.setattr(handler, "_export_caller", lambda: (lambda *a, **k: {
        "status": "completed", "key": "other/secret.docx", "filename": "x.docx"}))
    with pytest.raises(handler.ApiError) as err:
        handler.export_run(_export_event())
    assert err.value.status == 502


def test_an_agent_failure_during_export_is_a_502_not_a_crash(export_env, monkeypatch):
    from orchestrator.agents import AgentError

    handler, _, _ = export_env

    def boom(*a, **k):
        raise AgentError("sow", "timed out")

    monkeypatch.setattr(handler, "_export_caller", lambda: boom)
    with pytest.raises(handler.ApiError) as err:
        handler.export_run(_export_event())
    assert err.value.status == 502 and "timed out" in str(err.value)


def test_the_diagram_route_returns_the_png_and_404s_before_the_architecture_stage(export_env, monkeypatch):
    import json

    handler, state, _ = export_env
    response = handler.get_run_diagram(_export_event())
    assert json.loads(response["body"]) == {"png_base64": "UE5H"}

    state.run(Stage.ARCHITECTURE).output = {}
    with pytest.raises(handler.ApiError) as err:
        handler.get_run_diagram(_export_event())
    assert err.value.status == 404
    assert "GET /runs/{id}/diagram" in handler.ROUTES
