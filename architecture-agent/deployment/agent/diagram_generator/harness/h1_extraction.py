"""Harness 1: checks the Extractor's output (code, no LLM).

Blocking errors (the stage repairs the extraction with the errors, then fails):
  schema errors; duplicate ids (components, boundaries and relationships share one id space, because
  they all become draw.io cell ids); unknown boundary/parent/source/target references; boundary
  parent cycles; extracted items with no evidence.
Contradiction rule, enforced in code: every id in a contradiction's removed_ids is dropped from the
  lists if the model left it there, and relationships touching a dropped component are dropped too.
  A dropped boundary re-parents its children / components to null (warning). Every drop is logged.
Warnings (never a drop): quote not found in its file, unknown evidence file, too many components,
  aws_service without `service`, label over 4 words, self-loop, empty extraction.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from diagram_generator.config import Settings
from diagram_generator.contracts import Evidence, Extraction, HarnessRecord, SourceDoc
from diagram_generator.harness.quotes import quote_found

MAX_LABEL_WORDS = 4


def _dups(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i in seen and i not in out:
            out.append(i)
        seen.add(i)
    return out


def _counts(x: Extraction) -> dict[str, int]:
    return {"components": len(x.components), "relationships": len(x.relationships),
            "boundaries": len(x.boundaries)}


def check_extraction(raw: Any, sources: list[SourceDoc], settings: Settings) -> tuple[Optional[Extraction], HarnessRecord]:
    in_counts = {"files": len(sources), "chars": sum(len(s.text) for s in sources)}
    rec = HarnessRecord(step="harness_1", ok=False, in_counts=in_counts)

    # ---- schema
    try:
        ext = Extraction.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            rec.errors.append(f"schema: {loc}: {err['msg']}")
        return None, rec
    except Exception as e:  # non-dict input etc.
        rec.errors.append(f"schema: {e}")
        return None, rec

    ext = ext.model_copy(deep=True)
    n_in = len(ext.components) + len(ext.relationships) + len(ext.boundaries)

    # ---- contradiction rule (before reference checks, so dropped ids cause no false errors)
    removed: set[str] = {i for c in ext.contradictions for i in c.removed_ids}
    for c in ext.contradictions:
        if not c.removed_ids:
            rec.warnings.append(f"contradiction '{c.subject}' lists no removed_ids; nothing was removed")

    def drop(item_id: str, reason: str) -> None:
        rec.dropped.append({"id": item_id, "reason": reason})

    dropped_comp: set[str] = set()
    keep_c = []
    for c in ext.components:
        if c.id in removed:
            dropped_comp.add(c.id)
            drop(c.id, "contradiction: id in removed_ids but left in components")
        else:
            keep_c.append(c)
    ext.components = keep_c

    keep_b = []
    for b in ext.boundaries:
        if b.id in removed:
            drop(b.id, "contradiction: id in removed_ids but left in boundaries")
        else:
            keep_b.append(b)
    ext.boundaries = keep_b

    keep_r = []
    for r in ext.relationships:
        if r.id in removed:
            drop(r.id, "contradiction: id in removed_ids but left in relationships")
        elif r.source in removed or r.target in removed or r.source in dropped_comp or r.target in dropped_comp:
            hit = r.source if (r.source in removed or r.source in dropped_comp) else r.target
            drop(r.id, f"touches contradicted component '{hit}'")
        else:
            keep_r.append(r)
    ext.relationships = keep_r

    # a removed boundary must not leave dangling references
    for b in ext.boundaries:
        if b.parent in removed:
            rec.warnings.append(f"boundary '{b.id}': parent '{b.parent}' was removed by a contradiction; parent set to null")
            b.parent = None
    for c in ext.components:
        if c.boundary in removed:
            rec.warnings.append(f"component '{c.id}': boundary '{c.boundary}' was removed by a contradiction; boundary set to null")
            c.boundary = None

    # ---- ids
    comp_ids = [c.id for c in ext.components]
    b_ids = [b.id for b in ext.boundaries]
    r_ids = [r.id for r in ext.relationships]
    for i in comp_ids + b_ids + r_ids:
        if not i.strip():
            rec.errors.append("empty id")
            break
    for d in _dups(comp_ids + b_ids + r_ids):
        rec.errors.append(f"duplicate id '{d}' (components, boundaries and relationships share one id space)")

    # ---- references
    comp_set, b_set = set(comp_ids), set(b_ids)
    for c in ext.components:
        if c.boundary is not None and c.boundary not in b_set:
            rec.errors.append(f"component '{c.id}' references unknown boundary '{c.boundary}'")
    parent_of: dict[str, Optional[str]] = {}
    for b in ext.boundaries:
        if b.parent is not None and b.parent not in b_set:
            rec.errors.append(f"boundary '{b.id}' references unknown parent '{b.parent}'")
        parent_of.setdefault(b.id, b.parent)
    for r in ext.relationships:
        if r.source not in comp_set:
            rec.errors.append(f"relationship '{r.id}' source '{r.source}' is not a known component id")
        if r.target not in comp_set:
            rec.errors.append(f"relationship '{r.id}' target '{r.target}' is not a known component id")
        if r.source == r.target and r.source in comp_set:
            rec.warnings.append(f"relationship '{r.id}' connects '{r.source}' to itself")

    # ---- parent cycles
    cyc_reported: set[str] = set()
    for start in parent_of:
        seen = {start}
        cur = parent_of.get(start)
        while cur is not None and cur in parent_of:
            if cur in seen:
                if cur not in cyc_reported:
                    cyc_reported.update(seen)
                    rec.errors.append(f"boundary parent cycle involving '{cur}'")
                break
            seen.add(cur)
            cur = parent_of.get(cur)

    # ---- evidence required
    for kind, items in (("component", ext.components), ("relationship", ext.relationships),
                        ("boundary", ext.boundaries)):
        for it in items:
            if not any(e.quote.strip() for e in it.evidence):
                rec.errors.append(f"{kind} '{it.id}' has no evidence (file + verbatim quote required)")

    # ---- quote verification (warnings)
    by_file = {s.file: s.text for s in sources}
    unverified: list[dict] = []

    def verify(owner: str, ev: Evidence) -> None:
        if not ev.quote.strip():
            return
        if ev.file not in by_file:
            unverified.append({"id": owner, "file": ev.file, "quote": ev.quote, "reason": "file not among the inputs"})
        elif not quote_found(ev.quote, by_file[ev.file]):
            unverified.append({"id": owner, "file": ev.file, "quote": ev.quote, "reason": "quote not found in file"})

    for items in (ext.components, ext.relationships, ext.boundaries):
        for it in items:
            for ev in it.evidence:
                verify(it.id, ev)
    for con in ext.contradictions:
        verify(f"contradiction:{con.subject}", con.claim_a.evidence)
        verify(f"contradiction:{con.subject}", con.claim_b.evidence)
    for res in ext.resolutions:
        verify(f"resolution:{res.subject}", res.kept.evidence)
        verify(f"resolution:{res.subject}", res.dropped.evidence)
    for u in unverified:
        rec.warnings.append(f"unverified quote for {u['id']} ({u['file']}): {u['reason']}")

    # ---- other warnings
    if not ext.components:
        rec.warnings.append("no components extracted")
    if len(ext.components) > settings.max_components_warn:
        rec.warnings.append(f"{len(ext.components)} components exceeds {settings.max_components_warn}; check the altitude (service level only)")
    for c in ext.components:
        if c.kind == "aws_service" and not c.service:
            rec.warnings.append(f"component '{c.id}' is an aws_service but has no catalog `service`")
    for r in ext.relationships:
        if len(r.label.split()) > MAX_LABEL_WORDS:
            rec.warnings.append(f"relationship '{r.id}' label has more than {MAX_LABEL_WORDS} words")

    rec.ok = not rec.errors
    rec.out_counts = {**_counts(ext), "contradictions": len(ext.contradictions),
                      "resolutions": len(ext.resolutions), "unverified_quotes": len(unverified)}
    rec.details = {
        "unverified_quotes": unverified,
        "contradictions": [{"subject": c.subject, "removed_ids": c.removed_ids} for c in ext.contradictions],
        "resolutions": [{"subject": r.subject, "kept_id": r.kept_id} for r in ext.resolutions],
        "items_in": n_in,
        "items_out": len(ext.components) + len(ext.relationships) + len(ext.boundaries),
    }
    return (ext if rec.ok else None), rec
