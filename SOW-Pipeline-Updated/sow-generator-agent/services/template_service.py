"""Template catalogue use cases."""

from dataclasses import asdict

from app.modules.sow_generator.repositories import template_repository
from app.modules.sow_generator.schemas import TemplateOption


def list_template_options() -> list[TemplateOption]:
    return [TemplateOption(**asdict(info)) for info in template_repository.list_templates()]


def get_template_markdown(category: str, stem: str) -> str:
    """The raw template; raises TemplateError when the id is unknown or malformed."""
    return template_repository.load_template_raw(f"{category}/{stem}")
