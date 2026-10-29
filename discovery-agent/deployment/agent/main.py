"""AgentCore Runtime entrypoint for the Project Catalyst Discovery agent.

Called by the ingram-discovery-api Lambda (api/handler.py). Every reply has a "status".

    {"action": "start" (default when absent),
     "engagement": "Acme Corp", "run_id": "2026-10-24T10-00-00Z-1a2b3c",
     "input_prefix": "input/Acme_Corp/", "output_prefix": "output/Acme_Corp/",
     "options": {"narrative": true, "clarify": false, "instructions": "...", "resume": false}}
        -> {"status": "accepted" | "already_started", "run_id", "status_key"}
    {"action": "resume_with_answers", "engagement", "run_id", ...}    phase 2 of a clarify run (answers.json is in S3)
        -> same as start (sets options.resume_with_answers)
    {"action": "ping"}  -> {"status": "ok", ...}                       0 model calls (deploy smoke test)
    anything invalid    -> {"status": "error", "error": "..."}

A run takes many minutes, so start/resume validate the payload, run the pipeline in a background thread registered
with add_async_task / complete_async_task (the session stays HealthyBusy, up to 8 h) and answer at once. The agent
writes its progress to S3 (output/<folder>/<run_id>/status.json, checkpoints under work/); callers poll that file.
"""
import json
import logging
import sys
import threading

from discovery_agent import settings
from discovery_agent.data.storage import default_storage
from discovery_agent.runtime.orchestrator import run_orchestrator
from discovery_agent.runtime.state import RunConfig, RunState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("discovery_agent")

ACTIONS = ("start", "resume_with_answers", "ping")


def handle_invocation(payload: dict, *, storage=None, agent=None, stages=None) -> dict:
    """Run the whole orchestrated pipeline and return the final status document."""
    config = RunConfig.from_payload(payload)
    state = RunState(config, storage or default_storage(), **({"stages": stages} if stages is not None else {}))
    return run_orchestrator(state, agent)


def _background(payload: dict) -> None:
    try:
        handle_invocation(payload)
    except Exception as e:  # noqa: BLE001  (log the type only: messages can carry document content)
        log.error("run crashed error_type=%s", type(e).__name__)


def _already_started(config: RunConfig, storage) -> bool:
    """A retried invocation must not start the same run twice (unless resuming)."""
    key = f"{config.output_dir}status.json"
    if config.resume or config.resume_with_answers or not storage.exists(key):
        return False
    return json.loads(storage.read_bytes(key)).get("state") in ("running", "succeeded")


def start_background(payload: dict, tracker=None, storage=None) -> dict:
    """Validate the payload now, run in a background thread, and answer immediately.

    `tracker` (optional) has add(name, metadata) -> task_id and done(task_id); it tells the runtime the session is busy.
    A bad payload raises ValueError before anything starts. Callers poll `status_key` for the outcome.
    """
    config = RunConfig.from_payload(payload)
    status_key = f"{config.output_dir}status.json"
    if _already_started(config, storage or default_storage()):
        return {"status": "already_started", "run_id": config.run_id, "status_key": status_key}
    task = tracker.add("discovery_run", {"run_id": config.run_id}) if tracker else None

    def work() -> None:
        try:
            _background(payload)
        finally:
            if tracker:
                tracker.done(task)

    threading.Thread(target=work, name=f"run-{config.run_id}", daemon=True).start()
    return {"status": "accepted", "run_id": config.run_id, "status_key": status_key}


def route(payload: dict, tracker=None, storage=None) -> dict:
    """Dispatch on payload["action"]; never raises."""
    if not isinstance(payload, dict):
        return {"status": "error", "error": "payload must be a JSON object"}
    action = payload.get("action") or "start"
    if action not in ACTIONS:
        return {"status": "error", "error": f"unknown action {action!r} (expected one of {', '.join(ACTIONS)})"}
    if action == "ping":
        return {"status": "ok", "agent": "discovery", "region": settings.AWS_REGION,
                "storage": "s3" if settings.S3_BUCKET else "unset",
                "models": {"extraction": settings.MODEL_EXTRACTION, "hidden_signals": settings.MODEL_HIDDEN_SIGNALS,
                           "narrative": settings.MODEL_NARRATIVE, "orchestrator": settings.MODEL_ORCHESTRATOR}}
    if action == "resume_with_answers":
        payload = {**payload, "options": {**(payload.get("options") or {}), "resume_with_answers": True}}
    try:
        return start_background(payload, tracker, storage)
    except ValueError as e:  # payload validation (RunConfig.from_payload): safe to return
        return {"status": "error", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        log.error("start failed error_type=%s", type(e).__name__)
        return {"status": "error", "error": f"could not start the run ({type(e).__name__})"}


# ── AgentCore wrapper ───────────────────────────────────────────────────────────────────────────────────────
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
except ImportError:  # local development without the SDK
    BedrockAgentCoreApp = None

if BedrockAgentCoreApp is not None:
    app = BedrockAgentCoreApp()

    class _SdkTracker:
        def add(self, name, metadata):
            return app.add_async_task(name, metadata)

        def done(self, task_id):
            app.complete_async_task(task_id)

    @app.entrypoint
    def invoke(payload: dict) -> dict:
        return route(payload, _SdkTracker())


if __name__ == "__main__":
    if BedrockAgentCoreApp is not None and len(sys.argv) == 1:
        app.run()  # serves /invocations and /ping on port 8080
    else:  # local run in the foreground: python main.py '{"engagement": "Acme"}'
        print(json.dumps(handle_invocation(json.loads(sys.argv[1])), indent=2))
