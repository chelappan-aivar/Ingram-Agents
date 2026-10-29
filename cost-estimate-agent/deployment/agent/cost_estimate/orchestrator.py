"""One cost estimate: MCP calculator tool -> structure check -> summary for the caller.

Same flow as the original agent_orchestrator.py (MCP tool, then validate_cost_data); the result is returned as a
plain dict with a "status" instead of {"success": ...}, and failures name the stage that failed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from cost_estimate.mcp_tool import MCPCalculatorTool
from cost_estimate.validators import validate_cost_data

logger = logging.getLogger(__name__)


class EstimateFailed(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def count_services(cost_data: dict[str, Any]) -> int:
    envs = cost_data.get("environments") or {}
    return sum(len((env or {}).get("services") or []) for env in envs.values() if isinstance(env, dict))


class CostEstimateOrchestrator:
    def __init__(self) -> None:
        self.mcp_tool = MCPCalculatorTool()

    def generate_estimate(self, user_input: str) -> dict[str, Any]:
        """-> {"estimate", "calculator_url", "estimate_name", "services_count", "seconds", "checklist", ...}.

        Raises EstimateFailed(stage, message): stage "estimate" (agent / MCP) or "validate" (bad estimate JSON)."""
        started = time.time()
        report: dict[str, Any] = {}
        logger.info("STEP 1: MCP calculator")
        try:
            cost_data = self.mcp_tool.create_estimate(user_input, report=report)
        except Exception as exc:
            logger.exception("estimate failed")
            raise EstimateFailed("estimate", str(exc)) from exc
        try:
            validate_cost_data(cost_data)
        except Exception as exc:
            logger.warning("estimate JSON failed the structure check: %s", exc)
            raise EstimateFailed("validate", str(exc)) from exc
        seconds = round(time.time() - started, 1)
        logger.info("estimate %s ready in %ss: %s", cost_data.get("estimate_id"), seconds, cost_data.get("calculator_url"))
        return {
            "estimate": cost_data,
            "estimate_id": cost_data.get("estimate_id"),
            "calculator_url": cost_data.get("calculator_url"),
            "estimate_name": cost_data.get("estimate_name"),
            "services_count": count_services(cost_data),
            "seconds": seconds,
            "checklist": report.get("checklist") or [],
            "coverage": report.get("coverage"),
            "attempts": report.get("attempts"),
            "preflight_rejections": report.get("preflight_rejections"),
        }


_orchestrator: CostEstimateOrchestrator | None = None


def get_orchestrator() -> CostEstimateOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CostEstimateOrchestrator()
    return _orchestrator
