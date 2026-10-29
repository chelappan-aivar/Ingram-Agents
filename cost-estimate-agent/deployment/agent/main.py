"""AgentCore Runtime entrypoint for the Cost estimate agent (container, port 8080, stateless).

Routes on payload["action"]; every reply has "status" ("completed" | "failed").

    {"action": "estimate", "requirements": "<free text>"}
        -> {"status": "completed", "estimate": {...}, "calculator_url", "estimate_name", "services_count",
            "seconds", "checklist": [{"name", "environment"}], "coverage", "attempts", "preflight_rejections",
            "model_id"}
        -> {"status": "failed", "stage": "input" | "estimate" | "validate" | "agent", "error"}
    {"action": "ping"}
        -> {"status": "completed", "service", "model_id", "mcp_command", "mcp_server_version"}   (0 model calls)

The estimate takes a few minutes; the worker Lambda calls it synchronously (AgentCore allows 15 minutes).
"""

from __future__ import annotations

import logging
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from cost_estimate import __version__
from cost_estimate.mcp_tool import MCP_SERVER_VERSION, mcp_command, model_id
from cost_estimate.orchestrator import EstimateFailed, get_orchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("cost_estimate.main")

SERVICE = "ingram-cost-agent"
MIN_CHARS, MAX_CHARS = 30, 20_000

app = BedrockAgentCoreApp()


def _failed(stage: str, error: str) -> dict[str, Any]:
    return {"status": "failed", "stage": stage, "error": error[:2000]}


def estimate(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("requirements")
    if not isinstance(text, str) or not MIN_CHARS <= len(text.strip()) <= MAX_CHARS:
        return _failed("input", f"'requirements' must be text of {MIN_CHARS} to {MAX_CHARS:,} characters")
    logger.info("estimate: %d characters", len(text))
    try:
        result = get_orchestrator().generate_estimate(text.strip())
    except EstimateFailed as exc:
        return _failed(exc.stage, str(exc))
    return {"status": "completed", **result, "model_id": model_id()}


def ping(payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "completed", "service": SERVICE, "version": __version__, "model_id": model_id(),
            "mcp_command": " ".join(mcp_command()), "mcp_server_version": MCP_SERVER_VERSION}


ACTIONS = {"estimate": estimate, "ping": ping}


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _failed("input", "the payload must be a JSON object")
    action = payload.get("action")
    handler = ACTIONS.get(action) if isinstance(action, str) else None
    if handler is None:
        return _failed("input", f"unknown action {action!r}; expected one of {sorted(ACTIONS)}")
    try:
        return handler(payload)
    except Exception as exc:  # never let the runtime return a 500 without a status
        logger.exception("action %s failed", action)
        return _failed("agent", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    app.run()
