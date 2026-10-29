"""Audit events for the SOW actions that read or store customer data.

Metadata carries identifiers only, never customer names or document content.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.actions import AuditAction
from app.modules.audit.services import AuditService, create_audit_service


class SowAuditor:
    def __init__(self, session: AsyncSession) -> None:
        self._audit: AuditService = create_audit_service(session)

    async def generation_started(
        self,
        actor_id: uuid.UUID,
        *,
        session_id: str | None,
        opportunity_id: str | None,
        template_id: str,
    ) -> None:
        await self._audit.record(
            AuditAction.SOW_GENERATION_STARTED,
            actor_id=actor_id,
            resource=session_id,
            metadata={"opportunity_id": opportunity_id, "template_id": template_id},
        )

    async def edit_requested(self, actor_id: uuid.UUID, *, template_id: str) -> None:
        await self._audit.record(
            AuditAction.SOW_EDIT_REQUESTED, actor_id=actor_id, metadata={"template_id": template_id}
        )

    async def draft_checked(self, actor_id: uuid.UUID, *, ai_review: bool) -> None:
        await self._audit.record(
            AuditAction.SOW_DRAFT_CHECKED, actor_id=actor_id, metadata={"ai_review": ai_review}
        )

    async def exported(
        self,
        actor_id: uuid.UUID,
        *,
        session_id: str | None,
        opportunity_id: str | None,
        persisted: bool,
    ) -> None:
        await self._audit.record(
            AuditAction.SOW_EXPORTED,
            actor_id=actor_id,
            resource=session_id,
            metadata={"opportunity_id": opportunity_id, "persisted": persisted},
        )
