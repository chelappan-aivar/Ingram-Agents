"""Session-mint contract and the session id format."""

from typing import Annotated, Final

from pydantic import BaseModel, StringConstraints

SESSION_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{8,64}$"

# Server-minted ids are 32 hex characters; clients echo them back, so the format is enforced.
SessionId = Annotated[str, StringConstraints(pattern=SESSION_ID_PATTERN)]


class SessionResponse(BaseModel):
    """Response for the session-mint endpoint consumed by the UI."""

    session_id: str
