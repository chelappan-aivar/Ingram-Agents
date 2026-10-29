"""Template catalogue entries and the parts of a template the agent uses."""

from dataclasses import dataclass
from typing import NamedTuple


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    id: str
    category: str
    filename: str
    label: str


class LoadedTemplate(NamedTuple):
    category: str
    sample: str
    requirements: str
