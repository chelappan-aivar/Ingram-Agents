"""Job progress in DynamoDB, written by the runtime while a background job runs.

The API Lambda creates the item first (JOB#<id> / META, status "queued"); the runtime then updates it:
    status    queued -> running -> completed | failed
    stage     short machine name of the current step (reading_inputs, drafting, saving, done, ...)
    progress  0-100, log lines as JOB#<id> / LOG#<ts>#<rand> items.
Items stay < 400 KB: large results go to S3 and the item keeps only the key (see put_result).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from sow_generator.core import session as aws_session

logger = logging.getLogger(__name__)

# DynamoDB's item limit is 400 KB; keep a wide margin for the other attributes.
MAX_INLINE_BYTES: Final = 300_000

_table: Any = None


def table() -> Any:
    global _table
    if _table is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        _table = aws_session.build_boto_session(region=region).resource("dynamodb").Table(os.environ["RUNS_TABLE"])
    return _table


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def dynamo_safe(value: Any) -> Any:
    """JSON round trip: floats -> Decimal (DynamoDB has no float type), anything odd -> str."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def json_size(value: Any) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


class JobStore:
    """Writes one job's META item and log lines."""

    def __init__(self, job_id: str, *, ttl_days: int | None = None) -> None:
        self.job_id = job_id
        self.key = {"pk": f"JOB#{job_id}", "sk": "META"}
        self._ttl = int(time.time()) + ttl_days * 86400 if ttl_days else None

    def update(self, **fields: Any) -> None:
        fields = {**fields, "updated_at": now()}
        table().update_item(
            Key=self.key,
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": dynamo_safe(v) for k, v in fields.items()},
        )

    def log(self, stage: str, message: str, level: str = "info") -> None:
        at = now()
        item: dict[str, Any] = {"pk": self.key["pk"], "sk": f"LOG#{at}#{secrets.token_hex(3)}", "at": at,
                                "stage": stage, "message": message[:1000], "level": level}
        if self._ttl:
            item["ttl"] = self._ttl
        try:
            table().put_item(Item=dynamo_safe(item))
        except Exception:  # a log line must never fail the job
            logger.warning("job %s: log write failed", self.job_id, exc_info=True)
        logger.info("job %s [%s] %s", self.job_id, stage, message[:300])

    def fail(self, message: str, stage: str = "failed", **fields: Any) -> None:
        self.update(status="failed", stage=stage, progress=100, error=message[:1000], finished_at=now(), **fields)
        self.log(stage, message, level="error")
