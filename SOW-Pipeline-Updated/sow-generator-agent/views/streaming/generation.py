"""SSE framing of the generation stream: session, chunk, usage, done or error."""

from collections.abc import AsyncIterator
from typing import Any

from app.modules.sow_generator.models import (
    Completed,
    Failed,
    GenerationStreamEvent,
    SessionStarted,
    TextChunk,
    UsageReport,
)
from app.modules.sow_generator.schemas import TokenUsage
from app.modules.sow_generator.views.streaming.sse import format_event


def generation_event_payload(event: GenerationStreamEvent) -> dict[str, Any]:
    match event:
        case SessionStarted(session_id):
            return {"type": "session", "session_id": session_id}
        case TextChunk(text):
            return {"type": "chunk", "text": text}
        case UsageReport(input_tokens, output_tokens):
            usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
            return {"type": "usage", "usage": usage.model_dump(by_alias=True)}
        case Completed():
            return {"type": "done"}
        case Failed(message):
            return {"type": "error", "message": message}


async def stream_generation_sse(events: AsyncIterator[GenerationStreamEvent]) -> AsyncIterator[str]:
    async for event in events:
        yield format_event(generation_event_payload(event))
