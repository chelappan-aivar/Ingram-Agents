"""Server-sent event framing."""

import json
from typing import Any, Final

SSE_MEDIA_TYPE: Final = "text/event-stream"
# Stops proxies (including nginx) from buffering the stream.
SSE_HEADERS: Final = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def format_event(event: dict[str, Any]) -> str:
    """Frame one JSON event as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"
