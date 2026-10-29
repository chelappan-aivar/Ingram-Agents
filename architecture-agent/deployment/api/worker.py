"""ingram-architecture-worker Lambda: runs one ADR version on the AgentCore Runtime, step by step.

Invoked asynchronously by the API Lambda (no retries: every step costs model calls) with
    {"run_id": "<32 hex>", "version": 1, "mode": "new"}
    {"run_id": "<32 hex>", "version": 2, "mode": "revise", "instruction": "...", "base_version": 1}

Steps (each is one InvokeAgentRuntime call; the agent itself is stateless):
    1. extract_srs       uploaded file -> SRS text (new runs with a file only; 0 model calls)
    2. generate_adr      SRS text -> design, adr.md, spec.json            (1 Bedrock call, 2-3 min)
       or revise_adr     previous design + SRS text + instruction -> ...   (1 Bedrock call)
    3. generate_diagram  design -> PNG, SVG, draw.io, report               (0 model calls, ~10 s)
    4. estimate_usage    design + SRS text -> usage.json                   (1 Bedrock call, ~1 min)
A failed diagram or usage step does not discard the ADR: the version ends "partial".
Everything is written to DynamoDB (status, progress, log) and S3 (files) as it happens; the UI polls.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

import boto3
from botocore.config import Config

from common import (RUN_FILES, get_object, log, meta_key, now, put_object, run_prefix, table, update,
                    version_key, vkey)

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SMALL_JSON_LIMIT = 60_000            # assumptions / unresolved / contradictions kept inline in DynamoDB up to this size

_agentcore = boto3.client("bedrock-agentcore", config=Config(
    read_timeout=870, connect_timeout=20, retries={"max_attempts": 2, "mode": "standard"}))


class StepFailed(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def invoke(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = _agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["AGENT_RUNTIME_ARN"],
        qualifier="DEFAULT",
        runtimeSessionId=session_id,              # >= 33 characters; one session per version keeps the container warm
        contentType="application/json",
        accept="application/json",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["response"].read()
    try:
        result = json.loads(body)
    except ValueError as exc:
        raise StepFailed(payload.get("action", "agent"), f"agent returned a non-JSON reply ({len(body)} bytes)") from exc
    return result if isinstance(result, dict) else {"status": "failed", "error": "agent returned a non-object reply"}


def _check(result: dict[str, Any], stage: str) -> dict[str, Any]:
    if result.get("status") != "completed":
        tail = (result.get("log_tail") or "").strip().splitlines()[-1:] or [""]
        raise StepFailed(stage, f"{result.get('error') or 'failed'} {tail[0]}".strip())
    return result


def _inline_or_none(value: Any) -> Any:
    return value if len(json.dumps(value, default=str)) <= SMALL_JSON_LIMIT else None


def _progress(run_id: str, version: int, stage: str, progress: int, message: str) -> None:
    update(meta_key(run_id), stage=stage, progress=progress)
    update(version_key(run_id, version), stage=stage)
    log(run_id, stage, message, version=version)


# ---------------------------------------------------------------- steps
def _srs_text(run_id: str, meta: dict[str, Any], session_id: str, version: int) -> str:
    prefix = run_prefix(run_id)
    try:
        return get_object(f"{prefix}/srs.txt").decode("utf-8")
    except Exception:  # not extracted yet (first version of a file upload)
        pass
    if meta.get("input_kind") != "file":
        raise StepFailed("ingest", "the SRS text is missing")
    name = meta["srs_filename"]
    _progress(run_id, version, "ingest", 5, f"Reading {name}")
    data = get_object(f"{prefix}/input/{name}")
    result = _check(invoke(session_id, {"action": "extract_srs", "srs_filename": name,
                                        "srs_base64": base64.b64encode(data).decode("ascii")}), "ingest")
    text = result["srs_text"]
    put_object(f"{prefix}/srs.txt", text, "text/plain; charset=utf-8")
    note = " (cut to the size limit)" if result.get("truncated") else ""
    update(meta_key(run_id), input_chars=len(text), srs_truncated=bool(result.get("truncated")))
    log(run_id, "ingest", f"Read {len(text):,} characters from {name}{note}", version=version)
    return text


def _adr(run_id: str, version: int, mode: str, instruction: str, meta: dict[str, Any], srs_text: str,
         session_id: str, base_version: int) -> dict[str, Any]:
    if mode == "revise":
        previous = json.loads(get_object(f"{run_prefix(run_id)}/{vkey(base_version)}/design.json"))
        _progress(run_id, version, "adr", 15, "Revising the design with Claude (one model call, a few minutes)")
        payload = {"action": "revise_adr", "run_id": run_id, "design": previous, "srs_text": srs_text,
                   "instruction": instruction}
    else:
        _progress(run_id, version, "adr", 15, "Designing the architecture with Claude (one model call, a few minutes)")
        payload = {"action": "generate_adr", "srs_text": srs_text, "srs_filename": meta.get("srs_filename") or "srs.md"}
    started = time.time()
    result = _check(invoke(session_id, payload), "adr")
    out = f"{run_prefix(run_id)}/{vkey(version)}"
    sizes = {
        "design.json": put_object(f"{out}/design.json", json.dumps(result["design"], ensure_ascii=False, indent=2),
                                  RUN_FILES["design.json"]),
        "adr.md": put_object(f"{out}/adr.md", result["adr_md"], RUN_FILES["adr.md"]),
        "spec.json": put_object(f"{out}/spec.json", json.dumps(result["spec_json"], ensure_ascii=False, indent=2),
                                RUN_FILES["spec.json"]),
    }
    summary = {k: result.get(k) for k in ("counts", "tokens", "seconds", "model_id", "fallback_used", "stop_reason")}
    summary["wall_seconds"] = round(time.time() - started, 1)
    title = (result["design"] or {}).get("title") or meta.get("title")
    update(version_key(run_id, version), adr=summary, files=sizes, title=title)
    if title and not meta.get("title"):
        update(meta_key(run_id), title=title)
    c, t = result.get("counts") or {}, result.get("tokens") or {}
    log(run_id, "adr", f"ADR ready: {c.get('decisions', 0)} decisions, {c.get('components', 0)} components, "
                       f"{c.get('requirements', 0)} requirements ({t.get('input', 0):,} in / {t.get('output', 0):,} "
                       f"out tokens, {summary['wall_seconds']} s)", version=version)
    return {"design": result["design"], "sizes": sizes}


def _diagram(run_id: str, version: int, design: dict[str, Any], session_id: str, sizes: dict[str, int]) -> str:
    _progress(run_id, version, "diagram", 70, "Drawing the AWS architecture diagram (no model call)")
    out = f"{run_prefix(run_id)}/{vkey(version)}"
    try:
        result = invoke(session_id, {"action": "generate_diagram", "design": design})
        d = result.get("diagram") or {}
        if d.get("png_base64"):
            sizes["diagram.png"] = put_object(f"{out}/diagram.png", base64.b64decode(d["png_base64"]), RUN_FILES["diagram.png"])
        if d.get("svg_base64"):
            sizes["diagram.svg"] = put_object(f"{out}/diagram.svg", base64.b64decode(d["svg_base64"]), RUN_FILES["diagram.svg"])
        if d.get("drawio_xml"):
            sizes["diagram.drawio"] = put_object(f"{out}/diagram.drawio", d["drawio_xml"], RUN_FILES["diagram.drawio"])
        report = d.get("report") or {}
        if report:
            sizes["report.json"] = put_object(f"{out}/report.json", json.dumps(report, indent=2), RUN_FILES["report.json"])
        _check(result, "diagram")
        summary = {
            "status": "completed", "report_status": result.get("report_status"),
            "summary": report.get("summary"), "model_calls": report.get("model_calls"),
            "warnings": (report.get("warnings") or [])[:20], "suggestions": (report.get("suggestions") or [])[:20],
            "assumptions": _inline_or_none(d.get("assumptions")), "unresolved": _inline_or_none(d.get("unresolved")),
            "contradictions": _inline_or_none(d.get("contradictions")),
        }
        update(version_key(run_id, version), diagram=summary, files=sizes)
        s = report.get("summary") or {}
        log(run_id, "diagram", f"Diagram ready ({result.get('report_status')}): {s.get('components', 0)} components, "
                               f"{s.get('relationships', 0)} relationships, {s.get('boundaries', 0)} groups",
            version=version)
        return "completed"
    except Exception as exc:  # the ADR is still useful without a diagram
        message = str(exc) if isinstance(exc, StepFailed) else f"{type(exc).__name__}: {exc}"
        update(version_key(run_id, version), diagram={"status": "failed", "error": message[:1000]}, files=sizes)
        log(run_id, "diagram", f"Diagram failed: {message}", level="error", version=version)
        return "failed"


def _usage(run_id: str, version: int, design: dict[str, Any], srs_text: str, meta: dict[str, Any],
           session_id: str, sizes: dict[str, int]) -> str:
    _progress(run_id, version, "usage", 82, "Estimating monthly usage per service with Claude (one model call)")
    out = f"{run_prefix(run_id)}/{vkey(version)}"
    try:
        result = _check(invoke(session_id, {"action": "estimate_usage", "run_id": run_id, "design": design,
                                            "srs_text": srs_text,
                                            "srs_filename": meta.get("srs_filename") or "srs.md"}), "usage")
        sizes["usage.json"] = put_object(f"{out}/usage.json", json.dumps(result["usage_json"], ensure_ascii=False, indent=2),
                                         RUN_FILES["usage.json"])
        sizes["usage_assumptions.md"] = put_object(f"{out}/usage_assumptions.md", result.get("usage_assumptions_md") or "",
                                                   RUN_FILES["usage_assumptions.md"])
        summary = {"status": "completed",
                   **{k: result.get(k) for k in ("counts", "tokens", "seconds", "model_id", "fallback_used")}}
        update(version_key(run_id, version), usage=summary, files=sizes)
        t = result.get("tokens") or {}
        log(run_id, "usage", f"Usage estimate ready ({t.get('input', 0):,} in / {t.get('output', 0):,} out tokens)",
            version=version)
        return "completed"
    except Exception as exc:
        message = str(exc) if isinstance(exc, StepFailed) else f"{type(exc).__name__}: {exc}"
        update(version_key(run_id, version), usage={"status": "failed", "error": message[:1000]}, files=sizes)
        log(run_id, "usage", f"Usage estimate failed: {message}", level="error", version=version)
        return "failed"


# ---------------------------------------------------------------- entry point
def run(event: dict[str, Any]) -> dict[str, Any]:
    run_id, version = str(event.get("run_id") or ""), int(event.get("version") or 0)
    mode, instruction = str(event.get("mode") or "new"), str(event.get("instruction") or "")
    if not RUN_ID_RE.fullmatch(run_id) or version < 1:
        raise ValueError("bad worker event")
    meta = table().get_item(Key=meta_key(run_id)).get("Item")
    if not meta:
        raise ValueError(f"run {run_id} not found")
    session_id = f"arch-run-{run_id}-{vkey(version)}"
    started = time.time()
    update(meta_key(run_id), status="running", stage="starting", progress=2, error=None)
    update(version_key(run_id, version), status="running", started_at=now())
    log(run_id, "start", f"Version {version} started" + (f": {instruction[:200]}" if instruction else ""),
        version=version)
    try:
        srs_text = _srs_text(run_id, meta, session_id, version)
        adr = _adr(run_id, version, mode, instruction, meta, srs_text, session_id,
                   int(event.get("base_version") or version - 1))
        diagram = _diagram(run_id, version, adr["design"], session_id, adr["sizes"])
        usage = _usage(run_id, version, adr["design"], srs_text, meta, session_id, adr["sizes"])
    except Exception as exc:
        stage = exc.stage if isinstance(exc, StepFailed) else "agent"
        message = str(exc) if isinstance(exc, StepFailed) else f"{type(exc).__name__}: {exc}"
        finished = now()
        update(version_key(run_id, version), status="failed", error=message[:1000], finished_at=finished,
               seconds=round(time.time() - started, 1))
        update(meta_key(run_id), status="failed", stage=stage, progress=100, error=message[:1000], finished_at=finished)
        log(run_id, stage, f"Version {version} failed: {message}", level="error", version=version)
        return {"status": "failed", "stage": stage}
    status = "completed" if diagram == usage == "completed" else "partial"
    finished, seconds = now(), round(time.time() - started, 1)
    update(version_key(run_id, version), status=status, finished_at=finished, seconds=seconds, stage="done")
    update(meta_key(run_id), status=status, stage="done", progress=100, finished_at=finished,
           latest_complete_version=version)
    log(run_id, "done", f"Version {version} {'ready' if status == 'completed' else 'ready with gaps (see above)'} "
                        f"in {seconds} s", version=version)
    return {"status": status}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return run(event)
