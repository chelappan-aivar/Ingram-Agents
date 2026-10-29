"""UpstreamInputs v1: the versioned Stage 1-3 handoff consumed by the SOW generator."""

from typing import Final, Literal, Self

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION: Final = "1.0"


class Contact(BaseModel):
    name: str
    role: str = ""
    email: str | None = None


class Meta(BaseModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    opportunity_id: str
    template_id: str = Field(..., pattern=r"^[^/]+/[^/]+$")
    source_agents: list[str] = Field(default_factory=list)
    created_at: str | None = None
    generated_by: str | None = None


class Intake(BaseModel):
    customer_name: str = Field(..., min_length=1)
    industry: str = ""
    contacts: list[Contact] = Field(default_factory=list)
    engagement_type: str = ""
    template_hint: str | None = None
    problem_statement: str = ""
    goals: list[str] = Field(default_factory=list)
    timeline: str | None = None
    budget_range: str | None = None
    constraints: list[str] = Field(default_factory=list)


class Stakeholder(BaseModel):
    name: str
    role: str = ""


class TranscriptRef(BaseModel):
    title: str = ""
    text: str


class DiscoveryNotes(BaseModel):
    source: Literal["discovery", "live_assist"]
    findings: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    transcript_refs: list[TranscriptRef] = Field(default_factory=list)
    notes: str | None = None


class ArchImage(BaseModel):
    filename: str
    content_b64: str


class Service(BaseModel):
    name: str
    purpose: str = ""


class Architecture(BaseModel):
    diagram_images: list[ArchImage] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    nfrs: list[str] = Field(default_factory=list)


class CostLineItem(BaseModel):
    service: str
    monthly: float | None = None
    annual: float | None = None
    notes: str = ""


class FileRef(BaseModel):
    name: str
    content_b64: str


class Cost(BaseModel):
    currency: str = "USD"
    monthly_total: float | None = None
    annual_total: float | None = None
    region: str | None = None
    line_items: list[CostLineItem] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    # The cost estimate agent's calculator_url. The SOW Pricing section reproduces this
    # link; monthly_total/annual_total are used for reconciliation checks only and are
    # never rendered into the SOW (see pricing-refers-to-cost-estimate).
    source_link: str | None = None
    source_file: FileRef | None = None


class UpstreamInputs(BaseModel):
    """Versioned Stage 1-3 handoff consumed by the SOW generator."""

    meta: Meta
    intake: Intake
    discovery: DiscoveryNotes | None = None
    live_assist: DiscoveryNotes | None = None
    architecture: Architecture | None = None
    cost: Cost | None = None

    @model_validator(mode="after")
    def _validate_branch_sources(self) -> Self:
        """Enforce that discovery/live_assist carry the matching source tag."""
        if self.discovery and self.discovery.source != "discovery":
            raise ValueError("discovery block must have source 'discovery'")
        if self.live_assist and self.live_assist.source != "live_assist":
            raise ValueError("live_assist block must have source 'live_assist'")
        return self
