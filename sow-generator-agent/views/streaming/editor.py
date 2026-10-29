"""SSE framing of the editor stream."""

from collections.abc import AsyncIterator

from app.modules.sow_generator.models import EditorEvent
from app.modules.sow_generator.views.streaming.sse import format_event


async def stream_editor_sse(events: AsyncIterator[EditorEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield format_event(event)
