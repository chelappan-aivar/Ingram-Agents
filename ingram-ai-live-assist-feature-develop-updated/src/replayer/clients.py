"""Senders for the entry point, and a reader for the call's live/paused status."""

from __future__ import annotations

import json

import boto3
from botocore.config import Config

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
DEFAULT_QUALIFIER = "DEFAULT"


class LocalSender:
    """Posts events to a locally running agent (``python main.py``)."""

    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30) -> None:
        import requests  # Dev dependency; not needed in Lambda.

        self._session = requests.Session()
        self._url = base_url.rstrip("/") + "/invocations"
        self._timeout = timeout

    def send(self, event: dict) -> dict:
        response = self._session.post(
            self._url,
            json=event,
            headers={SESSION_HEADER: event["session_id"]},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()


class RuntimeSender:
    """Invokes a deployed AgentCore Runtime with IAM (SigV4) credentials."""

    def __init__(self, runtime_arn: str, *, region: str | None = None, qualifier: str = DEFAULT_QUALIFIER) -> None:
        self._arn = runtime_arn
        self._qualifier = qualifier or DEFAULT_QUALIFIER
        self._client = boto3.client(
            "bedrock-agentcore",
            region_name=region or _region_from_arn(runtime_arn),
            # No automatic retries: a retried turn could be stored twice.
            config=Config(read_timeout=60, retries={"max_attempts": 1}),
        )

    def send(self, event: dict) -> dict:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self._arn,
            qualifier=self._qualifier,
            runtimeSessionId=event["session_id"],
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(event).encode("utf-8"),
        )
        return json.loads(response["response"].read())


class TimelineStatus:
    """Reads a call's status (live, paused, ended) from the timeline table."""

    def __init__(self, table_name: str, *, region: str | None = None) -> None:
        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def __call__(self, session_id: str) -> str | None:
        item = self._table.get_item(
            Key={"session_id": session_id, "sk": "meta"}, ProjectionExpression="#st",
            ExpressionAttributeNames={"#st": "status"},
        ).get("Item")
        return item.get("status") if item else None


def _region_from_arn(arn: str) -> str | None:
    parts = arn.split(":")
    return parts[3] if len(parts) > 3 and parts[3] else None
