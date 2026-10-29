"""Shared data contracts for diagram_generator (see docs/diagram_generator/ADR.md, "Data contracts").

Every stage reads and writes these models. Engineers must not change field names or meanings
without the orchestrator; add new optional fields only.

Flow:  SourceDoc[] -> Extraction (LLM 1 + Harness 1) -> AssumerOutput (LLM 2 + Harness 2)
       -> FinalSpec (merged, icons resolved) -> draw.io XML (LLM 3 + Harness 3) -> exports
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ----------------------------------------------------------------------------- enums
Strength = Literal["stated", "inferred"]
"""stated = the document says it explicitly; inferred = the document implies it."""

Origin = Literal["stated", "inferred", "assumed"]
"""Where a FinalSpec item came from: the Extractor (stated/inferred) or the Assumer (assumed)."""

ComponentKind = Literal["aws_service", "external", "actor", "data_store", "generic"]
BoundaryType = Literal[
    "aws_cloud", "region", "vpc", "az", "subnet_public", "subnet_private", "security_group",
    "account", "on_prem", "external", "generic",
]
Direction = Literal["one_way", "bidirectional"]


# ----------------------------------------------------------------------------- inputs
class SourceDoc(BaseModel):
    """One uploaded document after text extraction."""
    file: str                      # original file name, used in Evidence.file
    text: str                      # extracted text sent to the models (may be truncated)
    chars_original: int
    truncated: bool = False


# ----------------------------------------------------------------------------- evidence
class Evidence(BaseModel):
    file: str = Field(description="Source file name exactly as given in the input")
    quote: str = Field(description="Exact quote from that file (verbatim, short)")
    reasoning: str = Field(description="One short sentence: why this quote supports the item")


# ----------------------------------------------------------------------------- base items
class ComponentBase(BaseModel):
    id: str = Field(description="Unique snake_case id, e.g. 'orders_api'")
    name: str = Field(description="Short display name, e.g. 'Orders API'")
    service: Optional[str] = Field(default=None, description="Canonical service name from the catalog, or null")
    kind: ComponentKind = "aws_service"
    boundary: Optional[str] = Field(default=None, description="Id of the innermost boundary it sits in, or null")


class BoundaryBase(BaseModel):
    id: str
    type: BoundaryType
    name: str
    parent: Optional[str] = None


class RelationshipBase(BaseModel):
    id: str = Field(description="Unique id, e.g. 'r1'")
    source: str = Field(description="Component id the flow starts at")
    target: str = Field(description="Component id the flow ends at")
    label: str = Field(default="", description="Verb phrase of at most 4 words")
    direction: Direction = "one_way"


# ----------------------------------------------------------------------------- LLM 1: Extractor
class Component(ComponentBase):
    strength: Strength
    evidence: list[Evidence] = Field(default_factory=list)


class Boundary(BoundaryBase):
    strength: Strength = "stated"
    evidence: list[Evidence] = Field(default_factory=list)


class Relationship(RelationshipBase):
    strength: Strength
    evidence: list[Evidence] = Field(default_factory=list)


class Claim(BaseModel):
    statement: str = Field(description="What this side claims, one sentence")
    strength: Strength
    evidence: Evidence


class Contradiction(BaseModel):
    """Two claims of similar strength (stated vs stated, or inferred vs inferred) that clash.
    Every item id in removed_ids is removed from the component/relationship/boundary lists."""
    subject: str
    claim_a: Claim
    claim_b: Claim
    removed_ids: list[str] = Field(default_factory=list)
    note: str = ""


class Resolution(BaseModel):
    """A stated claim beat an inferred one; logged, not a contradiction."""
    subject: str
    kept: Claim
    dropped: Claim
    kept_id: Optional[str] = None


class Extraction(BaseModel):
    title: str
    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    boundaries: list[Boundary] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    resolutions: list[Resolution] = Field(default_factory=list)


# ----------------------------------------------------------------------------- LLM 2: Assumer
class AssumptionFields(BaseModel):
    supports_ids: list[str] = Field(description="Existing ids that would be meaningless without this item")
    reasoning: str = Field(description="One or two sentences: why it must exist and fits the business context")
    evidence: list[Evidence] = Field(default_factory=list, description="Quotes that signal it (at least one)")


class AssumedComponent(ComponentBase, AssumptionFields):
    pass


class AssumedBoundary(BoundaryBase, AssumptionFields):
    pass


class AssumedRelationship(RelationshipBase, AssumptionFields):
    pass


class Unresolved(BaseModel):
    about: str = Field(description="The missing component/relationship, in a few words")
    reasoning: str = Field(description="Why it is critical and why no assumption was possible")
    suggestion: str = Field(description="One short sentence: what the SA should confirm or add")
    related_ids: list[str] = Field(default_factory=list)


class AssumerOutput(BaseModel):
    """The Assumer may only ADD items; it never edits or removes extracted ones."""
    components: list[AssumedComponent] = Field(default_factory=list)
    relationships: list[AssumedRelationship] = Field(default_factory=list)
    boundaries: list[AssumedBoundary] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)


# ----------------------------------------------------------------------------- final spec (to LLM 3)
class IconRef(BaseModel):
    """How to draw a component in draw.io (resolved by the catalog, never by an LLM)."""
    shape: str                     # AWS4 shape name, e.g. "lambda" (goes after mxgraph.aws4.)
    style: Literal["resource_icon", "sub_resource", "general"] = "resource_icon"
    category: str                  # e.g. "compute"
    fill_color: str                # icon fill, e.g. "#ED7100"
    tint_color: str = "#F5F5F5"     # category container tint
    stroke_color: str = "#666666"   # category container stroke
    category_label: str = ""        # container label, e.g. "Compute"
    matched: bool = True            # False when the catalog fell back to a generic icon


class SpecComponent(ComponentBase):
    origin: Origin
    icon: IconRef


class SpecBoundary(BoundaryBase):
    origin: Origin


class SpecRelationship(RelationshipBase):
    origin: Origin


class FinalSpec(BaseModel):
    title: str
    components: list[SpecComponent] = Field(default_factory=list)
    relationships: list[SpecRelationship] = Field(default_factory=list)
    boundaries: list[SpecBoundary] = Field(default_factory=list)


# ----------------------------------------------------------------------------- LLM 3: diagram layout
# The Diagram coder returns only box and group positions; code builds the draw.io XML from them with the AWS
# skill's styles (diagram_generator/render/drawio_builder.py) and routes every edge with libavoid
# (diagram_generator/render/edge_router.py). Coordinates are absolute pixels.
NODE_SIZE = 120  # every component is drawn as a 120x120 box (category container or actor container)


class LayoutRect(BaseModel):
    id: str = Field(description="Boundary id from the spec")
    x: int
    y: int
    w: int
    h: int


class LayoutNode(BaseModel):
    id: str = Field(description="Component id from the spec")
    x: int = Field(description="Left edge of the 120x120 box")
    y: int = Field(description="Top edge of the 120x120 box")


class AuxGroup(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Layout(BaseModel):
    subtitle: str = Field(default="", description="One short line saying what the architecture does")
    groups: list[LayoutRect] = Field(default_factory=list)
    nodes: list[LayoutNode] = Field(default_factory=list)
    aux_group: Optional[AuxGroup] = Field(default=None, description="'Auxiliary Services' rectangle, if used")


# ----------------------------------------------------------------------------- harness log
class HarnessRecord(BaseModel):
    """One line of harness.log.jsonl. Written after every LLM step (and for export)."""
    step: Literal["design_spec", "harness_1", "harness_2", "harness_3", "export"]
    attempt: int = 1
    ok: bool
    in_counts: dict[str, int] = Field(default_factory=dict)    # what went into the LLM step
    out_counts: dict[str, int] = Field(default_factory=dict)   # what came out (after the harness)
    added_ids: list[str] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)          # {"id": ..., "reason": ...}
    errors: list[str] = Field(default_factory=list)            # blocking problems
    warnings: list[str] = Field(default_factory=list)          # non-blocking problems
    details: dict = Field(default_factory=dict)                # step-specific extras
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
