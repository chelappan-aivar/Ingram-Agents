"""Per-request settings that control how the agent suggests questions."""

from __future__ import annotations

from dataclasses import asdict, dataclass

SCOPE_CHECKLIST = "checklist"  # Only open checklist topics.
SCOPE_GUIDED = "guided"  # Checklist topics first, plus follow-ups on what was said.
SCOPE_OPEN = "open"  # Any discovery question; the checklist is context only.
SCOPES = (SCOPE_CHECKLIST, SCOPE_GUIDED, SCOPE_OPEN)

FREQUENCY_CONSERVATIVE = "conservative"
FREQUENCY_BALANCED = "balanced"
FREQUENCY_PROACTIVE = "proactive"
FREQUENCIES = (FREQUENCY_CONSERVATIVE, FREQUENCY_BALANCED, FREQUENCY_PROACTIVE)

# What to do with facts from earlier calls with the same account (long-term memory).
PRIOR_CONTEXT_OFF = "off"  # Don't load or use them.
PRIOR_CONTEXT_CONFIRM = "confirm"  # Suggest confirming them instead of asking from scratch.
PRIOR_CONTEXT_SKIP = "skip"  # Treat topics they cover as known; don't ask about them.
PRIOR_CONTEXT_MODES = (PRIOR_CONTEXT_OFF, PRIOR_CONTEXT_CONFIRM, PRIOR_CONTEXT_SKIP)

MAX_SUGGESTIONS_LIMIT = 3
MAX_INSTRUCTIONS_CHARS = 2000

_KEYS = {"scope", "frequency", "max_suggestions", "instructions", "use_prior_context"}


class InvalidAssistConfigError(ValueError):
    """Raised when ``assist_config`` is malformed."""


@dataclass(frozen=True)
class AssistConfig:
    scope: str = SCOPE_CHECKLIST
    frequency: str = FREQUENCY_BALANCED
    max_suggestions: int = 1
    instructions: str = ""  # Free-text guidance from the BA, added to the prompt.
    use_prior_context: str = PRIOR_CONTEXT_CONFIRM  # Only matters for calls with an account.

    @classmethod
    def from_payload(cls, payload: object) -> AssistConfig:
        """Parse ``assist_config``. Missing keys take defaults.

        Raises:
            InvalidAssistConfigError: Unknown keys or invalid values.
        """
        if payload is None:
            return cls()
        if not isinstance(payload, dict):
            raise InvalidAssistConfigError("'assist_config' must be an object")
        unknown = set(payload) - _KEYS
        if unknown:
            raise InvalidAssistConfigError(f"Unknown assist_config keys: {sorted(unknown)}")

        defaults = cls()
        scope = payload.get("scope", defaults.scope)
        if scope not in SCOPES:
            raise InvalidAssistConfigError(f"assist_config.scope must be one of {list(SCOPES)}")

        frequency = payload.get("frequency", defaults.frequency)
        if frequency not in FREQUENCIES:
            raise InvalidAssistConfigError(
                f"assist_config.frequency must be one of {list(FREQUENCIES)}"
            )

        max_suggestions = payload.get("max_suggestions", defaults.max_suggestions)
        if (
            not isinstance(max_suggestions, int)
            or isinstance(max_suggestions, bool)
            or not 1 <= max_suggestions <= MAX_SUGGESTIONS_LIMIT
        ):
            raise InvalidAssistConfigError(
                f"assist_config.max_suggestions must be an integer from 1 to {MAX_SUGGESTIONS_LIMIT}"
            )

        instructions = payload.get("instructions", defaults.instructions)
        if not isinstance(instructions, str):
            raise InvalidAssistConfigError("assist_config.instructions must be a string")
        instructions = instructions.strip()
        if len(instructions) > MAX_INSTRUCTIONS_CHARS:
            raise InvalidAssistConfigError(
                f"assist_config.instructions must be at most {MAX_INSTRUCTIONS_CHARS} characters"
            )

        use_prior_context = payload.get("use_prior_context", defaults.use_prior_context)
        if use_prior_context not in PRIOR_CONTEXT_MODES:
            raise InvalidAssistConfigError(
                f"assist_config.use_prior_context must be one of {list(PRIOR_CONTEXT_MODES)}"
            )
        return cls(scope, frequency, max_suggestions, instructions, use_prior_context)

    def to_dict(self) -> dict:
        return asdict(self)
