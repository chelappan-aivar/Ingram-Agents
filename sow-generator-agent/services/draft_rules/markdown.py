"""A deliberately small markdown model: headings and the text between them."""

import re
from dataclasses import dataclass, field


@dataclass
class Section:
    """A heading plus the raw text that follows it, up to the next heading."""

    level: int
    heading: str
    body: str = ""


@dataclass
class ParsedDraft:
    headings: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    # All numbers found in the draft, with the source line, for numeric rules.
    text: str = ""

    def section_body_for(self, names: list[str]) -> str | None:
        """Return the body of the first section whose heading matches any name."""
        for name in names:
            needle = name.lower()
            for sec in self.sections:
                if needle in sec.heading.lower():
                    return sec.body
        return None


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def parse_markdown(md: str) -> ParsedDraft:
    """Parse SOW markdown into headings and section bodies.

    Deliberately small: headings and the text between them. Tables and bullets
    are left as raw text in the section body — the numeric/coverage rules scan
    that text, preferring labelled rows and table cells over free prose.
    """
    draft = ParsedDraft(text=md)
    current: Section | None = None
    for raw_line in md.splitlines():
        m = _HEADING_RE.match(raw_line.strip())
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
            draft.headings.append(heading)
            current = Section(level=level, heading=heading)
            draft.sections.append(current)
        elif current is not None:
            current.body += raw_line + "\n"
    return draft
