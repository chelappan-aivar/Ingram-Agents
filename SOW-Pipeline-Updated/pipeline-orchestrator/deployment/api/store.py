"""Persisting one PipelineState between worker invocations.

The pipeline runs one stage per Lambda invocation because the whole sequence is five agents and
several model calls, which comfortably exceeds the 15-minute Lambda ceiling. That means the
state has to survive between stages, which is what this module does.

Large values (the SOW markdown, the architecture reply with its diagram PNG) go to S3 and leave
a pointer behind, because a DynamoDB item caps at 400 KB.
"""
from __future__ import annotations

from typing import Any

from common import dynamo_safe, meta_key, now, offload, plain, reload, state_key, table, update

from orchestrator.contracts import STAGES, PipelineState, Stage, StageRun, Status


def save(state: PipelineState, *, refresh_meta: bool = True) -> None:
    """Write the whole state, then refresh the META item the UI reads.

    `refresh_meta=False` writes the state alone. The API uses it when it queues a change on a
    finished run: refreshing META would put the run's status back to "completed" and open a window
    where a second change could be queued on top of the first.
    """
    item = {
        **state_key(state.run_id),
        "run_id": state.run_id,
        "template_id": state.template_id,
        "project_details": state.project_details,
        "stop_on_blocked": state.stop_on_blocked,
        "upstream": offload(state.run_id, "upstream", state.upstream),
        "sow_markdown": offload(state.run_id, "sow", state.sow_markdown),
        "scope": dynamo_safe(state.scope or {}),
        "sa_comments": dynamo_safe(state.sa_comments or []),
        "sa_signed_off": bool(state.sa_signed_off),
        "revision": dynamo_safe(state.revision or {}),
        "revisions": dynamo_safe(state.revisions or []),
        "stages": {
            stage.value: {
                "status": state.run(stage).status.value,
                "error": state.run(stage).error,
                "detail": state.run(stage).detail,
                "seconds": state.run(stage).seconds,
                "output": offload(state.run_id, f"{stage.value}-output", state.run(stage).output),
            }
            for stage in STAGES
        },
        "updated_at": now(),
    }
    table().put_item(Item=dynamo_safe(item))
    if refresh_meta:
        update(meta_key(state.run_id), **meta_fields(state))


def meta_fields(state: PipelineState) -> dict[str, Any]:
    """The denormalised summary the list and detail endpoints read without touching S3."""
    fields: dict[str, Any] = {
        "status": state.status.value,
        "stage": state.stage_of_record.value,
        "progress": state.progress(),
        "stage_status": {s.value: state.run(s).status.value for s in STAGES},
        "stage_detail": {s.value: state.run(s).detail for s in STAGES if state.run(s).detail},
        "error": next((state.run(s).error for s in STAGES if state.run(s).error), None),
    }
    if state.status in (Status.COMPLETED, Status.FAILED, Status.BLOCKED):
        fields["finished_at"] = now()
    # Headline results, so the UI can link out without loading the whole state.
    cost = state.run(Stage.COST).output or {}
    if cost.get("calculator_url"):
        fields["calculator_url"] = cost["calculator_url"]
        fields["estimate_name"] = cost.get("estimate_name")
    wa = (state.run(Stage.WA_REVIEW).output or {}).get("review") or {}
    if wa.get("summary"):
        fields["wa_summary"] = wa["summary"]
    return fields


def load(run_id: str) -> PipelineState:
    """Rebuild the state, pulling any offloaded blob back from S3."""
    item = table().get_item(Key=state_key(run_id)).get("Item")
    if not item:
        raise KeyError(run_id)
    state = PipelineState(
        run_id=run_id,
        template_id=str(item.get("template_id") or ""),
        upstream=reload(item.get("upstream")) or {},
        project_details=str(item.get("project_details") or ""),
        sow_markdown=reload(item.get("sow_markdown")) or "",
        scope=plain(item.get("scope")) or {},
        sa_comments=plain(item.get("sa_comments")) or [],
        sa_signed_off=bool(item.get("sa_signed_off", False)),
        stop_on_blocked=bool(item.get("stop_on_blocked", True)),
        revision=plain(item.get("revision")) or {},
        revisions=plain(item.get("revisions")) or [],
    )
    stored = item.get("stages") or {}
    for stage in STAGES:
        raw = stored.get(stage.value)
        if raw is None:
            # A state saved before this stage existed (e.g. the SA review added later): treat it
            # as not applicable rather than resurrecting a finished run as unstarted.
            state.runs[stage] = StageRun(stage=stage,
                                         status=Status.SKIPPED if stored else Status.PENDING)
            continue
        state.runs[stage] = StageRun(
            stage=stage,
            status=Status(str(raw.get("status") or "pending")),
            output=reload(raw.get("output")) or {},
            error=str(raw.get("error") or ""),
            detail=str(raw.get("detail") or ""),
            seconds=float(raw.get("seconds") or 0),
        )
    return state


def reopen(state: PipelineState) -> PipelineState:
    """Clear a block so the run can continue past it, on a deliberate human decision.

    Only a BLOCKED stage is reopened, and only by marking it completed: a blocked stage ran
    successfully and produced findings (or, for the SA review, is simply waiting), so its output
    is still valid. A FAILED stage is not reopened here, because nothing downstream can trust its
    missing output.

    Overriding a *findings* gate (the Well-Architected or final review) also turns off
    `stop_on_blocked`, because the operator has chosen to proceed despite findings. Signing off
    the Solutions Architect checkpoint does not: it is an expected step, not an override, so the
    later review gates must still be able to stop the run.
    """
    findings_overridden = False
    for stage in STAGES:
        run = state.run(stage)
        if run.status != Status.BLOCKED:
            continue
        run.status = Status.COMPLETED
        if stage == Stage.SA_REVIEW:
            state.sa_signed_off = True
            run.detail = (run.detail + " Approved by the operator.").strip()
        else:
            findings_overridden = True
            run.detail = (run.detail + " Overridden by the operator to continue.").strip()
    if findings_overridden:
        state.stop_on_blocked = False
    return state
