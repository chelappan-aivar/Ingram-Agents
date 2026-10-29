"""SOW generator settings, read from environment variables (set on the AgentCore Runtime by template.yaml).

    AWS_REGION                     Bedrock / S3 region (the runtime sets it)
    AWS_PROFILE                    optional named profile (local runs only)
    SOW_GENERATION_MODEL           generation + editor model (Bedrock inference profile)
    SOW_VERIFICATION_MODEL         AI reviewer model
    DATA_BUCKET                    S3 bucket for inputs / outputs (storage disabled when empty)
    SOW_S3_ROOT_PREFIX             key prefix, default "sow"
    SOW_S3_MISC_FOLDER             folder used when there is no opportunity id, default "misc"
    SOW_BEDROCK_SEND_TEMPERATURE   "true" | "false" (default true)
    SOW_BEDROCK_TEMPERATURE        global temperature override (empty = per-role default)
    SOW_BEDROCK_NO_TEMPERATURE_MODEL_PATTERNS   comma-separated regexes; matching models get no temperature
    SOW_<ROLE>_SEND_TEMPERATURE / SOW_<ROLE>_TEMPERATURE   per role: GENERATION, VERIFICATION, EDITOR
    COMPANY_NAME / COMPANY_SHORT_NAME / COMPANY_ABOUT / COMPANY_LOGO_PATH   branding (defaults: Ingram Micro)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache

DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Matches "# About Ingram Micro" in resources/templates/*/*.md, which the generator copies verbatim.
DEFAULT_COMPANY_ABOUT = (
    "Ingram Micro Holding Corporation is a leading global distributor of information technology products, "
    "cloud, software, commerce and fulfillment services, and lifecycle solutions. Founded in 1979 and "
    "headquartered in Irvine, California, the company connects vendors with resellers, managed service "
    "providers, and other channel partners worldwide."
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool | None) -> bool | None:
    raw = _env(name).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _env_float(name: str) -> float | None:
    raw = _env(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """The consultant's branding used in prompts and the .docx (cover, header, acceptance page)."""

    name: str = "Ingram Micro"
    short_name: str = "IngramMicro"
    about: str = DEFAULT_COMPANY_ABOUT
    logo_path: str = ""          # absolute path to a PNG; empty = the packaged Ingram Micro logo


@dataclass(frozen=True, slots=True)
class SowSettings:
    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    sow_generation_model: str = DEFAULT_MODEL
    verification_model: str = DEFAULT_MODEL
    sow_s3_bucket: str = ""
    sow_s3_root_prefix: str = "sow"
    sow_s3_misc_folder: str = "misc"
    sow_bedrock_send_temperature: bool = True
    sow_bedrock_temperature: float | None = None
    sow_bedrock_no_temperature_model_patterns: tuple[str, ...] = ()
    sow_generation_send_temperature: bool | None = None
    sow_generation_temperature: float | None = None
    sow_verification_send_temperature: bool | None = None
    sow_verification_temperature: float | None = None
    sow_editor_send_temperature: bool | None = None
    sow_editor_temperature: float | None = None
    company: CompanyProfile = field(default_factory=CompanyProfile)

    @property
    def storage_enabled(self) -> bool:
        return bool(self.sow_s3_bucket)

    @classmethod
    def from_env(cls) -> SowSettings:
        patterns = tuple(p.strip() for p in _env("SOW_BEDROCK_NO_TEMPERATURE_MODEL_PATTERNS").split(",") if p.strip())
        defaults = CompanyProfile()
        return cls(
            aws_region=_env("AWS_REGION") or _env("AWS_DEFAULT_REGION") or "us-east-1",
            aws_profile=_env("AWS_PROFILE") or None,
            sow_generation_model=_env("SOW_GENERATION_MODEL") or DEFAULT_MODEL,
            verification_model=_env("SOW_VERIFICATION_MODEL") or _env("SOW_GENERATION_MODEL") or DEFAULT_MODEL,
            sow_s3_bucket=_env("DATA_BUCKET"),
            sow_s3_root_prefix=(_env("SOW_S3_ROOT_PREFIX") or "sow").strip("/"),
            sow_s3_misc_folder=_env("SOW_S3_MISC_FOLDER") or "misc",
            sow_bedrock_send_temperature=bool(_env_bool("SOW_BEDROCK_SEND_TEMPERATURE", True)),
            sow_bedrock_temperature=_env_float("SOW_BEDROCK_TEMPERATURE"),
            sow_bedrock_no_temperature_model_patterns=patterns,
            sow_generation_send_temperature=_env_bool("SOW_GENERATION_SEND_TEMPERATURE", None),
            sow_generation_temperature=_env_float("SOW_GENERATION_TEMPERATURE"),
            sow_verification_send_temperature=_env_bool("SOW_VERIFICATION_SEND_TEMPERATURE", None),
            sow_verification_temperature=_env_float("SOW_VERIFICATION_TEMPERATURE"),
            sow_editor_send_temperature=_env_bool("SOW_EDITOR_SEND_TEMPERATURE", None),
            sow_editor_temperature=_env_float("SOW_EDITOR_TEMPERATURE"),
            company=CompanyProfile(
                name=_env("COMPANY_NAME") or defaults.name,
                short_name=_env("COMPANY_SHORT_NAME") or defaults.short_name,
                about=_env("COMPANY_ABOUT") or defaults.about,
                logo_path=_env("COMPANY_LOGO_PATH"),
            ),
        )


@cache
def get_app_sow_settings() -> SowSettings:
    """Settings for this process (read once; tests call get_app_sow_settings.cache_clear())."""
    return SowSettings.from_env()
