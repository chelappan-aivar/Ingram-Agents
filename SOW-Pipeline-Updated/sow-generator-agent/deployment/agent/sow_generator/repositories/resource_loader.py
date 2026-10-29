"""The one place packaged resources (prompts, policies, templates, assets) are located."""

from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Final

RESOURCES_PACKAGE: Final = "sow_generator.resources"


def resource(*parts: str) -> Traversable:
    """A packaged resource, resolved from the package rather than from `__file__` hops."""
    return files(RESOURCES_PACKAGE).joinpath(*parts)


def read_resource_text(*parts: str) -> str:
    return resource(*parts).read_text(encoding="utf-8")


def read_resource_bytes(*parts: str) -> bytes:
    return resource(*parts).read_bytes()
