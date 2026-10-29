"""Generates a SOW draft: persists the inputs, streams the agent, persists the result."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, Final

from app.core.config import SowSettings
from app.modules.sow_generator.agents.generation import stream_sow_generation
from app.modules.sow_generator.errors import SowInputError, TemplateError
from app.modules.sow_generator.models import (
    Completed,
    Failed,
    GenerationEvent,
    GenerationInputs,
    GenerationStreamEvent,
    SessionStarted,
    TextChunk,
    UsageReport,
)
from app.modules.sow_generator.repositories.sow_storage import SowStorage, new_session_id
from app.modules.sow_generator.schemas import GenerateRequest
from app.modules.sow_generator.services.agent_inputs import (
    build_generation_inputs,
    safe_file_stem,
)

logger = logging.getLogger(__name__)

GENERIC_FAILURE: Final = "The request could not be completed."

type GenerationStreamer = Callable[[SowSettings, GenerationInputs], AsyncIterator[GenerationEvent]]

# Strong references keep detached persistence tasks from being garbage-collected mid-run.
_detached: set[asyncio.Task[None]] = set()


async def _persist_detached(work: Coroutine[Any, Any, None]) -> None:
    """Wait for persistence, but let it finish even if the client disconnects meanwhile."""
    task = asyncio.create_task(work)
    _detached.add(task)
    task.add_done_callback(_detached.discard)
    await asyncio.shield(task)


class GenerationService:
    def __init__(
        self,
        settings: SowSettings,
        storage: SowStorage,
        *,
        streamer: GenerationStreamer = stream_sow_generation,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._streamer = streamer

    async def generate(self, request: GenerateRequest) -> AsyncIterator[GenerationStreamEvent]:
        """Yield the session id, the draft chunks, usage and a closing Completed or Failed."""
        session_id = request.session_id or new_session_id()
        opportunity_id = request.resolved_opportunity_id
        yield SessionStarted(session_id)

        collected: list[str] = []
        try:
            await self._storage.save_inputs(opportunity_id, session_id, request)
            # Reading uploaded PDFs, DOCX and XLSX files is CPU-bound, so keep it off the loop.
            inputs = await asyncio.to_thread(build_generation_inputs, request)
            async for event in self._streamer(self._settings, inputs):
                if isinstance(event, TextChunk):
                    collected.append(event.text)
                    yield event
                elif isinstance(event, UsageReport):
                    yield event
            yield Completed()
        except (SowInputError, TemplateError) as error:
            yield Failed(error.message)
        except Exception:
            logger.exception("generation failed", extra={"session_id": session_id})
            yield Failed(GENERIC_FAILURE)
        finally:
            markdown = "".join(collected).strip()
            if markdown:
                await _persist_detached(
                    self._storage.save_outputs(
                        opportunity_id,
                        session_id,
                        f"{safe_file_stem(request.customer_name)}_SOW",
                        markdown,
                        b"",
                    )
                )
