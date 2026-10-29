"""Result of the advisory LLM review of a draft."""

from dataclasses import dataclass, field
from typing import Literal

from app.modules.sow_generator.schemas import ConsistencyIssue


@dataclass
class VerificationOutcome:
    """Distinguishes a clean review from a silent failure of the reviewer."""

    status: Literal["ok", "failed", "empty_output"]
    issues: list[ConsistencyIssue] = field(default_factory=list)
    model_id: str = ""
    detail: str = ""
