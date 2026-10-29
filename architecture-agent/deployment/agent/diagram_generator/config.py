"""Application settings (pydantic-settings). Nothing is hardcoded elsewhere; see ../../ADR.md."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.2.0"

ROOT_DIR = Path(__file__).resolve().parents[1]              # repository root
DEFAULT_VENDOR_DIR = ROOT_DIR / "vendor"

OPUS_5_5 = "us.anthropic.claude-opus-5-5"
SONNET_4_6 = "us.anthropic.claude-sonnet-4-6"

Step = Literal["extractor", "assumer", "coder"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- AWS / Bedrock
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_profile: Optional[str] = Field(default=None, alias="AWS_PROFILE")
    # Owner decision 2026-10-08: all three steps on Sonnet 4.6 for now (Opus 5.5 was too slow on the layout
    # step); switch back to OPUS_5_5 only when the owner says so. With primary == fallback, no fallback is used.
    extractor_model_id: str = Field(default=SONNET_4_6, alias="EXTRACTOR_MODEL_ID")
    assumer_model_id: str = Field(default=SONNET_4_6, alias="ASSUMER_MODEL_ID")
    coder_model_id: str = Field(default=SONNET_4_6, alias="CODER_MODEL_ID")
    fallback_model_id: Optional[str] = Field(default=SONNET_4_6, alias="FALLBACK_MODEL_ID")
    # Models listed here get a temperature; every other model is called WITHOUT one
    # (Opus 5.5 rejects `temperature` with a ValidationException).
    model_temperatures: dict[str, float] = Field(default_factory=lambda: {SONNET_4_6: 0.1})
    # Models that reject forced tool use (tool_choice "tool"/"any"): Opus 5.5 always runs adaptive thinking and
    # only accepts tool_choice "auto". For these the client asks for the tool in the prompt and checks the reply.
    auto_tool_choice_models: list[str] = Field(default_factory=lambda: [OPUS_5_5])
    # Thinking depth for models with always-on adaptive thinking (sent as output_config.effort; other models get
    # nothing). The layout step is mechanical once the rules are known, and at the default "medium" a 36-box
    # layout took more than 7 minutes, so the coder runs at "low".
    effort_models: list[str] = Field(default_factory=lambda: [OPUS_5_5])
    extractor_effort: Optional[str] = Field(default="medium", alias="EXTRACTOR_EFFORT")
    assumer_effort: Optional[str] = Field(default="medium", alias="ASSUMER_EFFORT")
    coder_effort: Optional[str] = Field(default="low", alias="CODER_EFFORT")

    # maxTokens covers Opus 5.5's always-on thinking as well as the answer, so leave generous room.
    extractor_max_tokens: int = 32000
    assumer_max_tokens: int = 24000
    coder_max_tokens: int = 32000

    # --- inputs
    max_files: int = Field(default=10, alias="MAX_FILES")
    max_upload_mb: int = Field(default=20, alias="MAX_UPLOAD_MB")          # per file
    max_text_chars: int = Field(default=150000, alias="MAX_TEXT_CHARS")    # total across files

    # --- harness rules
    max_components_warn: int = 40
    assumption_warn_ratio: float = 0.3      # warn when assumed components > ratio * extracted (min below)
    assumption_warn_min: int = 3
    extractor_repair_attempts: int = 1      # LLM repair calls after Harness 1 errors
    coder_max_retries: int = Field(default=2, alias="CODER_MAX_RETRIES")

    # --- accuracy / cost controls
    # elk: deterministic layout (render/elk_layout.py, no model call); llm: the Diagram coder picks coordinates.
    # With elk, the LLM coder is only used as a fallback when ELK cannot run or its layout fails the checks.
    layout_engine: Literal["elk", "llm"] = Field(default="elk", alias="LAYOUT_ENGINE")
    # What the Assumer's additions do (document input only; a design.json input has no Assumer):
    #   suggest: listed in assumptions.json and the report, never drawn (default: no unrequested services)
    #   draw:    drawn as "assumed" items (the old behaviour)
    #   off:     the Assumer is not called (one model call fewer)
    assumptions_mode: Literal["suggest", "draw", "off"] = Field(default="suggest", alias="ASSUMPTIONS_MODE")
    # Drop an Extractor item marked "inferred" when it is an AWS service the documents never name.
    named_services_only: bool = Field(default=True, alias="NAMED_SERVICES_ONLY")

    # --- draw.io toolchain
    vendor_dir: Path = Field(default=DEFAULT_VENDOR_DIR, alias="VENDOR_DIR")
    drawio_cli: Optional[str] = Field(default=None, alias="DRAWIO_CLI")    # auto-detect when unset
    node_bin: Optional[str] = Field(default=None, alias="NODE_BIN")        # edge router runtime; `node` on PATH when unset
    # libavoid edge routing (render/edge_router.py). Tuned on 11 saved real runs (12-35 components): without the
    # crossing penalty (libavoid's default 0) crossings rose above the old LLM-routed diagrams; draw.io's
    # 16px buffer / 14px nudge left parallel edges stacked in narrow lanes, 10/10 separates them.
    routing_crossing_penalty: int = Field(default=400, alias="ROUTING_CROSSING_PENALTY")
    routing_shape_buffer: int = Field(default=10, alias="ROUTING_SHAPE_BUFFER")    # min gap between edge and box
    routing_nudge_distance: int = Field(default=10, alias="ROUTING_NUDGE_DISTANCE")  # gap between parallel edges
    tool_timeout_sec: int = 30              # vendored fixers / validator
    export_timeout_sec: int = 120           # drawio CLI export
    svg_force_light: bool = True

    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def skill_dir(self) -> Path:
        return self.vendor_dir / "aws-architecture-diagram"

    @property
    def plugin_lib_dir(self) -> Path:
        return self.vendor_dir / "deploy-on-aws-plugin" / "scripts" / "lib"

    @property
    def aws4_shapes_path(self) -> Path:
        return self.plugin_lib_dir / "aws4-shapes.json"

    def model_for(self, step: Step) -> str:
        return {"extractor": self.extractor_model_id, "assumer": self.assumer_model_id,
                "coder": self.coder_model_id}[step]

    def max_tokens_for(self, step: Step) -> int:
        return {"extractor": self.extractor_max_tokens, "assumer": self.assumer_max_tokens,
                "coder": self.coder_max_tokens}[step]

    def temperature_for(self, model_id: str) -> Optional[float]:
        return self.model_temperatures.get(model_id)

    def forced_tool_supported(self, model_id: str) -> bool:
        return model_id not in self.auto_tool_choice_models

    def effort_for(self, step: Step, model_id: str) -> Optional[str]:
        if model_id not in self.effort_models:
            return None
        return {"extractor": self.extractor_effort, "assumer": self.assumer_effort, "coder": self.coder_effort}[step]


@lru_cache
def get_settings() -> Settings:
    return Settings()
