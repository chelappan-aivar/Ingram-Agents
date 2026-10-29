"""A sender that runs ``main.agent_invocation`` on one event loop that stays alive, like the runtime.

``meeting.join`` starts a background task, so unlike the one-shot ``EntryPointSender`` in ``test_api``
the loop must outlive each event.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import main


class LoopSender:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.events: list[dict] = []
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def send(self, event: dict) -> dict:
        self.events.append(event)
        future = asyncio.run_coroutine_threadsafe(
            main.agent_invocation(event, SimpleNamespace(session_id=event["session_id"])), self.loop)
        return future.result(timeout=30)

    def wait_idle(self, processor) -> None:
        asyncio.run_coroutine_threadsafe(processor.wait_idle(), self.loop).result(timeout=30)

    def close(self) -> None:
        """Cancel the background tasks (meeting followers) and stop the loop."""
        async def cancel_all() -> None:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(cancel_all(), self.loop).result(timeout=15)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
