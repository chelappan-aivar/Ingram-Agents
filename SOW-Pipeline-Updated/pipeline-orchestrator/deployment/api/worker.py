"""ingram-pipeline-worker Lambda: runs ONE stage of one pipeline run, then schedules the next.

One stage per invocation on purpose. The whole sequence is five agents and several model calls,
which runs well past the 15-minute Lambda ceiling, so the worker advances a single stage, saves
the state, and re-invokes itself asynchronously for the next one. A run that blocks or fails
simply stops scheduling.

Invoked with {"run_id": "<32 hex>"}. Asynchronous and with retries off: every attempt costs
model calls, so a retried read timeout would start a second paid stage.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import boto3
from botocore.exceptions import ClientError
from common import log, meta_key, now, table, update
from store import load, save

from orchestrator.agents import AGENT_SOW, AgentCoreCaller, JobPoller
from orchestrator.contracts import REVISION_LABELS, REVISION_TARGETS, STAGE_LABELS, Status
from orchestrator.pipeline import Pipeline

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_lambda = boto3.client("lambda")
_sow_jobs = None


def sow_jobs():
    """The SOW agent's own job table.

    The SOW agent's `generate` and `check` actions are asynchronous: they return immediately and
    report progress on a job item. Reading that table directly is a deliberate, documented
    coupling to one fixed table name (SOW_JOBS_TABLE), chosen over calling the SOW agent's HTTP
    API so a pipeline run does not consume the public API's per-IP rate limit.
    """
    global _sow_jobs
    if _sow_jobs is None:
        _sow_jobs = boto3.resource("dynamodb").Table(os.environ["SOW_JOBS_TABLE"])
    return _sow_jobs


def _s3_text(key: str) -> str:
    body = boto3.client("s3").get_object(Bucket=os.environ["SOW_DATA_BUCKET"], Key=key)["Body"].read()
    return body.decode("utf-8")


def read_sow_job(job_id: str) -> dict[str, Any]:
    """The SOW agent's job item, with anything it moved to S3 loaded back in.

    The agent writes the item as `JOB#<id>` / `META` (sow_generator/jobs.py) and keeps a result inline only
    while it fits a DynamoDB item: a large draft is `markdown: null, markdown_in_s3: true, markdown_key`,
    and a large check result is `check: null, check_key`. An item that does not exist yet is simply "still
    starting", so it comes back empty rather than failing.
    """
    item = sow_jobs().get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"}).get("Item") or {}
    item = json.loads(json.dumps(item, default=str))
    if not os.environ.get("SOW_DATA_BUCKET"):
        return item
    if item.get("markdown_in_s3") and item.get("markdown_key") and not item.get("markdown"):
        item["markdown"] = _s3_text(str(item["markdown_key"]))
    if item.get("check") is None and item.get("check_key"):
        item["check"] = json.loads(_s3_text(str(item["check_key"])))
    # An `edit` job keeps its change proposals the same way (main.py _inline_or_s3).
    if item.get("proposals") is None and item.get("proposals_key"):
        item["proposals"] = json.loads(_s3_text(str(item["proposals_key"])))
    return item


def _claim(run_id: str, stage: str) -> bool:
    """Take the run for this stage. False when another delivery already has it."""
    try:
        table().update_item(
            Key=meta_key(run_id),
            UpdateExpression="SET #status = :running, #stage = :stage, locked_at = :at, updated_at = :at",
            ConditionExpression="attribute_not_exists(locked_at) OR #status <> :running",
            ExpressionAttributeNames={"#status": "status", "#stage": "stage"},
            ExpressionAttributeValues={":running": "running", ":stage": stage, ":at": now()},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _schedule_next(run_id: str) -> None:
    _lambda.invoke(FunctionName=os.environ["WORKER_FUNCTION"], InvocationType="Event",
                   Payload=json.dumps({"run_id": run_id}).encode())


def _build_pipeline(run_id: str) -> Pipeline:
    poller = JobPoller(
        read_sow_job,
        timeout=float(os.environ.get("SOW_JOB_TIMEOUT_SECONDS", "780")),
        interval=float(os.environ.get("SOW_JOB_POLL_SECONDS", "5")),
    )
    return Pipeline(
        AgentCoreCaller(),
        log=lambda stage, message: log(run_id, stage, message),
        poller=poller,
        wa_verify=os.environ.get("WA_VERIFY", "false").lower() == "true",
        combined_verify=os.environ.get("COMBINED_VERIFY", "true").lower() == "true",
    )


def _revise(run_id: str, state: Any) -> dict[str, Any]:
    """Carry out the change a person asked the AI for, then let the normal sequence rebuild what it made stale.

    A change that fails or changes nothing leaves the run exactly as it was, so the run is simply
    done again; only a change that took has stages to re-run.
    """
    target = str(state.revision.get("target") or "")
    stage = REVISION_TARGETS.get(target)
    if not _claim(run_id, stage.value if stage else "revision"):
        print(json.dumps({"run_id": run_id, "message": "skipped: another worker holds this run"}),
              flush=True)
        return {"status": "skipped"}

    entry = _build_pipeline(run_id).revise(state)
    save(state)
    if entry is None:
        return {"status": state.status.value}
    if entry["status"] == "failed":
        log(run_id, "revision", f"The change was not applied: {entry['error']}", level="error")
        return {"status": "revision_failed", "target": target}
    if entry["status"] == "no_change":
        log(run_id, "revision", "The AI made no change to the " + REVISION_LABELS.get(target, target),
            level="warning")
        return {"status": "no_change", "target": target}
    if state.next_stage() is not None:
        log(run_id, "revision", "Change applied. Re-running the stages that depended on it")
        _schedule_next(run_id)
        return {"status": "running", "target": target}
    return {"status": state.status.value, "target": target}


def run(event: dict[str, Any]) -> dict[str, Any]:
    run_id = str(event.get("run_id") or "")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("bad worker event")

    try:
        state = load(run_id)
    except KeyError:
        raise ValueError(f"run {run_id} not found") from None

    if state.revision:
        return _revise(run_id, state)

    stage = state.next_stage()
    if stage is None:
        log(run_id, "done", f"Nothing left to run; the pipeline is {state.status.value}")
        return {"status": state.status.value}

    if not _claim(run_id, stage.value):
        print(json.dumps({"run_id": run_id, "message": "skipped: another worker holds this run"}),
              flush=True)
        return {"status": "skipped"}

    pipeline = _build_pipeline(run_id)
    stage_run = pipeline.advance(state)
    save(state)

    if stage_run is None:
        return {"status": state.status.value}
    if stage_run.status == Status.FAILED:
        log(run_id, stage.value, f"{STAGE_LABELS[stage]} failed: {stage_run.error}", level="error")
        return {"status": "failed", "stage": stage.value}
    if stage_run.status == Status.BLOCKED:
        log(run_id, stage.value, f"{STAGE_LABELS[stage]} stopped for review: {stage_run.detail}",
            level="warning")
        return {"status": "blocked", "stage": stage.value}

    if state.next_stage() is not None:
        _schedule_next(run_id)
        return {"status": "running", "stage": stage.value}

    log(run_id, "done", "Pipeline complete: SOW, Solutions Architect review, Well-Architected "
                        "review, architecture, cost and the final SOW review are all finished")
    return {"status": "completed"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    run_id = str(event.get("run_id") or "")
    try:
        return run(event)
    except Exception as exc:
        # A worker that dies cannot write its own failure, so record it before re-raising.
        if RUN_ID_RE.fullmatch(run_id):
            update(meta_key(run_id), status="failed", error=f"{type(exc).__name__}: {exc}"[:1000],
                   finished_at=now())
            log(run_id, "worker", f"The pipeline stopped unexpectedly: {exc}", level="error")
        raise
