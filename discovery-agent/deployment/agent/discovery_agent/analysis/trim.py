"""Remove small talk from large documents so a big corpus fits in one analysis pass.

A fast model only chooses WHICH turns to keep (it returns turn-number ranges). Code then cuts those turns out of the
original text, so every kept passage is a verbatim slice of the source and quotes can still be verified.
"""
import logging
import re
from typing import Callable

from ..data.documents import Document
from .transcript import speakers

log = logging.getLogger("discovery_agent")

TRIM_MIN_CHARS = 20_000  # smaller documents are never trimmed
WINDOW_CHARS = 300_000  # per model call (about 85k tokens)
MIN_KEEP_FRACTION = 0.05  # keeping less than this means the answer is not trusted: keep the whole document
_TS = re.compile(r"^\d+:\d{2}(:\d{2})?$")

Ask = Callable[[str, int, int], dict]  # (prompt, first_turn_number, last_turn_number) -> {"keep": [[a, b], ...]}


def split_turns(doc: Document) -> tuple[list[str], str]:
    """Return (turns, separator). Transcript turns are speaker blocks; other documents are paragraphs."""
    lines = doc.text.splitlines()
    if doc.kind == "transcript":
        starts = [i for i in range(len(lines) - 1)
                  if lines[i].strip() and not _TS.match(lines[i].strip()) and _TS.match(lines[i + 1].strip())]
        if starts:
            turns = ["\n".join(lines[:starts[0]])] if starts[0] > 0 else []
            turns += ["\n".join(lines[a:b]) for a, b in zip(starts, starts[1:] + [len(lines)])]
            return [t for t in turns if t.strip()], "\n"
    turns = []
    for para in (p for p in re.split(r"\n\s*\n", doc.text) if p.strip()):
        while len(para) > 2000:
            cut = para.rfind(" ", 0, 2000)
            cut = cut if cut > 0 else 2000
            turns.append(para[:cut])
            para = para[cut:].lstrip()
        turns.append(para)
    return turns, "\n\n"


def windows(turns: list[str]) -> list[tuple[int, int]]:
    """Consecutive (first, last) turn indices whose text fits one model call."""
    out, start, size = [], 0, 0
    for i, t in enumerate(turns):
        if size + len(t) > WINDOW_CHARS and i > start:
            out.append((start, i - 1))
            start, size = i, 0
        size += len(t)
    out.append((start, len(turns) - 1))
    return out


def range_problems(data: dict, lo: int, hi: int) -> list[str]:
    """Check a model answer; the problems are returned to the model for one retry."""
    keep = data.get("keep") if isinstance(data, dict) else None
    if not isinstance(keep, list):
        return ['answer must be {"keep": [[first, last], ...]}']
    problems = []
    for r in keep:
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r)):
            problems.append(f"{r!r} is not [first, last] with whole numbers")
        elif not (lo <= r[0] <= r[1] <= hi):
            problems.append(f"{r!r} must satisfy {lo} <= first <= last <= {hi}")
    return problems[:10]


def _prompt(turns: list[str], first: int, last: int) -> str:
    body = "\n".join(f"[T{i + 1}] {turns[i]}" for i in range(first, last + 1))
    return (f"Turns T{first + 1} to T{last + 1} follow. Return the ranges to KEEP.\n\n<turns>\n{body}\n</turns>")


def trim_document(doc: Document, ask: Ask) -> Document:
    """Return the document reduced to its relevant turns, or the unchanged document when trimming is not safe."""
    if len(doc.text) < TRIM_MIN_CHARS:
        return doc
    turns, sep = split_turns(doc)
    if len(turns) < 3:
        return doc
    keep: set[int] = set()
    try:
        for first, last in windows(turns):
            answer = ask(_prompt(turns, first, last), first + 1, last + 1)
            for a, b in answer["keep"]:
                keep.update(range(a - 1, b))
    except RuntimeError:  # the model could not give a valid answer: keep everything rather than lose content
        log.warning("trim failed for a document; keeping it whole")
        return doc
    text = sep.join(t for i, t in enumerate(turns) if i in keep)
    if len(text) < MIN_KEEP_FRACTION * len(doc.text):
        log.warning("trim kept under %d%% of a document; keeping it whole", int(MIN_KEEP_FRACTION * 100))
        return doc
    return Document(doc.id, doc.kind, text, speakers(text) if doc.kind == "transcript" else set(), doc.label,
                    kept_fraction=round(len(text) / len(doc.text), 2))
