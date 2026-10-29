"""AgentCore Runtime entrypoint for the Project Catalyst SOW generator.

Called by the ingram-sow-api Lambda. Every reply has "status" ("completed", "accepted" or "failed").

    {"action": "ping"}                                          -> {"status": "completed", ...}   0 model calls
    {"action": "templates"}                                     -> {"status": "completed", "templates": [...]}
    {"action": "template", "id": "aiml/ai-ml"}                  -> {"status": "completed", "id", "markdown"}
    {"action": "generate", "job_id", "request": GenerateRequest}       -> {"status": "accepted"}  (background)
    {"action": "edit", "job_id", "request": AgentEditRequest}          -> {"status": "accepted"}  (background)
    {"action": "wa_review", "request": WaReviewRequest}         -> {"status": "completed", "review": {...}}
    {"action": "check", "job_id", "request": ValidateDraftRequest}     -> {"status": "accepted"}  (background)
    {"action": "export_docx", "request": ExportDocxRequest}     -> {"status": "completed", "key", "filename"}

`wa_review` is the Well-Architected review of the SOW and runs before the architecture diagram is
generated, because the architect settles the service decisions while writing the SOW; reviewing
afterwards costs a loop through the diagram and the cost estimate. It is synchronous: the checks
are deterministic text rules. `{"verify": true}` in the request adds one advisory model call.
`check` is the later combined review, run once the architecture and cost blocks exist.

Generation, editing and the AI review take from seconds to minutes, so they run in a background thread registered
with add_async_task (the Runtime keeps the session HealthyBusy) and write progress to the DynamoDB job item
(sow_generator/jobs.py) and files to S3; the Lambda answers at once and the UI polls GET /jobs/{id}.
Errors that make a request invalid come back synchronously as {"status": "failed", "status_code": 400, ...}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import ValidationError

from sow_generator.core.config import get_app_sow_settings
from sow_generator.core.errors import AppError
from sow_generator.core.s3 import S3Client
from sow_generator.errors import TemplateError
from sow_generator.jobs import MAX_INLINE_BYTES, JobStore, json_size, now
from sow_generator.models import Completed, Failed, SessionStarted, TextChunk, UsageReport
from sow_generator.repositories.sow_storage import create_sow_storage
from sow_generator.repositories.template_repository import load_template
from sow_generator.schemas import (AgentEditRequest, ExportDocxRequest, GenerateRequest,
                                   ValidateDraftRequest, WaReviewRequest)
from sow_generator.services.editor_service import EditorService
from sow_generator.services.export_service import ExportService
from sow_generator.services.generation_service import GenerationService
from sow_generator.services.review_service import ReviewService
from sow_generator.services.template_service import get_template_markdown, list_template_options
from sow_generator.services.wa_review_service import WaReviewService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("sow")

app = BedrockAgentCoreApp()

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FLUSH_SECONDS = 2.0               # write the growing draft to DynamoDB at most this often
EDIT_CHECK_TTL_DAYS = 30          # edit / check jobs expire; generations stay in the history
MAX_ASSISTANT_TEXT = 20_000
MAX_TOOL_CALLS = 60
PROPOSAL_TYPES = {"edit_proposal", "insert_proposal", "insert_after_paragraph_proposal", "delete_proposal"}


class BadPayload(Exception):
    pass


def settings():
    return get_app_sow_settings()


def _failed(message: str, status_code: int = 400, code: str = "invalid_request") -> dict[str, Any]:
    return {"status": "failed", "error": message, "error_code": code, "status_code": status_code}


def _job_id(payload: dict[str, Any]) -> str:
    job_id = str(payload.get("job_id") or "")
    if not JOB_ID_RE.fullmatch(job_id):
        raise BadPayload("job_id must be 32 lowercase hex characters")
    return job_id


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("request")
    if not isinstance(request, dict):
        raise BadPayload("request must be an object")
    return request


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ())) or "request"
    return f"{where}: {first.get('msg', 'invalid value')}"


def _results_key(job_id: str, name: str) -> str:
    return f"{settings().sow_s3_root_prefix}/jobs/{job_id}/{name}"


def _put_s3(key: str, body: bytes, content_type: str) -> bool:
    s = settings()
    if not s.storage_enabled:
        return False
    S3Client(bucket=s.sow_s3_bucket, region=s.aws_region, profile=s.aws_profile).put_object_sync(key, body, content_type)
    return True


def _inline_or_s3(job_id: str, name: str, value: Any) -> dict[str, Any]:
    """{name: value} when it fits the DynamoDB item, else {name + "_key": <S3 key>} (result stored in S3)."""
    if json_size(value) <= MAX_INLINE_BYTES:
        return {name: value}
    key = _results_key(job_id, f"{name}.json")
    if _put_s3(key, json.dumps(value, default=str).encode("utf-8"), "application/json"):
        return {name: None, f"{name}_key": key}
    return {name: None, f"{name}_dropped": True}


# ---------------------------------------------------------------- background jobs
def _start_job(kind: str, job_id: str, work: Callable[[JobStore], Coroutine[Any, Any, None]],
               ttl_days: int | None = None) -> dict[str, Any]:
    store = JobStore(job_id, ttl_days=ttl_days)
    task_id = app.add_async_task(f"sow-{kind}", {"job_id": job_id})

    def runner() -> None:
        started = time.time()
        try:
            asyncio.run(work(store))
        except Exception as exc:
            logger.exception("%s job %s failed", kind, job_id)
            try:
                store.fail(f"{type(exc).__name__}: {exc}"[:500], seconds=round(time.time() - started, 1))
            except Exception:
                logger.exception("could not record the failure of job %s", job_id)
        finally:
            app.complete_async_task(task_id)

    threading.Thread(target=runner, name=f"sow-{kind}-{job_id}", daemon=True).start()
    return {"status": "accepted", "job_id": job_id, "kind": kind}


def _expected_chars(template_id: str) -> int:
    try:
        return max(4000, len(load_template(template_id).sample))
    except TemplateError:
        return 20000


async def _run_generation(store: JobStore, request: GenerateRequest) -> None:
    s = settings()
    started = time.time()
    images = (1 if request.architecture_image_b64 else 0) + len(request.architecture_image_b64s)
    store.update(status="running", stage="reading_inputs", progress=3, started_at=now())
    store.log("reading_inputs", f"Reading inputs: {len(request.transcript_files)} transcript file(s), "
                                f"{images} architecture image(s), template {request.template_id}")
    expected = _expected_chars(request.template_id)
    service = GenerationService(s, create_sow_storage(s))
    chunks: list[str] = []
    chars, last_flush, usage, error = 0, 0.0, {}, None
    async for event in service.generate(request):
        if isinstance(event, TextChunk):
            if not chunks:
                store.log("drafting", f"Drafting the SOW with {s.sow_generation_model}")
            chunks.append(event.text)
            chars += len(event.text)
            if time.time() - last_flush >= FLUSH_SECONDS:
                last_flush = time.time()
                so_far = "".join(chunks)
                store.update(stage="drafting", chars=chars,
                             markdown=so_far if len(so_far.encode("utf-8")) <= MAX_INLINE_BYTES else None,
                             progress=min(95, 10 + int(85 * chars / expected)))
        elif isinstance(event, UsageReport):
            usage = {"input_tokens": event.input_tokens, "output_tokens": event.output_tokens}
        elif isinstance(event, Failed):
            error = event.message
        elif isinstance(event, (SessionStarted, Completed)):
            continue
    markdown = "".join(chunks).strip()
    md_key = next((k for k in service.saved_keys if k.endswith(".md")), None)
    seconds = round(time.time() - started, 1)
    body: dict[str, Any] = {"chars": len(markdown), "usage": usage, "seconds": seconds, "markdown_key": md_key}
    if len(markdown.encode("utf-8")) <= MAX_INLINE_BYTES:
        body.update(markdown=markdown, markdown_in_s3=False)
    else:   # the item would pass DynamoDB's 400 KB limit: S3 only
        body.update(markdown=None, markdown_in_s3=bool(md_key))
    if error or not markdown:
        store.fail(error or "The model returned no draft.", stage="failed", **body)
        return
    store.update(status="completed", stage="done", progress=100, finished_at=now(), **body)
    store.log("done", f"Draft ready: {len(markdown):,} characters ({usage.get('input_tokens', 0):,} in / "
                      f"{usage.get('output_tokens', 0):,} out tokens, {seconds} s)")


def _summarise_tool_call(event: dict[str, Any]) -> dict[str, Any]:
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    keep = {k: str(args[k])[:200] for k in ("heading", "query", "after_heading", "block_id", "table_tag") if args.get(k)}
    return {"tool": str(event.get("tool") or "")[:60], **keep}


async def _run_edit(store: JobStore, request: AgentEditRequest) -> None:
    s = settings()
    started = time.time()
    store.update(status="running", stage="editing", progress=10, started_at=now())
    store.log("editing", f"Editor working on: {request.instruction[:200]}")
    proposals: list[dict[str, Any]] = []
    rejected: list[str] = []
    text: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, Any] = {}
    error = None
    async for event in EditorService(s).edit(request):
        kind = event.get("type")
        if kind == "text":
            text.append(str(event.get("chunk") or ""))
        elif kind == "tool_call_complete":
            if len(tool_calls) < MAX_TOOL_CALLS:
                tool_calls.append(_summarise_tool_call(event))
            store.update(stage="editing", progress=min(90, 10 + 8 * len(tool_calls)))
        elif kind in PROPOSAL_TYPES:
            proposals.append(dict(event))
        elif kind == "tool_result" and event.get("tool") == "edit":   # an edit the tool rejected
            rejected.append(str(event.get("result") or "")[:500])
        elif kind == "done":
            usage = dict(event.get("usage") or {})
        elif kind == "error":
            error = str(event.get("message") or "The editor failed.")
    assistant_text = "".join(text).strip()[:MAX_ASSISTANT_TEXT]
    seconds = round(time.time() - started, 1)
    body = {"assistant_text": assistant_text, "tool_calls": tool_calls, "rejected": rejected[:20],
            "usage": usage, "seconds": seconds, "proposal_count": len(proposals),
            **_inline_or_s3(store.job_id, "proposals", proposals)}
    if error and not proposals:
        store.fail(error, **body)
        return
    store.update(status="completed", stage="done", progress=100, finished_at=now(), **body)
    store.log("done", f"{len(proposals)} change proposal(s) after {len(tool_calls)} tool call(s) ({seconds} s)")


async def _run_check(store: JobStore, request: ValidateDraftRequest) -> None:
    s = settings()
    started = time.time()
    store.update(status="running", stage="ai_review" if request.verify else "rules", progress=20, started_at=now())
    store.log("rules", "Checking the draft against the SOW rules" + (" and the AI reviewer" if request.verify else ""))
    result = (await ReviewService(s).check(request)).model_dump(mode="json")
    seconds = round(time.time() - started, 1)
    store.update(status="completed", stage="done", progress=100, finished_at=now(), seconds=seconds,
                 **_inline_or_s3(store.job_id, "check", result))
    summary = result.get("summary") or {}
    review = result.get("ai_review") or {}
    store.log("done", f"{summary.get('passed', 0)}/{summary.get('total', 0)} rules passed, "
                      f"{len(result.get('issues') or [])} issue(s)"
                      + (f", AI review {review.get('status')}" if review else "") + f" ({seconds} s)")


# ---------------------------------------------------------------- actions
def act_ping(payload: dict[str, Any]) -> dict[str, Any]:
    s = settings()
    return {"status": "completed", "agent": "sow", "model": s.sow_generation_model,
            "verification_model": s.verification_model, "storage": s.storage_enabled}


def act_templates(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "completed", "templates": [t.model_dump() for t in list_template_options()]}


def act_template(payload: dict[str, Any]) -> dict[str, Any]:
    template_id = str(payload.get("id") or "")
    if not TEMPLATE_ID_RE.fullmatch(template_id):
        return _failed("Template not found.", 404, "template_not_found")
    category, stem = template_id.split("/", 1)
    return {"status": "completed", "id": template_id, "markdown": get_template_markdown(category, stem)}


def act_generate(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    request = GenerateRequest.model_validate(_request(payload))
    load_template(request.template_id)          # unknown template -> 404 now, not in the background
    request = request.model_copy(update={"session_id": request.session_id or job_id})
    return _start_job("generation", job_id, lambda store: _run_generation(store, request))


def act_edit(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    request = AgentEditRequest.model_validate(_request(payload))
    return _start_job("edit", job_id, lambda store: _run_edit(store, request), ttl_days=EDIT_CHECK_TTL_DAYS)


def act_check(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _job_id(payload)
    request = ValidateDraftRequest.model_validate(_request(payload))
    return _start_job("check", job_id, lambda store: _run_check(store, request), ttl_days=EDIT_CHECK_TTL_DAYS)


def act_wa_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Well-Architected review of the SOW, run before the architecture diagram is generated.

    Synchronous: the deterministic checks are text rules and finish in milliseconds. `verify: true`
    adds one model call, which is why it is opt-in rather than the default.
    """
    request = WaReviewRequest.model_validate(_request(payload))
    started = time.time()
    result = asyncio.run(WaReviewService(settings()).review(request)).model_dump(mode="json")
    return {"status": "completed", "review": result, "seconds": round(time.time() - started, 2)}


