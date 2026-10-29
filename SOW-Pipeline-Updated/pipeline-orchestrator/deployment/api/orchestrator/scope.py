"""Pull the agreed in-scope / out-of-scope split out of the finished SOW.

This is the handoff that makes architecture a second stage rather than a first one. The
architecture agent accepts a structured `scope` (see architecture-agent adr_generator/scope.py);
this module is what turns a human-written SOW into it.

It is deliberately textual and forgiving. A SOW is prose written by a solutions architect, so
the headings vary ("Out of Scope", "Out-of-Scope", "Exclusions", "Not Included") and the items
may be bullets, numbered, or a table. Anything we cannot read confidently is left out, and an
empty result is reported honestly rather than guessed at -- a wrong out-of-scope list is worse
than none, because it would silently delete components from the design.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MAX_ITEMS = 60
MAX_ITEM_CHARS = 400
MIN_ITEM_CHARS = 3

# Singular needles ("exclusion", "deliverable") also match "Exclusion(s)" and "Deliverable(s)", the form the
# EMEA USC CVA template uses. "cloud viability assessment" is the CVA template's heading for its phase list.
IN_SCOPE_HEADINGS = ("in scope", "in-scope", "scope of work", "scope of services", "engagement scope", "included",
                     "deliverable", "cloud viability assessment")
OUT_SCOPE_HEADINGS = ("out of scope", "out-of-scope", "exclusion", "excluded", "not included",
                      "not in scope")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+\u2022]|\d+[.)])\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# A phased scope written as plain lines: "Phase 4: AWS Rightsizing and Instance Blueprinting. Using real ...".
_PHASE_RE = re.compile(r"^\s*((?:Phase|Stage|Step|Workstream|Milestone)\s+\d+\s*[:.-][^.]*)", re.IGNORECASE)
# Inline form: "Out of scope: data migration, legacy decommissioning."
_INLINE_RE = re.compile(
    r"(out[- ]of[- ]scope|out of scope|exclusions?|not included)\s*[:\u2013-]\s*(.+)",
    re.IGNORECASE,
)


@dataclass
class Section:
    level: int
    heading: str
    body: str = ""


@dataclass
class ParsedSow:
    sections: list[Section] = field(default_factory=list)
    text: str = ""


def parse_sections(markdown: str) -> ParsedSow:
    """Headings and the text under each, including nested subsection text.

    Unlike a strict parser, a section's body carries everything up to the next heading *of the
    same or a higher level*, so "## In Scope" nested under "# Scope of Work" is still inside the
    parent's body. Without that, a parent section with subsections reads as empty.
    """
    parsed = ParsedSow(text=markdown or "")
    raw_sections: list[tuple[int, str, int]] = []       # (level, heading, start line index)
    lines = (markdown or "").splitlines()
    for i, line in enumerate(lines):
        match = _HEADING_RE.match(line.strip())
        if match:
            raw_sections.append((len(match.group(1)), match.group(2).strip(), i))

    for idx, (level, heading, start) in enumerate(raw_sections):
        end = len(lines)
        for next_level, _, next_start in raw_sections[idx + 1 :]:
            if next_level <= level:
                end = next_start
                break
        parsed.sections.append(Section(level, heading, "\n".join(lines[start + 1 : end])))
    return parsed


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|[*_`]", "", text)              # strip markdown emphasis
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # links -> their label
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" .;:")


def _items_from_body(body: str) -> list[str]:
    """Bullets, numbered items, or first-column table cells. Falls back to sentences."""
    items: list[str] = []

    for line in body.splitlines():
        bullet = _BULLET_RE.match(line)
        phase = None if bullet else _PHASE_RE.match(line)
        if bullet or phase:
            text = _clean(bullet.group(1) if bullet else phase.group(1))
            if len(text) >= MIN_ITEM_CHARS:
                items.append(text[:MAX_ITEM_CHARS])
    if items:
        return items[:MAX_ITEMS]

    # A table: take the first column, skipping the header and separator rows.
    rows = [m.group(1) for line in body.splitlines() if (m := _TABLE_ROW_RE.match(line))
            and not _TABLE_SEP_RE.match(line)]
    if len(rows) >= 2:
        for row in rows[1:]:
            cell = _clean(row.split("|")[0])
            if len(cell) >= MIN_ITEM_CHARS:
                items.append(cell[:MAX_ITEM_CHARS])
    if items:
        return items[:MAX_ITEMS]

    # Plain prose: one item per sentence, so at least something structured comes through.
    for sentence in re.split(r"(?<=[.;])\s+", body):
        text = _clean(sentence)
        if len(text) >= MIN_ITEM_CHARS:
            items.append(text[:MAX_ITEM_CHARS])
    return items[:MAX_ITEMS]


def _section_items(parsed: ParsedSow, needles: tuple[str, ...],
                   exclude: tuple[str, ...] = ()) -> list[str]:
    """Items from the deepest heading that matches, so '## In Scope' beats '# Scope of Work'.

    `exclude` keeps the in-scope lookup from matching an "Out of Scope" heading, since
    "out of scope" contains "scope of work"-like substrings in some documents.
    """
    matches = [
        section for section in parsed.sections
        if any(n in section.heading.lower() for n in needles)
        and not any(x in section.heading.lower() for x in exclude)
    ]
    if not matches:
        return []
    deepest = max(matches, key=lambda s: s.level)
    return _items_from_body(deepest.body)


def _inline_out_of_scope(text: str) -> list[str]:
    """'Out of scope: a, b and c.' written as a sentence rather than a section."""
    items: list[str] = []
    for match in _INLINE_RE.finditer(text):
        tail = match.group(2).split(".")[0]
        for part in re.split(r",|;|\band\b", tail):
            cleaned = _clean(part)
            if len(cleaned) >= MIN_ITEM_CHARS:
                items.append(cleaned[:MAX_ITEM_CHARS])
    return items[:MAX_ITEMS]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def extract_scope(sow_markdown: str, source: str = "") -> dict[str, list[str] | str]:
    """The SOW's scope split, shaped for the architecture agent's `scope` payload.

    Returns `{"in_scope": [...], "out_of_scope": [...], "source": "..."}`. Either list may be
    empty; the caller decides what to do about that. The Well-Architected review already fails
    a SOW with no out-of-scope list, which is the intended place for that complaint.
    """
    parsed = parse_sections(sow_markdown or "")

    out_of_scope = _section_items(parsed, OUT_SCOPE_HEADINGS)
    if not out_of_scope:
        out_of_scope = _inline_out_of_scope(parsed.text)

    in_scope = _section_items(parsed, IN_SCOPE_HEADINGS, exclude=OUT_SCOPE_HEADINGS)
    # A parent "Scope of Work" body can swallow the nested "Out of Scope" bullets; drop any
    # in-scope item that is really an exclusion we already captured.
    excluded = {item.lower() for item in out_of_scope}
    in_scope = [item for item in in_scope if item.lower() not in excluded]

    return {
        "in_scope": _dedupe(in_scope),
        "out_of_scope": _dedupe(out_of_scope),
        "source": source,
    }


def scope_is_usable(scope: Optional[dict]) -> bool:
    """True when the scope carries enough to constrain a design.

    An out-of-scope list is the valuable half: in-scope repeats the SOW the design already sees,
    whereas exclusions are the only thing that stops the model adding unagreed services.
    """
    if not scope:
        return False
    return bool(scope.get("in_scope") or scope.get("out_of_scope"))
