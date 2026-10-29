"""Configuration for the discovery agent, read from environment variables.

In the AgentCore Runtime the variables come from template.yaml (Runtime EnvironmentVariables). On a development
machine set them in the shell; only the model ids have defaults. Nothing account-specific is hard-coded here.

    AWS_REGION            Bedrock + S3 region                        set by the runtime; locally the profile's
    AWS_PROFILE           local named profile (development only)     unset in the runtime: it uses its IAM role
    DATA_BUCKET           S3 bucket with input/, work/, output/      required in the runtime
    KMS_KEY_ID            customer-managed KMS key for S3 writes     empty = the bucket default (aws/s3)
    USE_S3_LOCALLY        "true": use DATA_BUCKET on a dev machine   default false (files under LOCAL_ROOT)
    LOCAL_ROOT            local storage root on a dev machine        default <agent>/samples/_store
    MODEL_EXTRACTION / MODEL_HIDDEN_SIGNALS / MODEL_NARRATIVE / MODEL_TRIM / MODEL_ORCHESTRATOR   Bedrock inference profiles
"""
import os
from pathlib import Path

import boto3


def _env(name: str, default: str | None = None) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or default


# ── AWS ──────────────────────────────────────────────────────────────────────────────────────────────────────
AWS_PROFILE = _env("AWS_PROFILE")  # only set on a development machine; the runtime never looks for a profile
AWS_REGION = _env("AWS_REGION") or _env("AWS_DEFAULT_REGION")  # None: boto3 uses the profile's region

# ── Bedrock models (cross-Region inference profiles; the bare model ids are rejected) ────────────────────────
MODEL_EXTRACTION = _env("MODEL_EXTRACTION", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MODEL_HIDDEN_SIGNALS = _env("MODEL_HIDDEN_SIGNALS", "us.anthropic.claude-opus-5-5")
MODEL_NARRATIVE = _env("MODEL_NARRATIVE", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MODEL_TRIM = _env("MODEL_TRIM", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MODEL_ORCHESTRATOR = _env("MODEL_ORCHESTRATOR", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# ── Limits ───────────────────────────────────────────────────────────────────────────────────────────────────
BEDROCK_READ_TIMEOUT_SECONDS = 900  # botocore's default 60 s broke on 117k-token prompts
MAX_OUTPUT_TOKENS = 64000
CORPUS_TOKEN_BUDGET = 150000  # estimated tokens (chars / 3.5) above which a run refuses to start

# ── Storage ──────────────────────────────────────────────────────────────────────────────────────────────────
# On a development machine (AWS_PROFILE is set) files live under LOCAL_ROOT unless USE_S3_LOCALLY is true.
# Anywhere else, i.e. the AgentCore Runtime, DATA_BUCKET is used.
S3_BUCKET: str | None = _env("DATA_BUCKET")
S3_KMS_KEY_ID: str | None = _env("KMS_KEY_ID")
USE_S3_ON_DEV_MACHINE = (_env("USE_S3_LOCALLY", "false") or "").lower() in ("1", "true", "yes")
LOCAL_ROOT = Path(_env("LOCAL_ROOT") or Path(__file__).resolve().parent.parent / "samples" / "_store")


def aws_session() -> boto3.Session:
    """A boto3 session using AWS_PROFILE when it is set, otherwise the default credential chain (the runtime role)."""
    return boto3.Session(profile_name=AWS_PROFILE if has_local_profile() else None, region_name=AWS_REGION)


def has_local_profile() -> bool:
    """True on a development machine (AWS_PROFILE set); False in the runtime or in CI."""
    return bool(AWS_PROFILE)
