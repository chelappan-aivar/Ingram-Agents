"""Opportunity checklists: the general one plus specialised ones layered on top.

A ``GuidanceSet`` is one complete set. The built-in set is read from
``config/guidance/*.json``; others come from config profiles saved in the UI
(``app/config_profiles.py``). A call uses the set it was started with: its meta
holds a snapshot, so later edits to a profile never change a running or past call.
Calls without a snapshot (built-in profile, or started before profiles existed)
use the built-in set.

The module-level functions read the built-in set, for callers that don't have a call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

GUIDANCE_DIR = Path(__file__).resolve().parent.parent / "config" / "guidance"
BUILTIN_PROFILE_ID = "ingram-default"
BUILTIN_PROFILE_NAME = "Ingram default"

# Field and opportunity type ids: what the agent's tools and session state use.
ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"

# Applies to every session; specialised checklists are layered on top.
GENERAL_OPPORTUNITY_TYPE = "general"

MEMORY_SCOPE_ACCOUNT = "account"  # A fact about the company; shared by all its opportunities.
MEMORY_SCOPE_OPPORTUNITY = "opportunity"  # A fact about one deal.
MEMORY_SCOPES = (MEMORY_SCOPE_ACCOUNT, MEMORY_SCOPE_OPPORTUNITY)


class UnknownOpportunityTypeError(ValueError):
    """Raised when no checklist exists for an opportunity type."""


@dataclass(frozen=True)
class RequiredField:
    id: str
    label: str
    description: str
    required: bool
    category: str = ""  # Business, Technical, Delivery constraint, Commercial
    ask_when: str = ""  # Hint for when the topic fits in the conversation.
    examples: tuple[str, ...] = ()  # Example questions, shown to the model as style hints.
    memory_scope: str = MEMORY_SCOPE_OPPORTUNITY  # Long-term memory level of a captured value.


@dataclass(frozen=True)
class Guidance:
    opportunity_type: str
    label: str
    description: str
    required_fields: tuple[RequiredField, ...]

    def required_field_ids(self) -> list[str]:
        return [f.id for f in self.required_fields if f.required]

    def all_field_ids(self) -> list[str]:
        return [f.id for f in self.required_fields]

    def get_field(self, field_id: str) -> RequiredField | None:
        return next((f for f in self.required_fields if f.id == field_id), None)


@dataclass(frozen=True)
class GuidanceSet:
    profile_id: str
    version: int  # 0 for the built-in set.
    general: Guidance
    specialised: dict[str, Guidance] = field(default_factory=dict)

    def list_opportunity_types(self) -> list[str]:
        return sorted(self.specialised)

    def load(self, opportunity_type: str) -> Guidance:
        """Raises UnknownOpportunityTypeError if this set has no such checklist."""
        if opportunity_type == GENERAL_OPPORTUNITY_TYPE:
            return self.general
        try:
            return self.specialised[opportunity_type]
        except KeyError:
            raise UnknownOpportunityTypeError(
                f"No checklist for opportunity_type={opportunity_type!r}. "
                f"Known types: {self.list_opportunity_types()}"
            ) from None

    def askable_field_ids(self) -> set[str]:
        """Field ids the agent may suggest questions about (the general checklist)."""
        return set(self.general.all_field_ids())

    def categories(self) -> dict[str, str]:
        """Field id -> category. The general checklist's wins over a specialised refinement."""
        out: dict[str, str] = {}
        for g in [*self.specialised.values(), self.general]:
            out.update({f.id: f.category for f in g.required_fields if f.category})
        return out

    def memory_scopes(self, opportunity_type: str | None = None) -> dict[str, str]:
        """Field id -> memory scope for the general and the call's specialised checklist.

        A field both define is account-level if either says so: a specialised field that
        refines a general one (same id) keeps the general field's account scope.
        """
        fields = list(self.general.required_fields)
        if opportunity_type in self.specialised:
            fields += self.specialised[opportunity_type].required_fields
        scopes: dict[str, str] = {}
        for f in fields:
            if scopes.get(f.id) != MEMORY_SCOPE_ACCOUNT:
                scopes[f.id] = f.memory_scope
        return scopes

    # --- snapshots stored in call meta -----------------------------------------------

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "general": checklist_to_dict(self.general),
            "opportunity_types": [checklist_to_dict(g) for g in self.specialised.values()],
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> GuidanceSet:
        general = checklist_from_dict(GENERAL_OPPORTUNITY_TYPE, data["general"])
        specialised = {t["id"]: checklist_from_dict(t["id"], t) for t in data.get("opportunity_types", [])}
        return cls(data["profile_id"], int(data.get("version") or 0), general, specialised)


@lru_cache(maxsize=1)
def builtin_guidance() -> GuidanceSet:
    """The built-in set from config/guidance. Read once per process."""
    general = _load_file(GUIDANCE_DIR / f"{GENERAL_OPPORTUNITY_TYPE}.json")
    specialised = {}
    if GUIDANCE_DIR.is_dir():
        for path in sorted(GUIDANCE_DIR.glob("*.json")):
            if path.stem != GENERAL_OPPORTUNITY_TYPE:
                g = _load_file(path)
                specialised[g.opportunity_type] = g
    return GuidanceSet(BUILTIN_PROFILE_ID, 0, general, specialised)


def guidance_for_meta(meta: dict[str, Any] | None) -> GuidanceSet:
    """The call's checklists: its snapshot, else the built-in set."""
    snapshot = (meta or {}).get("guidance")
    return GuidanceSet.from_snapshot(snapshot) if snapshot else builtin_guidance()


# --- built-in set, for callers without a call ------------------------------------------

def list_opportunity_types() -> list[str]:
    """Return the specialised opportunity types, excluding the general checklist."""
    return builtin_guidance().list_opportunity_types()


def load_general_guidance() -> Guidance:
    return builtin_guidance().general


def askable_field_ids() -> set[str]:
    return builtin_guidance().askable_field_ids()


def load_guidance(opportunity_type: str) -> Guidance:
    """Raises UnknownOpportunityTypeError if no built-in checklist exists for the type."""
    return builtin_guidance().load(opportunity_type)


# --- parsing ------------------------------------------------------------------------

def _load_file(path: Path) -> Guidance:
    if not path.is_file():
        raise UnknownOpportunityTypeError(f"No guidance config found at {path.name}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return checklist_from_dict(data["opportunity_type"], {**data, "fields": data.get("required_fields", [])})


def checklist_from_dict(opportunity_type: str, data: dict[str, Any]) -> Guidance:
    fields = tuple(
        RequiredField(
            id=item["id"],
            label=item["label"],
            description=item.get("description", ""),
            required=bool(item.get("required", False)),
            category=item.get("category") or "",
            ask_when=item.get("ask_when") or "",
            examples=tuple(item.get("examples") or ()),
            memory_scope=item.get("memory_scope") or MEMORY_SCOPE_OPPORTUNITY,
        )
        for item in data.get("fields", [])
    )
    return Guidance(
        opportunity_type=opportunity_type,
        label=data.get("label") or opportunity_type,
        description=data.get("description", ""),
        required_fields=fields,
    )


def checklist_to_dict(g: Guidance) -> dict[str, Any]:
    return {
        "id": g.opportunity_type,
        "label": g.label,
        "description": g.description,
        "fields": [
            {
                "id": f.id, "label": f.label, "description": f.description, "required": f.required,
                "category": f.category, "ask_when": f.ask_when, "examples": list(f.examples),
                "memory_scope": f.memory_scope,
            }
            for f in g.required_fields
        ],
    }
