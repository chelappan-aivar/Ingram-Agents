"""Application settings (pydantic-settings), following the diagram_generator pattern. Nothing is hardcoded elsewhere."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"

ROOT_DIR = Path(__file__).resolve().parents[1]              # repository root
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

SONNET_4_6 = "us.anthropic.claude-sonnet-4-6"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- AWS / Bedrock
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_profile: Optional[str] = Field(default=None, alias="AWS_PROFILE")
    model_id: str = Field(default=SONNET_4_6, alias="MODEL_ID")
    # With fallback == model_id no fallback client is built.
    fallback_model_id: Optional[str] = Field(default=SONNET_4_6, alias="FALLBACK_MODEL_ID")
    # Models listed here get a temperature; every other model is called without one.
    model_temperatures: dict[str, float] = Field(default_factory=lambda: {SONNET_4_6: 0.2})
    # Models that reject forced tool use (tool_choice "tool"/"any") get tool_choice "auto" plus a prompt instruction.
    auto_tool_choice_models: list[str] = Field(default_factory=lambda: ["us.anthropic.claude-opus-5-5"])
    max_tokens: int = Field(default=24000, alias="MAX_TOKENS")

    # --- input
    max_upload_mb: int = Field(default=20, alias="MAX_UPLOAD_MB")
    max_text_chars: int = Field(default=150000, alias="MAX_TEXT_CHARS")

    # --- design policy
    # Availability Zones the design spans. Two is the Well-Architected Reliability baseline
    # (REL10-BP02); 1 is a deliberate, documented exception. See adr_generator/availability.py.
    availability_zones: int = Field(default=2, alias="AVAILABILITY_ZONES")

    # --- output
    data_dir: Path = Field(default=ROOT_DIR / "data", alias="DATA_DIR")
    prompts_dir: Path = Field(default=PROMPTS_DIR, alias="PROMPTS_DIR")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    def temperature_for(self, model_id: str) -> Optional[float]:
        return self.model_temperatures.get(model_id)

    def forced_tool_supported(self, model_id: str) -> bool:
        return model_id not in self.auto_tool_choice_models


@lru_cache
def get_settings() -> Settings:
    return Settings()
