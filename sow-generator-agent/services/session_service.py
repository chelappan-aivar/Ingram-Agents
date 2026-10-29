"""Session ids tie a workspace's inputs and outputs together in storage."""

from app.modules.sow_generator.repositories.sow_storage import new_session_id


def mint_session_id() -> str:
    return new_session_id()
