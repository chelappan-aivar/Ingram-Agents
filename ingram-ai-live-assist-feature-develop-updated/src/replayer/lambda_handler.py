"""Lambda entry point that runs a call's source: a text recording or audio
through Amazon Transcribe, read from the uploads bucket.

Job (the invocation event):
    {"mode": "recording" | "audio",
     "session_id": "...",
     "bucket": "...", "key": "uploads/recording/<id>/conversation.json",   # recording
     "bucket": "...", "prefix": "uploads/audio/<id>/",                      # audio
     "speed": 1, "max_gap_seconds": 8, "limit_seconds": null,
     "send_start": false,          # the API already sent session.start
     "runtime_arn": "...", "qualifier": "DEFAULT"}

Near the 15-minute limit the function re-invokes itself with its progress
(``start_index`` for text, ``offset_sec`` for audio). If the source fails, the
call is ended with the reason, so the console doesn't wait forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable

from app.conversation_import import parse_conversation
from replayer.core import DEADLINE, ReplayError, Sender, end_event, new_session_id, replay
from replayer.transcribe import (
    MANIFEST_FILE,
    AmazonTranscriber,
    S3Reader,
    parse_manifest,
    stream_call,
)

SAFETY_MARGIN_SECONDS = 60
MAX_JOB_FILE_BYTES = 5 * 1024 * 1024

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def run_job(
    job: dict,
    *,
    sender: Sender,
    s3: Any = None,
    get_status: Callable[[str], str | None] | None = None,
    deadline: float | None = None,
    transcriber_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict, dict | None]:
    """Run a job until it finishes or nears ``deadline``.

    Returns the result and, at the deadline, the fields to continue with.

    Raises:
        ReplayError: An event was rejected, or no progress fit before the deadline.
        ValueError: The job is malformed.
    """
    mode = job.get("mode", "recording")
    session_id = job["session_id"]
    send_start = bool(job.get("send_start", True))

    if mode == "recording":
        data = job["conversation"] if "conversation" in job else _read_json(s3, job["bucket"], job["key"])
        start_index = int(job.get("start_index", 0))
        result = replay(
            session_id=session_id,
            turns=parse_conversation(data),
            sender=sender,
            speed=float(job.get("speed", 1)),
            max_gap_seconds=float(job.get("max_gap_seconds", 8)),
            start_index=start_index,
            title=job.get("title"),
            assist_config=job.get("assist_config"),
            get_status=get_status,
            deadline=deadline,
            now=clock,
            send_start=send_start,
        )
        progressed = result.turns_sent > 0 or (result.next_index or 0) > start_index
        continuation = {"start_index": result.next_index, "send_start": False} if result.stopped == DEADLINE else None

    elif mode == "audio":
        prefix = job["prefix"]
        reader = S3Reader(job["bucket"], prefix, client=s3)
        call = parse_manifest(_read_json(s3, job["bucket"], prefix + MANIFEST_FILE), reader)
        offset = float(job.get("offset_sec", 0.0))
        started_at = (
            datetime.fromisoformat(job["call_started_at"]) if job.get("call_started_at")
            else datetime.now(timezone.utc)
        )
        factory = transcriber_factory or (
            lambda: AmazonTranscriber(os.environ.get("AWS_REGION", "us-east-1"), job.get("language", "en-US"))
        )
        result = asyncio.run(stream_call(
            session_id=session_id,
            call=call,
            sender=sender,
            transcriber_factory=factory,
            speed=float(job.get("speed", 1)),
            title=job.get("title"),
            assist_config=job.get("assist_config"),
            get_status=get_status,
            limit_seconds=job.get("limit_seconds"),
            send_start=send_start and offset == 0,
            start_offset=offset,
            call_started_at=started_at,
            deadline=deadline,
            clock=clock,
        ))
        progressed = result.turns_sent > 0 or (result.next_offset or 0) > offset
        continuation = (
            {"offset_sec": result.next_offset, "call_started_at": started_at.isoformat(), "send_start": False}
            if result.stopped == DEADLINE else None
        )

    else:
        raise ValueError(f"unknown job mode {mode!r}")

    if continuation is not None and not progressed:
        raise ReplayError("no progress fit in one invocation; use a faster speed")
    return asdict(result), continuation


def run_safely(job: dict, *, sender: Sender, **kwargs) -> tuple[dict, dict | None]:
    """``run_job``, but a failure ends the call with the reason instead of raising."""
    try:
        return run_job(job, sender=sender, **kwargs)
    except Exception as e:  # noqa: BLE001 - reported on the call, never retried
        logger.exception("source failed session=%r mode=%s", job.get("session_id"), job.get("mode"))
        reason = f"source failed: {type(e).__name__}"
        try:
            sender.send(end_event(job["session_id"], reason))
        except Exception:  # noqa: BLE001
            logger.exception("could not end the call after a source failure")
        return {"session_id": job.get("session_id"), "stopped": "error", "error": str(e)[:500]}, None


def handler(event: dict, context) -> dict:
    import boto3

    from replayer.clients import DEFAULT_QUALIFIER, RuntimeSender, TimelineStatus

    job = {**event, "session_id": event.get("session_id") or new_session_id()}
    runtime_arn = job.get("runtime_arn") or os.environ["AGENTCORE_RUNTIME_ARN"]
    sender = RuntimeSender(runtime_arn, qualifier=job.get("qualifier") or os.environ.get("AGENTCORE_QUALIFIER", DEFAULT_QUALIFIER))
    table = os.environ.get("TIMELINE_TABLE_NAME")
    deadline = time.monotonic() + context.get_remaining_time_in_millis() / 1000 - SAFETY_MARGIN_SECONDS
    # Settings only, never content.
    logger.info(
        "source start session=%r mode=%s speed=%s limit_seconds=%s max_gap_seconds=%s "
        "start_index=%s offset_sec=%s send_start=%s",
        job["session_id"], job.get("mode"), job.get("speed"), job.get("limit_seconds"),
        job.get("max_gap_seconds"), job.get("start_index"), job.get("offset_sec"), job.get("send_start"),
    )

    result, continuation = run_safely(
        job,
        sender=sender,
        s3=boto3.client("s3"),
        get_status=TimelineStatus(table) if table else None,
        deadline=deadline,
    )
    logger.info("source %s session=%r sent=%s", result.get("stopped"), job["session_id"], result.get("turns_sent"))

    if continuation is not None:
        boto3.client("lambda").invoke(
            FunctionName=context.invoked_function_arn,
            InvocationType="Event",
            Payload=json.dumps({**job, **continuation}).encode("utf-8"),
        )
    return result


def _read_json(s3: Any, bucket: str, key: str) -> Any:
    obj = s3.get_object(Bucket=bucket, Key=key)
    if obj.get("ContentLength", 0) > MAX_JOB_FILE_BYTES:
        raise ValueError(f"{key} is larger than {MAX_JOB_FILE_BYTES} bytes")
    return json.loads(obj["Body"].read())
