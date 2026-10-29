"""Template catalogue contract."""

from pydantic import BaseModel


class TemplateOption(BaseModel):
    id: str
    category: str
    filename: str
    label: str


class TemplateList(BaseModel):
    templates: list[TemplateOption]
