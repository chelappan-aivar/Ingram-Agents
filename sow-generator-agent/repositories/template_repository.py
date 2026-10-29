"""Template discovery and tagged-section parsing."""

import re
from importlib.resources.abc import Traversable
from typing import Final

from app.modules.sow_generator.errors import TemplateError
from app.modules.sow_generator.models import LoadedTemplate, TemplateInfo
from app.modules.sow_generator.repositories.resource_loader import resource

# Template categories. "cva" is the Ingram EMEA USC AWS Cloud Viability Assessment (Full) SOW.
CATEGORY_LABELS: Final = {"aiml": "AI/ML", "devops": "DevOps", "cva": "CVA"}

# Legacy upstream ids still resolve to the current template file.
_TEMPLATE_ID_ALIASES: Final = {"aiml/convogent": "aiml/ai-ml"}

_STEM_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def normalize_template_id(template_id: str) -> str:
    tid = (template_id or "").strip()
    return _TEMPLATE_ID_ALIASES.get(tid, tid)


def _markdown_files(folder: Traversable) -> list[Traversable]:
    return sorted(
        (entry for entry in folder.iterdir() if entry.is_file() and entry.name.endswith(".md")),
        key=lambda entry: entry.name,
    )


def _discover_categories() -> list[str]:
    """The recognised category folders that contain templates; other folders are ignored."""
    root = resource("templates")
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name in CATEGORY_LABELS and _markdown_files(entry)
    )


def _titleize_stem(stem: str) -> str:
    """Turn a file stem like 'devops-standard' into 'Devops Standard'."""
    parts = re.split(r"[-_]", stem)
    return " ".join(p[:1].upper() + p[1:] if p else p for p in parts)


def category_label(category: str) -> str:
    """Human-readable label for a template category (falls back to the key)."""
    return CATEGORY_LABELS.get(category, category)


def list_templates() -> list[TemplateInfo]:
    out: list[TemplateInfo] = []
    for category in _discover_categories():
        for md in _markdown_files(resource("templates", category)):
            stem = md.name.removesuffix(".md")
            label = (
                f"{CATEGORY_LABELS.get(category, category.title())} \u2014 {_titleize_stem(stem)}"
            )
            out.append(
                TemplateInfo(
                    id=f"{category}/{stem}", category=category, filename=md.name, label=label
                )
            )
    return out


def _resolve(template_id: str) -> tuple[str, Traversable]:
    template_id = normalize_template_id(template_id)
    if "/" not in template_id:
        raise TemplateError("template_id must be in the form '<category>/<stem>'")
    category, stem = template_id.split("/", 1)
    if category not in _discover_categories():
        raise TemplateError(f"Unknown category: {category}")
    if not _STEM_PATTERN.fullmatch(stem):
        raise TemplateError("Invalid template stem")
    path = resource("templates", category, f"{stem}.md")
    if not path.is_file():
        raise TemplateError(f"Template '{template_id}' not found")
    return category, path


def _extract_tag(content: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", content, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def load_template(template_id: str) -> LoadedTemplate:
    """The category, sample and requirements of the given template."""
    category, path = _resolve(template_id)
    raw = path.read_text(encoding="utf-8")
    return LoadedTemplate(category, _extract_tag(raw, "sample"), _extract_tag(raw, "requirements"))


def load_template_raw(template_id: str) -> str:
    return _resolve(template_id)[1].read_text(encoding="utf-8")
