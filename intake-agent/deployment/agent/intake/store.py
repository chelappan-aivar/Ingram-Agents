"""Everything the agent does is written to the intake-runs DynamoDB table.

One partition per run:
    pk = RUN#<run_id>
    sk = META               owner, inputs, status, phase, counts (created by the Lambda)
         EMAIL#<msg_key>    every email and the decision taken for it
         DOC#<key>          every CRM row / seller note and the decision taken for it
         OPP#<group_key>    one consolidated opportunity: intake record, its source documents,
                            matched (found in 2+ sources) and the sources it is missing from
         LOG#<time>#<seq>   agent steps and errors
"""

import itertools
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

MAX_ERROR_CHARS = 1000


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _dynamo_safe(value: Any) -> Any:
    # DynamoDB rejects floats and empty strings in some positions; round-trip through Decimal.
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


class RunStore:
    def __init__(self, run_id: str, table=None) -> None:
        self.run_id = run_id
        self.pk = f"RUN#{run_id}"
        self._table = table or boto3.resource("dynamodb").Table(os.environ["RUNS_TABLE"])
        self._seq = itertools.count()

    def _update_meta(self, **fields: Any) -> None:
        fields["updated_at"] = now()
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": _dynamo_safe(v) for k, v in fields.items()}
        self._table.update_item(
            Key={"pk": self.pk, "sk": "META"},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def log(self, step: str, message: str, level: str = "INFO") -> None:
        ts = now()
        self._table.put_item(Item={
            "pk": self.pk, "sk": f"LOG#{ts}#{next(self._seq):05d}",
            "at": ts, "step": step, "level": level, "message": message[:MAX_ERROR_CHARS],
        })

    def mark_running(self) -> None:
        self._update_meta(status="RUNNING", started_at=now())
        self.log("start", "Intake agent started")

    def update_counts(self, counts: dict[str, int], **extra: Any) -> None:
        self._update_meta(counts=dict(counts), **extra)

    def put_email(self, email: dict[str, Any], msg_key: str, thread_key: str,
                  decision: str, reason: str) -> None:
        self._table.put_item(Item=_dynamo_safe({
            "pk": self.pk, "sk": f"EMAIL#{msg_key}",
            "thread_key": thread_key, "decision": decision, "reason": reason[:MAX_ERROR_CHARS],
            "subject": email.get("subject", "")[:500], "from": email.get("from", "")[:300],
            "received_at": email.get("received_at"), "web_link": email.get("web_link"),
        }))

    def put_document(self, key: str, doc: dict[str, Any], decision: str, reason: str, group_key: str = "") -> None:
        """Decision for one CRM row or seller note (opportunity, skipped or failed)."""
        self._table.put_item(Item=_dynamo_safe({
            "pk": self.pk, "sk": f"DOC#{key}", "decision": decision, "reason": reason[:MAX_ERROR_CHARS],
            "title": doc.get("subject", "")[:500], "file_name": doc.get("file_name", ""), "group_key": group_key,
        }))

    def put_consolidated(self, group_key: str, intake: dict[str, Any], sources: list[dict[str, Any]],
                         source_types: list[str], matched: bool, missing_sources: list[str],
                         dynamics: dict[str, Any] | None = None) -> None:
        """One opportunity built from all of its documents; sources[0] is the primary (shown first)."""
        self._table.put_item(Item=_dynamo_safe({
            "pk": self.pk, "sk": f"OPP#{group_key}", "thread_key": group_key,
            "source": sources[0], "sources": sources, "source_types": source_types,
            "matched": matched, "missing_sources": missing_sources, "intake": intake, "created_at": now(),
            **({"dynamics": dynamics} if dynamics else {}),
        }))

    def finish(self, status: str, counts: dict[str, int]) -> None:
        self._update_meta(status=status, counts=dict(counts), phase="done", finished_at=now())
        self.log("finish", f"Run {status.lower()}: {dict(counts)}")

    def fail(self, error: str) -> None:
        error = error[:MAX_ERROR_CHARS]
        self._update_meta(status="FAILED", error=error, phase="done", finished_at=now())
        self.log("error", error, level="ERROR")
