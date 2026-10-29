"""Handles ``meeting.join`` inside the AgentCore Runtime session that owns the call.

Sequence: check the link and the keys, send the Vexa bot, answer the caller at once, then follow the
meeting in the background. While that task runs it is registered with the runtime as async work, so the
session reports itself busy and is not shut down as idle (its maximum lifetime still applies).

The Vexa keys arrive in the event, live in this process's memory for the length of the meeting, and are
never written to storage or logs. Messages that go back to the caller are scrubbed of them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.events import STATUS_ENDED, MeetingJoin
from app.meeting_link import InvalidMeetingLinkError, MeetingLink, parse_meeting_link
from app.meeting_source import SOURCE, end_event, run_meeting
from app.timeline_store import TimelineStore
from app.vexa_client import VexaError, VexaSettings, check_read_access, request_bot

logger = logging.getLogger(__name__)

Deliver = Callable[[dict], Awaitable[dict]]


class TaskTracker(Protocol):
    def add_async_task(self, name: str, metadata: dict | None = None) -> int: ...

    def complete_async_task(self, task_id: int) -> bool: ...


def _reject(code: str, message: str, *secrets: str) -> dict:
    for secret in secrets:
        if secret:
            message = message.replace(secret, "***")
    logger.info("meeting.join rejected code=%s", code)
    return {"accepted": False, "code": code, "error": message}


def _vexa_rejection(error: VexaError, *secrets: str) -> dict:
    """Turn a Vexa failure into an ack code the API maps to an HTTP status."""
    if error.status == 409:
        return _reject("meeting_busy", "A bot is already in this meeting.")
    if error.status == 429:
        return _reject("meeting_limit", "The meeting service limit was reached. Try again later.")
    if error.status in (401, 403):
        return _reject("meeting_not_authorized", f"The meeting service refused the key: {error.detail}", *secrets)
    if error.status == 422:
        return _reject("invalid_meeting_link", f"The meeting service could not use this link: {error.detail}", *secrets)
    return _reject("meeting_unavailable", f"The meeting service did not accept the request: {error.detail}", *secrets)


class MeetingRunner:
    def __init__(self, *, timeline: TimelineStore, deliver: Deliver, tracker: TaskTracker | None = None,
                 base_settings: VexaSettings | None = None) -> None:
        self._timeline = timeline
        self._deliver = deliver
        self._tracker = tracker
        self._base = base_settings
        self._tasks: dict[str, asyncio.Task] = {}

    def is_following(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def _settings(self, event: MeetingJoin) -> VexaSettings:
        base = self._base or VexaSettings.from_env(os.environ)  # Non-secret: API address, timing.
        return dataclasses.replace(base, api_key=event.vexa_api_key, tx_key=event.vexa_tx_key)

    async def join(self, event: MeetingJoin) -> dict:
        """Start following the meeting. Returns the ack for the caller."""
        secrets = (event.vexa_api_key, event.vexa_tx_key)
        try:
            link = parse_meeting_link(event.meeting_url)
        except InvalidMeetingLinkError as e:
            return _reject("invalid_meeting_link", str(e))
        try:
            settings = self._settings(event)
        except ValueError:
            return _reject("meeting_not_configured", "The meeting service address is misconfigured on the server.")

        meta = await asyncio.to_thread(self._timeline.get_meta, event.session_id)
        if meta is None:
            return _reject("unknown_session", "Unknown session_id. Send session.start first.")
        if meta.get("status") == STATUS_ENDED:
            return _reject("session_ended", "This session has ended.")
        if meta.get("source") != SOURCE:
            return _reject("source_mismatch", "This call was not started as a meeting call.")
        if self.is_following(event.session_id):
            return {"accepted": True, "meeting": "already_following"}  # A repeated join is harmless.

        try:
            await check_read_access(settings, link)
            await request_bot(settings, link, event.bot_name or "Ingram Live Assist")
        except VexaError as e:
            return _vexa_rejection(e, *secrets)

        self._tasks[event.session_id] = asyncio.get_running_loop().create_task(
            self._follow(event, link, settings), name=f"meeting-{event.session_id}")
        logger.info("meeting.join accepted session=%r platform=%s", event.session_id, link.platform)
        return {"accepted": True, "meeting": "joining", "platform": link.platform}

    async def _follow(self, event: MeetingJoin, link: MeetingLink, settings: VexaSettings) -> None:
        session_id = event.session_id
        task_id = self._tracker.add_async_task("meeting", {"session_id": session_id}) if self._tracker else None
        try:
            await run_meeting(
                session_id=session_id, link=link, ba_names=list(event.ba_names), settings=settings,
                deliver=self._deliver,
                get_status=lambda sid: (self._timeline.get_meta(sid) or {}).get("status"),
                set_bot_status=lambda bot: self._timeline.set_meeting_status(session_id, link.platform, bot),
            )
        except Exception:  # noqa: BLE001 - never leave the call hanging if the source dies
            logger.exception("meeting source crashed session=%r", session_id)
            try:
                await self._deliver(end_event(session_id, "source failed"))
            except Exception:  # noqa: BLE001
                logger.exception("could not end the call after a crash session=%r", session_id)
        finally:
            self._tasks.pop(session_id, None)
            if task_id is not None:
                self._tracker.complete_async_task(task_id)
