"""AgentCore Runtime entrypoint for the Project Catalyst Intake agent.

Called by the intake-api Lambda with any mix of the three sources:

    {"run_id": "<32 hex>",
     "graph_token": "<Outlook (Graph) token>", "days": 1 | 7 | 30,       # only when Microsoft is connected
     "documents": [{"source": "crm" | "notes", "title", "text", "file_name"}],   # CRM rows, seller notes
     "submitted_by": "..."}

The agent reads, triages, matches the records across sources and builds one intake record per
opportunity (intake/pipeline.py). That takes minutes, so it runs in a background thread registered
with add_async_task (the Runtime keeps the session HealthyBusy) and the Lambda gets an answer at once.
"""

import logging
import re
import threading

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from intake.pipeline import run_consolidated
from intake.store import RunStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("intake")

app = BedrockAgentCoreApp()

ALLOWED_DAYS = {1, 7, 30}
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_DOCUMENTS = 60           # CRM rows + seller notes per run (api/uploads.py caps them first)
MAX_DOCUMENT_CHARS = 60_000


def _validate(payload: dict) -> tuple[str | None, list[dict] | None]:
    run_id = str(payload.get("run_id", ""))
    if not RUN_ID_RE.fullmatch(run_id):
        return "run_id is required", None
    if payload.get("graph_token") and payload.get("days") not in ALLOWED_DAYS:
        return "days must be 1, 7 or 30 with graph_token", None
    docs = payload.get("documents") or []
    if not isinstance(docs, list) or len(docs) > MAX_DOCUMENTS:
        return f"documents must be a list of at most {MAX_DOCUMENTS}", None
    clean = []
    for d in docs:
        if not isinstance(d, dict) or d.get("source") not in ("crm", "notes"):
            return 'every document needs source "crm" or "notes"', None
        text = str(d.get("text") or "").strip()
        if not text or len(text) > MAX_DOCUMENT_CHARS:
            return f"every document needs text (1-{MAX_DOCUMENT_CHARS} characters)", None
        clean.append({"source": d["source"], "text": text, "title": str(d.get("title") or "")[:300],
                      "file_name": str(d.get("file_name") or "")[:120]})
    if not clean and not payload.get("graph_token"):
        return "nothing to analyse: connect Outlook or add CRM records / seller notes", None
    return None, clean


@app.entrypoint
def invoke(payload: dict) -> dict:
    if payload.get("action") in ("funding_check", "funding_batch"):
        return _funding_check(payload)
    error, documents = _validate(payload)
    if error:
        return {"ok": False, "error": error}
    run_id = payload["run_id"]
    store = RunStore(run_id)
    task_id = app.add_async_task("intake-run", {"run_id": run_id})

    def runner() -> None:
        try:
            run_consolidated(store, documents, str(payload.get("submitted_by") or "")[:300],
                             payload.get("graph_token"), payload.get("days"))
        except Exception as exc:
            logger.exception("run %s failed", run_id)
            store.fail(str(exc))
        finally:
            app.complete_async_task(task_id)

    threading.Thread(target=runner, name=f"intake-{run_id}", daemon=True).start()
    return {"ok": True, "run_id": run_id, "status": "RUNNING"}


MAX_BATCH = 25
FUNDING_WORKERS = 4


def _funding_checks(payload: dict) -> list[dict] | None:
    """[{check_id, opportunity_id}] from a single check or a batch; None when invalid."""
    items = payload.get("checks") if payload.get("action") == "funding_batch" else [payload]
    if not isinstance(items, list) or not 0 < len(items) <= MAX_BATCH:
        return None
    out = []
    for c in items:
        check_id, opp_id = str((c or {}).get("check_id", "")), str((c or {}).get("opportunity_id", ""))
        if not RUN_ID_RE.fullmatch(check_id) or not GUID_RE.fullmatch(opp_id):
            return None
        out.append({"check_id": check_id, "opportunity_id": opp_id})
    return out


def _funding_check(payload: dict) -> dict:
    """Funding check flow: {"action": "funding_check", "check_id", "opportunity_id"} or
    {"action": "funding_batch", "checks": [{check_id, opportunity_id}, ...]} -> background checks."""
    checks = _funding_checks(payload)
    if checks is None:
        return {"ok": False, "error": f"check_id and opportunity_id are required (at most {MAX_BATCH} checks)"}
    from concurrent.futures import ThreadPoolExecutor

    from funding.pipeline import CheckStore, run_funding_check   # only loaded for this flow

    owner = str(payload.get("owner", ""))[:200]
    checked_by = str(payload.get("checked_by", ""))[:300]
    task_id = app.add_async_task("funding-check", {"checks": len(checks)})

    def one(c: dict) -> None:
        store = CheckStore(c["check_id"])
        try:
            run_funding_check(store, c["check_id"], c["opportunity_id"], owner, checked_by)
        except Exception as exc:
            logger.exception("funding check %s failed", c["check_id"])
            store.fail(str(exc), c["opportunity_id"])

    def runner() -> None:
        try:
            with ThreadPoolExecutor(max_workers=FUNDING_WORKERS) as pool:
                list(pool.map(one, checks))
        finally:
            app.complete_async_task(task_id)

    threading.Thread(target=runner, name=f"funding-{checks[0]['check_id']}", daemon=True).start()
    return {"ok": True, "check_ids": [c["check_id"] for c in checks], "status": "RUNNING"}


if __name__ == "__main__":
    app.run()
