"""In-memory stand-in for the ``bedrock-agentcore`` data-plane client (long-term memory).

Records are added directly with ``add_record`` (extraction is AgentCore's job);
``create_event`` only records the call. Namespace arguments match by prefix, as
the service documents for ListMemoryRecords.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import boto3
from botocore.validate import ParamValidator

# Every request is checked against the real service model, so shape mistakes fail tests.
_MODEL = boto3.client("bedrock-agentcore", region_name="us-east-1").meta.service_model
_OPERATIONS = {
    "retrieve_memory_records": "RetrieveMemoryRecords", "list_memory_records": "ListMemoryRecords",
    "create_event": "CreateEvent", "batch_delete_memory_records": "BatchDeleteMemoryRecords",
}


class FakeMemoryClient:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.events: list[dict] = []
        self.calls: list[str] = []
        self.fail = False
        self._ids = itertools.count(1)

    def add_record(self, namespace: str, text: str, *, age_days: float = 0, score: float = 0.5) -> str:
        rid = f"mem-record-{next(self._ids):030d}"  # The service requires 40+ characters.
        self.records[rid] = {
            "memoryRecordId": rid, "content": {"text": text}, "namespaces": [namespace],
            "createdAt": datetime.now(timezone.utc) - timedelta(days=age_days), "score": score,
        }
        return rid

    def _check(self, name: str, params: dict) -> None:
        report = ParamValidator().validate(params, _MODEL.operation_model(_OPERATIONS[name]).input_shape)
        if report.has_errors():
            raise AssertionError(report.generate_report())
        self.calls.append(name)
        if self.fail:
            raise ConnectionError("memory down")

    def _matching(self, prefix: str) -> list[dict]:
        return [r for r in self.records.values() if r["namespaces"][0].startswith(prefix)]

    def retrieve_memory_records(self, **kw):
        self._check("retrieve_memory_records", kw)
        namespace, searchCriteria = kw["namespace"], kw["searchCriteria"]
        words = set(searchCriteria["searchQuery"].lower().split())
        hits = [r for r in self._matching(namespace) if words & set(r["content"]["text"].lower().split())]
        return {"memoryRecordSummaries": hits[: searchCriteria["topK"]]}

    def list_memory_records(self, **kw):
        self._check("list_memory_records", kw)
        namespace, maxResults = kw["namespace"], kw["maxResults"]
        return {"memoryRecordSummaries": self._matching(namespace)[:maxResults]}

    def create_event(self, **kwargs):
        self._check("create_event", kwargs)
        if any(e["clientToken"] == kwargs["clientToken"] for e in self.events):
            return {"event": {}}  # Same token: the service ignores the repeat.
        self.events.append(kwargs)
        return {"event": {}}

    def batch_delete_memory_records(self, **kw):
        self._check("batch_delete_memory_records", kw)
        records = kw["records"]
        for r in records:
            self.records.pop(r["memoryRecordId"], None)
        return {"successfulRecords": records, "failedRecords": []}
