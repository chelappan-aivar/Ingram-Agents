"""Runs the agentic editor for one instruction."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Final

from sow_generator.core.config import SowSettings
from sow_generator.agents.editor import stream_editor
from sow_generator.models import EditInputs, EditorEvent
from sow_generator.schemas import AgentEditRequest
from sow_generator.services.agent_inputs import build_edit_inputs

logger = logging.getLogger(__name__)

GENERIC_FAILURE: Final = "The request could not be completed."

type EditorStreamer = Callable[[SowSettings, EditInputs], AsyncIterator[EditorEvent]]


class EditorService:
    def __init__(self, settings: SowSettings, *, streamer: EditorStreamer = stream_editor) -> None:
        self._settings = settings
        self._streamer = streamer

    async def edit(self, request: AgentEditRequest) -> AsyncIterator[EditorEvent]:
        """Yield editor events; a failure becomes a final `error` event with a generic message."""
        try:
            inputs = await asyncio.to_thread(build_edit_inputs, request)
            async for event in self._streamer(self._settings, inputs):
                yield event
        except Exception:
            logger.exception("editor failed")
            yield {"type": "error", "message": GENERIC_FAILURE}
