"""Schema for guidance config profiles edited in the UI (pydantic; also served as JSON Schema).

A profile is a complete checklist set: the general checklist plus specialised
opportunity types layered on top. Text from a profile goes into the agent's
prompt, so every string is normalised to a single line without control
characters; that keeps a description from forging a new prompt section.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.guidance import (
    GENERAL_OPPORTUNITY_TYPE,
    ID_PATTERN,
    MEMORY_SCOPE_OPPORTUNITY,
    Guidance,
    GuidanceSet,
    checklist_from_dict,
    checklist_to_dict,
)

MAX_PROFILE_BYTES = 60_000  # Fits in one API request body (64 KB).
MAX_FIELDS = 50
MAX_TYPES = 30


def _one_line(value: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())


Line = Annotated[str, AfterValidator(_one_line)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldSpec(_Strict):
    id: str = Field(pattern=ID_PATTERN, description="Stable id; captured values are stored under it")
    label: Line = Field(min_length=1, max_length=120)
    description: Line = Field(default="", max_length=1000)
    required: bool = False
    category: Line = Field(default="", max_length=50, description="Grouping shown to the agent and UI")
    ask_when: Line = Field(default="", max_length=500, description="When the topic fits in the conversation")
    examples: list[Annotated[Line, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=10)
    memory_scope: Literal["account", "opportunity"] = Field(
        default=MEMORY_SCOPE_OPPORTUNITY,
        description="account: a fact about the company, shared by its opportunities; opportunity: about one deal",
    )


def _unique_fields(fields: list[FieldSpec], where: str) -> list[FieldSpec]:
    seen = set()
    for f in fields:
        if f.id in seen:
            raise ValueError(f"field id '{f.id}' appears twice in {where}")
        seen.add(f.id)
    return fields


class GeneralChecklist(_Strict):
    label: Line = Field(default="General Discovery", min_length=1, max_length=100)
    description: Line = Field(default="", max_length=1000)
    fields: list[FieldSpec] = Field(min_length=1, max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def _unique(self) -> GeneralChecklist:
        _unique_fields(self.fields, "the general checklist")
        return self


class OpportunityTypeChecklist(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: Line = Field(min_length=1, max_length=100)
    description: Line = Field(default="", max_length=1000, description="Helps the agent recognise the type")
    fields: list[FieldSpec] = Field(default_factory=list, max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def _unique(self) -> OpportunityTypeChecklist:
        if self.id == GENERAL_OPPORTUNITY_TYPE:
            raise ValueError("'general' is reserved for the general checklist")
        _unique_fields(self.fields, f"opportunity type '{self.id}'")
        return self


class ProfileBody(_Strict):
    """What the UI edits. A specialised field may reuse a general field id to refine it."""

    name: Line = Field(min_length=1, max_length=100)
    description: Line = Field(default="", max_length=1000)
    general: GeneralChecklist
    opportunity_types: list[OpportunityTypeChecklist] = Field(default_factory=list, max_length=MAX_TYPES)

    @model_validator(mode="after")
    def _checks(self) -> ProfileBody:
        ids = [t.id for t in self.opportunity_types]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"opportunity type ids must be unique: {duplicates}")
        size = len(json.dumps(self.model_dump()).encode())
        if size > MAX_PROFILE_BYTES:
            raise ValueError(f"profile is {size} bytes; the limit is {MAX_PROFILE_BYTES}")
        return self

    def to_guidance(self, profile_id: str, version: int) -> GuidanceSet:
        general = checklist_from_dict(GENERAL_OPPORTUNITY_TYPE, self.general.model_dump())
        specialised = {t.id: checklist_from_dict(t.id, t.model_dump()) for t in self.opportunity_types}
        return GuidanceSet(profile_id, version, general, specialised)

    @classmethod
    def from_guidance(cls, guidance: GuidanceSet, name: str, description: str = "") -> ProfileBody:
        def checklist(g: Guidance) -> dict[str, Any]:
            data = checklist_to_dict(g)
            data.pop("id")
            return data

        return cls.model_validate({
            "name": name,
            "description": description,
            "general": checklist(guidance.general),
            "opportunity_types": [{"id": t, **checklist(g)} for t, g in guidance.specialised.items()],
        })


def profile_json_schema() -> dict[str, Any]:
    return ProfileBody.model_json_schema()


def validation_details(error: ValidationError) -> list[dict[str, str]]:
    """Field-level errors for the API: ``[{"path": "general.fields.2.id", "message": "..."}]``."""
    return [
        {"path": ".".join(str(p) for p in e["loc"]) or "(profile)", "message": e["msg"]}
        for e in error.errors()[:50]
    ]
