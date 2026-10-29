"""Apply the SOW agent's edit proposals to SOW markdown, on the server.

The SOW agent's `edit` action does not return a new document. It returns *proposals* (edit a
block, insert after a block or section, delete a section), each addressed by the block ids and
headings it read through its tools. Its own web UI applies them in the browser with `draft.js`.
The pipeline has no browser in the loop, so this module is a port of that file's `parse`,
`sectionsMap`, `resolve` and `apply`. Keep the two in step: block ids must be numbered exactly as
`draft.js` numbers them, because the agent echoes those ids back.

    model = parse(markdown)
    sections_map, outline = sections_map_of(model)      # what the editor is shown
    new_markdown, applied, skipped = apply_proposals(markdown, proposals)

Blocks follow the .docx export: every non-empty line is one block, except tables (one block per
table) and ``` fences.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^(\s*(?:[-*+]|\d+\.)\s+)(.*)$")
IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
TABLE_PREFIX = "TABLE_JSON:"


@dataclass(eq=False)
class Block:
    kind: str                      # heading | code | table | image | bullet | para
    start: int
    end: int
    text: str
    id: str = ""
    prefix: str = ""
    rows: list[list[str]] = field(default_factory=list)
    tag: str = ""
    before_id: str = ""
    section: Optional["Section"] = field(default=None, repr=False)


@dataclass(eq=False)
class Section:
    heading: str
    level: int
    start: int
    end: int = 0
    blocks: list[Block] = field(default_factory=list, repr=False)
    tables: list[Block] = field(default_factory=list, repr=False)


@dataclass(eq=False)
class Model:
    lines: list[str]
    blocks: list[Block]
    sections: list[Section]
    by_id: dict[str, Block]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:\-|]+\|?\s*", line)) and "-" in line


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped[1:] if stripped.startswith("|") else stripped
    stripped = stripped[:-1] if stripped.endswith("|") else stripped
    return [c.strip() for c in stripped.split("|")]


def _norm(value: Any) -> str:
    text = re.sub(r"^#+\s*", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip().lower()


def _lines(markdown: str) -> list[str]:
    return str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def parse(markdown: str) -> Model:
    lines = _lines(markdown)
    blocks: list[Block] = []
    sections: list[Section] = []
    section: Optional[Section] = None
    counter = {"block": 0, "table": 0}

    def add(block: Block) -> Block:
        counter["block"] += 1
        block.id = f"b{counter['block']}"
        block.section = section
        blocks.append(block)
        if section is not None:
            section.blocks.append(block)
        return block

    i = 0
    while i < len(lines):
        line = lines[i]
        trimmed = line.strip()
        if not trimmed:
            i += 1
            continue
        heading = HEADING.match(trimmed)
        if heading:
            section = Section(heading=heading.group(2), level=len(heading.group(1)), start=i)
            sections.append(section)
            add(Block("heading", i, i, heading.group(2), prefix=f"{heading.group(1)} "))
            i += 1
            continue
        if trimmed.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            add(Block("code", i, min(j, len(lines) - 1), "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        if trimmed.startswith("|") and i + 1 < len(lines) and _is_rule(lines[i + 1]):
            j = i + 2
            rows = [_cells(line)]
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(_cells(lines[j]))
                j += 1
            before = next((b for b in reversed(section.blocks) if b.kind != "table"), None) if section else None
            counter["table"] += 1
            table = add(Block("table", i, j - 1, "\n".join("\t".join(r) for r in rows), rows=rows,
                              tag=f"t{counter['table']}", before_id=before.id if before else ""))
            if section is not None:
                section.tables.append(table)
            i = j
            continue
        if IMAGE.match(trimmed):
            add(Block("image", i, i, trimmed))
            i += 1
            continue
        bullet = BULLET.match(line)
        if bullet:
            add(Block("bullet", i, i, bullet.group(2).strip(), prefix=bullet.group(1)))
            i += 1
            continue
        add(Block("para", i, i, trimmed))
        i += 1

    for index, sec in enumerate(sections):         # a section ends where one of the same or higher level starts
        end = len(lines) - 1
        for later in sections[index + 1:]:
            if later.level <= sec.level:
                end = later.start - 1
                break
        sec.end = end
    return Model(lines, blocks, sections, {b.id: b for b in blocks})


def _para(block: Block) -> dict[str, str]:
    style = "listParagraph" if block.kind == "bullet" else "image" if block.kind == "image" else "normal"
    return {"block_id": block.id, "text": block.text, "style": style}


def sections_map_of(model: Model) -> tuple[dict[str, Any], list[str]]:
    """The editor contract (AgentEditRequest.sections_map): heading -> {paragraphs, tables}."""
    mapping: dict[str, Any] = {}
    outline: list[str] = []

    def key_for(heading: str) -> str:
        key, n = heading or "(untitled)", 2
        while key in mapping:
            key = f"{heading} ({n})"
            n += 1
        return key

    before = [b for b in model.blocks if b.section is None]
    if before:
        mapping["(before the first heading)"] = {
            "paragraphs": [_para(b) for b in before if b.kind != "table"], "tables": []}
    for sec in model.sections:
        key = key_for(sec.heading)
        outline.append(key)
        mapping[key] = {
            "paragraphs": [_para(b) for b in sec.blocks if b.kind not in ("table", "heading")],
            "tables": [{"tag": t.tag, "before_para_id": t.before_id, "data": t.rows} for t in sec.tables],
        }
    return mapping, outline


def _lines_text(model: Model, start: int, end: int) -> str:
    return "\n".join(model.lines[start:end + 1])


def _find_section(model: Model, heading: Any) -> Optional[Section]:
    want = _norm(heading)
    for sec in model.sections:
        if _norm(sec.heading) == want:
            return sec
    if len(want) > 3:
        for sec in model.sections:
            if want in _norm(sec.heading):
                return sec
    return None


def table_markdown(rows: list[list[Any]]) -> str:
    width = max([1] + [len(r) for r in rows])

    def fmt(row: list[Any]) -> str:
        cells = [str(row[c]) if c < len(row) and row[c] is not None else "" for c in range(width)]
        return "| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |"

    return "\n".join([fmt(rows[0] if rows else []), "| " + " | ".join(["---"] * width) + " |",
                      *[fmt(r) for r in rows[1:]]])


def _parse_table(replace: str) -> Optional[list[list[Any]]]:
    try:
        rows = json.loads(replace[replace.index(TABLE_PREFIX) + len(TABLE_PREFIX):])
    except ValueError:
        return None
    if isinstance(rows, list) and rows and all(isinstance(r, list) for r in rows):
        return rows
    return None


def _find_table(model: Model, proposal: dict[str, Any], rows: list[list[Any]]) -> Optional[Block]:
    tables = [b for b in model.blocks if b.kind == "table"]
    if proposal.get("table_tag"):
        found = next((t for t in tables if t.tag == proposal["table_tag"]), None)
        if found:
            return found
    if proposal.get("before_block_id"):
        found = next((t for t in tables if t.before_id == proposal["before_block_id"]), None)
        if found:
            return found
    by_id = model.by_id.get(str(proposal.get("block_id") or ""))
    if by_id and by_id.kind == "table":
        return by_id
    header = _norm("|".join(str(c) for c in rows[0])) if rows else ""
    return next((t for t in tables if _norm("|".join(t.rows[0])) == header), None)


LABELS = {"edit_proposal": "Edit", "insert_proposal": "New section",
          "insert_after_paragraph_proposal": "Insert", "delete_proposal": "Delete section"}


def resolve(model: Model, proposal: dict[str, Any]) -> dict[str, Any]:
    """One proposal -> {ok, op: replace | insert_after | delete, target, text, label, reason}."""
    kind = str(proposal.get("type") or "")
    label = LABELS.get(kind, kind)

    def fail(reason: str) -> dict[str, Any]:
        return {"ok": False, "label": label, "reason": reason}

    if kind == "edit_proposal":
        replace = str(proposal.get("replace") or "")
        if replace.startswith(TABLE_PREFIX):
            rows = _parse_table(replace)
            if not rows:
                return fail("The proposed table could not be read.")
            table = _find_table(model, proposal, rows)
            if not table:
                return fail("The table to change was not found in the draft.")
            return {"ok": True, "op": "replace", "label": "Edit table",
                    "target": _lines_text(model, table.start, table.end), "text": table_markdown(rows)}
        block = model.by_id.get(str(proposal.get("block_id") or ""))
        if not block:
            return fail("The paragraph to change was not found in the draft.")
        if block.kind == "table":
            return fail("A table edit must use the TABLE_JSON format.")
        text = replace.strip()
        target = _lines_text(model, block.start, block.end)
        if not text:
            return {"ok": True, "op": "delete", "label": "Remove text", "target": target, "text": ""}
        if block.kind == "bullet" and not BULLET.match(text):
            text = block.prefix + text
        if block.kind == "heading" and not HEADING.match(text):
            text = block.prefix + text
        return {"ok": True, "op": "replace", "label": label, "target": target, "text": text}
    if kind == "insert_after_paragraph_proposal":
        block = model.by_id.get(str(proposal.get("block_id") or ""))
        if not block:
            return fail("The paragraph to insert after was not found.")
        return {"ok": True, "op": "insert_after", "label": label,
                "target": _lines_text(model, block.start, block.end),
                "text": str(proposal.get("markdown") or "").strip()}
    if kind == "insert_proposal":
        sec = _find_section(model, proposal.get("after_heading"))
        if not sec:
            return fail(f'The section "{proposal.get("after_heading") or ""}" was not found.')
        return {"ok": True, "op": "insert_after", "label": label, "heading": sec.heading,
                "target": _lines_text(model, sec.start, sec.end).rstrip("\n"),
                "text": str(proposal.get("markdown") or "").strip()}
    if kind == "delete_proposal":
        sec = _find_section(model, proposal.get("heading"))
        if not sec:
            return fail(f'The section "{proposal.get("heading") or ""}" was not found.')
        return {"ok": True, "op": "delete", "label": label, "heading": sec.heading,
                "target": _lines_text(model, sec.start, sec.end).rstrip("\n"), "text": ""}
    return fail("This kind of change cannot be applied automatically.")


def apply_one(markdown: str, resolved: dict[str, Any]) -> Optional[str]:
    """Apply one resolved proposal to the current markdown, or None when its target is gone.

    Block targets are found by their exact text; section targets by their heading, so proposals
    still apply after an earlier one moved things.
    """
    if not resolved.get("ok") or not resolved.get("target"):
        return None
    md = "\n".join(_lines(markdown))
    target = resolved["target"]
    if resolved.get("heading"):
        model = parse(md)
        sec = _find_section(model, resolved["heading"])
        if not sec:
            return None
        target = _lines_text(model, sec.start, sec.end).rstrip("\n")
    at = md.find(target)
    if at < 0:
        return None
    before, after = md[:at], md[at + len(target):]
    op, text = resolved["op"], resolved.get("text", "")
    if op == "replace":
        return before + text + after
    if op == "delete":
        joined = re.sub(r"\n+\Z", "\n", before) + re.sub(r"\A\n+", "\n", after)
        return re.sub(r"\A\n", "", joined, count=1)
    if op == "insert_after":
        gap = "" if after.startswith("\n") else "\n"
        return f"{before}{target}\n\n{text}\n{gap}{after}"
    return None


def apply_proposals(markdown: str, proposals: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Apply every proposal that still fits. Returns (markdown, applied labels, skipped reasons).

    All proposals are resolved against the original document, which is what the editor saw, and
    applied one after another to the evolving text.
    """
    snapshot = parse(markdown)
    current = markdown
    applied: list[str] = []
    skipped: list[str] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        resolved = resolve(snapshot, proposal)
        if not resolved.get("ok"):
            skipped.append(f"{resolved['label']}: {resolved['reason']}")
            continue
        updated = apply_one(current, resolved)
        if updated is None:
            skipped.append(f"{resolved['label']}: the text it targets is no longer in the SOW.")
            continue
        current = updated
        where = proposal.get("heading") or proposal.get("after_heading") or resolved.get("heading") or ""
        applied.append(f"{resolved['label']}{f' ({where})' if where else ''}")
    return current, applied, skipped