def act_export_docx(payload: dict[str, Any]) -> dict[str, Any]:
    request = ExportDocxRequest.model_validate(_request(payload))
    s = settings()
    started = time.time()
    stored = asyncio.run(ExportService(s, create_sow_storage(s)).export_to_storage(request))
    return {"status": "completed", "key": stored.key, "filename": stored.filename,
            "seconds": round(time.time() - started, 2)}


ACTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ping": act_ping,
    "templates": act_templates,
    "template": act_template,
    "generate": act_generate,
    "edit": act_edit,
    "wa_review": act_wa_review,
    "check": act_check,
    "export_docx": act_export_docx,
}


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed("payload must be a JSON object")
    action = ACTIONS.get(str(payload.get("action") or ""))
    if action is None:
        return _failed(f"unknown action; use one of {', '.join(ACTIONS)}")
    try:
        return action(payload)
    except BadPayload as exc:
        return _failed(str(exc))
    except ValidationError as exc:
        return _failed(_validation_message(exc))
    except AppError as exc:
        return _failed(exc.message, exc.status_code, exc.code)
    except Exception as exc:
        logger.exception("action %s failed", payload.get("action"))
        return _failed(f"{type(exc).__name__}: the request could not be completed", 500, "internal_error")


if __name__ == "__main__":
    app.run()
