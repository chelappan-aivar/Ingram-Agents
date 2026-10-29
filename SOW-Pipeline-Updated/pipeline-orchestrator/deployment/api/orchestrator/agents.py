"""How the orchestrator talks to the three agent runtimes.

`AgentCaller` is the only seam the pipeline knows about, so the whole sequence is testable with
a dictionary of fake replies and no AWS. `AgentCoreCaller` is the real one: it invokes each
agent's Bedrock AgentCore runtime directly, the same way each agent's own worker Lambda does,
rather than going back out through their HTTP APIs and per-IP rate limits.

Two of the SOW agent's actions (`generate`, `check`) are asynchronous: they return
`{"status": "accepted"}` and write progress to the SOW agent's own DynamoDB job table, which the
caller polls. `JobPoller` handles that so the pipeline does not have to care which actions block.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Protocol

AGENT_SOW = "sow"
AGENT_ARCHITECTURE = "architecture"
AGENT_COST = "cost"

AGENTS = (AGENT_SOW, AGENT_ARCHITECTURE, AGENT_COST)

# Env var holding each agent's runtime ARN.
RUNTIME_ARN_ENV = {
    AGENT_SOW: "SOW_AGENT_RUNTIME_ARN",
    AGENT_ARCHITECTURE: "ARCHITECTURE_AGENT_RUNTIME_ARN",
    AGENT_COST: "COST_AGENT_RUNTIME_ARN",
}

# AgentCore requires a session id of at least 33 characters.
SESSION_PREFIX = "catalyst-pipeline"


class AgentError(Exception):
    """An agent could not be reached, or replied with something unusable."""

    def __init__(self, agent: str, message: str) -> None:
        super().__init__(message)
        self.agent = agent


class AgentCaller(Protocol):
    """Invoke one action on one agent and return its JSON reply."""

    def __call__(self, agent: str, payload: dict[str, Any], *, session: str) -> dict[str, Any]:
        ...


def session_id(agent: str, run_id: str, stage: str) -> str:
    """A distinct, long-enough session per stage, so one agent's stages do not share state."""
    raw = f"{SESSION_PREFIX}-{agent}-{stage}-{run_id}"
    return raw if len(raw) >= 33 else raw + "0" * (33 - len(raw))


class AgentCoreCaller:
    """The real caller. One attempt per invoke: a retry would start a second paid model run."""

    def __init__(self, read_timeout: int = 870) -> None:
        import boto3
        from botocore.config import Config

        self._client = boto3.client("bedrock-agentcore", config=Config(
            read_timeout=read_timeout, connect_timeout=20,
            retries={"max_attempts": 1, "mode": "standard"}))

    def _arn(self, agent: str) -> str:
        env = RUNTIME_ARN_ENV.get(agent)
        arn = os.environ.get(env or "")
        if not arn:
            raise AgentError(agent, f"{env} is not configured on this function.")
        return arn

    def __call__(self, agent: str, payload: dict[str, Any], *, session: str) -> dict[str, Any]:
        try:
            response = self._client.invoke_agent_runtime(
                agentRuntimeArn=self._arn(agent),
                qualifier="DEFAULT",
                runtimeSessionId=session,
                contentType="application/json",
                accept="application/json",
                payload=json.dumps(payload).encode("utf-8"),
            )
            body = response["response"].read()
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(agent, f"{type(exc).__name__}: {exc}") from exc
        try:
            result = json.loads(body)
        except ValueError as exc:
            raise AgentError(agent, f"the agent returned a non-JSON reply ({len(body)} bytes)") from exc
        if not isinstance(result, dict):
            raise AgentError(agent, "the agent returned a non-object reply")
        return result


class JobPoller:
    """Waits for one of the SOW agent's background jobs to finish.

    The SOW agent's `generate` and `check` return immediately and report progress on a DynamoDB
    job item. `read` is injected so this is testable, and `sleep` so a test does not actually wait.
    """

    def __init__(self, read: Callable[[str], dict[str, Any]],
                 *, timeout: float = 900.0, interval: float = 3.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._read = read
        self._timeout = timeout
        self._interval = interval
        self._sleep = sleep
        self._clock = clock

    def wait(self, job_id: str, *, on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        deadline = self._clock() + self._timeout
        last_stage = ""
        while True:
            job = self._read(job_id) or {}
            status = str(job.get("status") or "")
            stage = str(job.get("stage") or "")
            if on_progress and stage and stage != last_stage:
                last_stage = stage
                on_progress(job)
            if status == "completed":
                return job
            if status == "failed":
                raise AgentError(AGENT_SOW, str(job.get("error") or "the job failed"))
            if self._clock() >= deadline:
                raise AgentError(AGENT_SOW, f"the job did not finish within {int(self._timeout)}s")
            self._sleep(self._interval)
