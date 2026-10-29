"""ingram-cost-worker Lambda: runs one estimate on the AgentCore Runtime and stores the result.

Invoked asynchronously by the API Lambda (no retries: every attempt costs model calls) with {"run_id": "<32 hex>"}.

    1. claim the run: queued -> running (conditional update; a duplicate delivery of the same event does nothing)
    2. InvokeAgentRuntime action "estimate" (one call, a few minutes; the agent itself is stateless)
    3. write the summary on RUN#<id>/META (status, calculator_url, estimate_name, services_count, seconds, error),
       the full estimate JSON on RUN#<id>/RESULT and log lines; the UI polls GET /runs/{id}.
Every failure ends as status "failed" with the stage and the error (nothing is swallowed).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from common import dynamo_safe, log, meta_key, now, result_key, table, update

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RESULT_LIMIT = 350_000      # bytes of JSON on RUN#/RESULT (DynamoDB items max out at 400 KB)

# One attempt only: a retried read timeout would start a second, paid estimate on the runtime.
_agentcore = boto3.client("bedrock-agentcore", config=Config(
    read_timeout=870, connect_timeout=20, retries={"max_attempts": 1, "mode": "standard"}))


class StepFailed(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def invoke(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        qualifier="DEFAULT",
        runtimeSessionId=session_id,              # >= 33 characters; one session per run
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["response"].read()
    try:
        result = json.loads(body)
    except ValueError as exc:
        raise StepFailed("agent", f"the agent returned a non-JSON reply ({len(body)} bytes)") from exc
    if not isinstance(result, dict):
        raise StepFailed("agent", "the agent returned a non-object reply")
    return result


def _claim(run_id: str) -> bool:
    """queued -> running, atomically. False when another delivery already took (or finished) the run."""
    try:
        table().update_item(
            Key=meta_key(run_id),
            UpdateExpression="SET #status = :running, #stage = :stage, started_at = :at, updated_at = :at",
            ConditionExpression="#status = :queued",
            ExpressionAttributeNames={"#status": "status", "#stage": "stage"},
            ExpressionAttributeValues={":running": "running", ":queued": "queued", ":stage": "estimate", ":at": now()},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))


def fit_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep RUN#/RESULT under RESULT_LIMIT: drop per-service config first, then everything but the summary."""
    if _size(result) <= RESULT_LIMIT:
        return result
    estimate = json.loads(json.dumps(result.get("estimate") or {}, default=str))
    for env in (estimate.get("environments") or {}).values():
        for svc in (env or {}).get("services") or []:
            if isinstance(svc, dict):
                svc.pop("config", None)
    trimmed = {**result, "estimate": estimate, "truncated": True,
               "note": "The estimate was too large to store in full: per-service configuration was left out. "
                       "Open the calculator link for the complete estimate."}
    if _size(trimmed) <= RESULT_LIMIT:
        return trimmed
    top = {k: v for k, v in estimate.items() if k != "environments"}
    return {"estimate": top, "checklist": (result.get("checklist") or [])[:500], "coverage": None, "truncated": True,
            "note": "The estimate was too large to store: only the summary is kept. "
                    "Open the calculator link for the complete estimate."}


def _fail(run_id: str, stage: str, message: str, started: float) -> dict[str, Any]:
    update(meta_key(run_id), status="failed", stage=stage, error=message[:1000], finished_at=now(),
           seconds=round(time.time() - started, 1))
    log(run_id, stage, f"Estimate failed: {message}", level="error")
    return {"status": "failed", "stage": stage}


def run(event: dict[str, Any]) -> dict[str, Any]:
    run_id = str(event.get("run_id") or "")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("bad worker event")
    meta = table().get_item(Key=meta_key(run_id)).get("Item")
    if not meta:
        raise ValueError(f"run {run_id} not found")
    if not _claim(run_id):
        print(json.dumps({"run_id": run_id, "message": f"skipped: run is already {meta.get('status')}"}), flush=True)
        return {"status": "skipped"}
    started = time.time()
    log(run_id, "estimate", "Building the estimate in the AWS Pricing Calculator with Claude (a few minutes)")
    try:
        result = invoke(f"cost-run-{run_id}", {"action": "estimate", "requirements": meta["requirements"]})
    except StepFailed as exc:
        return _fail(run_id, exc.stage, str(exc), started)
    except Exception as exc:  # timeouts, throttling, access denied: shown to the user, nothing swallowed
        return _fail(run_id, "agent", f"{type(exc).__name__}: {exc}", started)
    if result.get("status") != "completed":
        return _fail(run_id, str(result.get("stage") or "agent"), str(result.get("error") or "the agent failed"),
                     started)

    checklist = result.get("checklist") or []
    stored = fit_result({"estimate": result.get("estimate") or {}, "checklist": checklist,
                         "coverage": result.get("coverage"), "attempts": result.get("attempts"),
                         "preflight_rejections": result.get("preflight_rejections"),
                         "model_id": result.get("model_id")})
    table().put_item(Item={**result_key(run_id), **dynamo_safe(stored), "created_at": now()})
    if stored.get("truncated"):
        log(run_id, "store", stored["note"], level="warning")
    coverage = result.get("coverage") or {}
    missing = coverage.get("missing") or []
    if checklist:
        log(run_id, "checklist", f"Requested services: {len(checklist)}")
    if missing:
        log(run_id, "checklist", f"Not found in the saved estimate: {', '.join(map(str, missing))[:900]}", level="warning")
    seconds = round(time.time() - started, 1)
    update(meta_key(run_id), status="completed", stage="done", finished_at=now(), seconds=seconds, error=None,
           calculator_url=result.get("calculator_url"), estimate_name=result.get("estimate_name"),
           estimate_id=result.get("estimate_id"), services_count=int(result.get("services_count") or 0),
           requested_count=len(checklist), missing_count=len(missing) if coverage.get("missing") is not None else None,
           agent_seconds=result.get("seconds"))
    log(run_id, "done", f"Estimate ready: {int(result.get('services_count') or 0)} services in {seconds} s")
    return {"status": "completed"}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return run(event)

