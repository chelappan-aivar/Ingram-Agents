"""Data contracts for the ADR Generator (SRS -> low-level ADR + spec.json).

`Design` is the validated form of the model's `submit_design` tool call. Both renderers (adr.md, spec.json) read
only this object, so the two outputs always agree. Every field has a lenient default: a sparse or partly malformed
tool call still produces a Design, and the renderers print "Not specified" instead of failing.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"


def _str(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if not isinstance(v, list):
        return []
    return [_str(x) for x in v if x is not None and _str(x).strip()]


class _Model(BaseModel):
    """Ignores unknown keys. Coerces None/numbers to str for every `str` field and None/str to list for every
    `list[str]` field, so the model's small slips never fail validation."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, v: Any, info):  # type: ignore[no-untyped-def]
        ann = cls.model_fields[info.field_name].annotation
        if ann is str:
            return _str(v)
        if ann == list[str]:
            return _str_list(v)
        return v


class Requirement(_Model):
    id: str = ""                       # REQ-01 ...
    text: str = ""
    kind: str = "functional"           # functional | non_functional | constraint


class RejectedAlternative(_Model):
    option: str = ""
    reason: str = ""


class Decision(_Model):
    id: str = ""                       # ADR-001 ...
    title: str = ""
    status: str = "Proposed"
    context: str = ""
    decision: str = ""
    consequences: list[str] = Field(default_factory=list)
    rejected_alternatives: list[RejectedAlternative] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    component_ids: list[str] = Field(default_factory=list)

    @field_validator("rejected_alternatives", mode="before")
    @classmethod
    def _alts(cls, v: Any) -> list:
        if not isinstance(v, list):
            return []
        return [x if isinstance(x, dict) else {"option": _str(x)} for x in v if x]


class Boundary(_Model):
    id: str = ""                       # snake_case, e.g. main_vpc
    type: str = "generic"              # region | vpc | az | subnet_public | subnet_private | on_prem | external | generic
    name: str = ""
    parent: Optional[str] = None


class Component(_Model):
    id: str = ""                       # C-01 ...
    name: str = ""
    service: str = ""                  # canonical AWS service name from the service sheet, or the external system
    category: str = ""                 # compute | database | storage | networking | integration | ai_ml | security | ...
    role: str = ""                     # one short phrase: what it does in this solution
    configuration: dict[str, str] = Field(default_factory=dict)   # instance_type, engine, storage_gb, ...
    sku_summary: str = ""              # one line, e.g. "db.t4g.medium, PostgreSQL 16, gp3 100 GiB, Single-AZ"
    quantity: int = 1
    boundary: Optional[str] = None     # boundary id (innermost), or null for Region-level / external
    purpose: str = ""
    assumed: bool = False
    assumption_reason: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)

    @field_validator("configuration", mode="before")
    @classmethod
    def _config(cls, v: Any) -> dict[str, str]:
        if not v:
            return {}
        if isinstance(v, list):  # [{"key": k, "value": v}] or [[k, v]]
            out: dict[str, str] = {}
            for item in v:
                if isinstance(item, dict) and "key" in item:
                    out[_str(item["key"])] = _str(item.get("value"))
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    out[_str(item[0])] = _str(item[1])
            return out
        if isinstance(v, dict):
            return {_str(k): _str(val) for k, val in v.items() if val is not None and _str(val).strip()}
        return {}

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> int:
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1

    @field_validator("assumed", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1")
        return bool(v)


class Relationship(_Model):
    id: str = ""                       # F-01 ...
    source: str = ""                   # component id
    target: str = ""                   # component id
    label: str = ""                    # <= 4 words, verb phrase
    sentence: str = ""                 # one plain sentence naming both components by name
    protocol: str = ""
    port: str = ""


class Assumption(_Model):
    id: str = ""                       # A-01 ...
    text: str = ""
    reason: str = ""


class Design(_Model):
    title: str = ""
    customer: str = ""
    context: str = ""
    region: str = ""
    # Every AZ the workload spans, in order. A single-element list is a single-AZ design.
    # See adr_generator/availability.py; legacy `availability_zone` input is folded in below.
    availability_zones: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    boundaries: list[Boundary] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    security_operations: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("requirements", "decisions", "boundaries", "components", "relationships", "assumptions",
                     mode="before")
    @classmethod
    def _objects(cls, v: Any) -> list:
        # drop non-object items instead of failing the whole design
        if not isinstance(v, list):
            return []
        return [x for x in v if isinstance(x, (dict, BaseModel))]

    @model_validator(mode="before")
    @classmethod
    def _legacy_single_az(cls, data: Any) -> Any:
        """Accept the pre-multi-AZ scalar `availability_zone` as a one-element list.

        Designs saved before the availability model was configurable carry a single AZ string,
        and so do hand-written fixtures. Folding it in here keeps them loadable.
        """
        if not isinstance(data, dict):
            return data
        if not data.get("availability_zones") and data.get("availability_zone"):
            data = {**data, "availability_zones": [data["availability_zone"]]}
        return data

    @property
    def availability_zone(self) -> str:
        """The first AZ, for callers and reports that name just one. '' when none is set."""
        return self.availability_zones[0] if self.availability_zones else ""

    def component(self, cid: str) -> Optional[Component]:
        return next((c for c in self.components if c.id == cid), None)

    def boundary(self, bid: Optional[str]) -> Optional[Boundary]:
        return next((b for b in self.boundaries if b.id == bid), None) if bid else None


class RunMeta(_Model):
    """metadata.json of one run."""
    run_id: str = ""
    source_srs: str = ""
    model_id: str = ""
    fallback_used: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reason: Optional[str] = None
    counts: dict[str, int] = Field(default_factory=dict)
    status: str = "completed"          # completed | failed
    error: Optional[str] = None
