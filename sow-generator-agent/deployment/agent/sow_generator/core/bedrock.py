"""Bedrock client settings and the temperature policy shared by the three agent roles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from botocore.config import Config

# Long generations stream for minutes; adaptive retries back off on throttling.
BEDROCK_BOTO_CONFIG: Final = Config(
    read_timeout=600,
    connect_timeout=20,
    retries={"max_attempts": 4, "mode": "adaptive"},
)

# Bedrock's per-image limit for Claude is 3.75 MB (decoded bytes).
BEDROCK_MAX_IMAGE_BYTES: Final = int(3.75 * 1024 * 1024)


@dataclass(frozen=True, slots=True)
class RoleTemperature:
    """Per-role override: send=None / value=None inherit the global setting."""

    send: bool | None = None
    value: float | None = None


@dataclass(frozen=True, slots=True)
class TemperaturePolicy:
    send: bool = True
    value: float | None = None
    no_temperature_patterns: tuple[str, ...] = ()
    roles: dict[str, RoleTemperature] = field(default_factory=dict)

    def temperature_for(self, role: str, model_id: str, default_temperature: float) -> float | None:
        """The temperature to send, or None when the model or configuration says not to send one."""
        role_cfg = self.roles.get(role, RoleTemperature())
        send = self.send if role_cfg.send is None else role_cfg.send
        if not send:
            return None
        for pattern in self.no_temperature_patterns:
            try:
                if re.search(pattern, model_id, flags=re.IGNORECASE):
                    return None
            except re.error:
                if pattern.lower() in model_id.lower():
                    return None
        if role_cfg.value is not None:
            return role_cfg.value
        if self.value is not None:
            return self.value
        return default_temperature


def bedrock_model_kwargs(
    policy: TemperaturePolicy,
    *,
    role: str,
    model_id: str,
    default_temperature: float,
    **options: Any,
) -> dict[str, Any]:
    """Keyword arguments for strands BedrockModel: the model id, options and (when allowed) a temperature."""
    kwargs: dict[str, Any] = {"model_id": model_id, **options}
    temperature = policy.temperature_for(role, model_id, default_temperature)
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs
