"""Harness 2: checks the Assumer's additions and builds the FinalSpec (code, no LLM).

Never fails the run: a bad assumption is dropped and logged with its reason.
Drops: schema-invalid item; id already used by an extracted or earlier assumed item (this is also the
  add-only check: an assumer can never replace an extracted item); empty or unknown `supports_ids`;
  unknown boundary/parent/source/target; no evidence (no non-blank quote); a quote not found in its file.
Warnings: assumed components above the cap, label over 4 words, unresolved related_ids that are unknown
  (filtered), a component drawn with a generic icon because the catalog has no match.
FinalSpec: extracted items get origin = strength, assumed items origin = "assumed",
  every component gets icon = catalog.resolve(service, name, kind). Counts are asserted to reconcile.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from diagram_generator.config import Settings
from diagram_generator.contracts import (
    AssumedBoundary, AssumedComponent, AssumedRelationship, AssumerOutput, Extraction, FinalSpec,
    HarnessRecord, SourceDoc, SpecBoundary, SpecComponent, SpecRelationship, Unresolved,
)
from diagram_generator.harness.quotes import quote_found

MAX_LABEL_WORDS = 4


def _parse_list(raw: dict, key: str, model: type[BaseModel], rec: HarnessRecord) -> list:
    items = raw.get(key) or []
    if not isinstance(items, list):
        rec.warnings.append(f"assumer output '{key}' is not a list; ignored")
        return []
    out = []
    for i, it in enumerate(items):
        try:
            out.append(model.model_validate(it))
        except ValidationError as e:
            ident = it.get("id") if isinstance(it, dict) and it.get("id") else f"{key}[{i}]"
            msg = "; ".join(f"{'.'.join(str(p) for p in er['loc'])}: {er['msg']}" for er in e.errors()[:3])
            rec.dropped.append({"id": str(ident), "reason": f"schema: {msg}"})
        except Exception as e:  # not a dict etc.
            rec.dropped.append({"id": f"{key}[{i}]", "reason": f"schema: {e}"})
    return out


def check_assumptions(raw: Any, extraction: Extraction, sources: list[SourceDoc], settings: Settings,
                      catalog, draw: bool = True) -> tuple[AssumerOutput, FinalSpec, HarnessRecord]:
    """draw=False (ASSUMPTIONS_MODE=suggest): accepted assumptions are returned in AssumerOutput as suggestions
    but left out of the FinalSpec, so nothing the documents do not contain is drawn."""
    rec = HarnessRecord(step="harness_2", ok=True)
    ext_counts = {"components": len(extraction.components), "relationships": len(extraction.relationships),
                  "boundaries": len(extraction.boundaries)}
    rec.in_counts = dict(ext_counts)
    if not isinstance(raw, dict):
        rec.warnings.append("assumer output is not an object; no assumptions applied")
        raw = {}

    cands_b = _parse_list(raw, "boundaries", AssumedBoundary, rec)
    cands_c = _parse_list(raw, "components", AssumedComponent, rec)
    cands_r = _parse_list(raw, "relationships", AssumedRelationship, rec)
    cands_u = _parse_list(raw, "unresolved", Unresolved, rec)

    used_ids: set[str] = ({c.id for c in extraction.components} | {b.id for b in extraction.boundaries}
                          | {r.id for r in extraction.relationships})
    boundary_ids: set[str] = {b.id for b in extraction.boundaries}
    component_ids: set[str] = {c.id for c in extraction.components}
    known: set[str] = set(used_ids)           # ids usable in supports_ids / related_ids
    by_file = {s.file: s.text for s in sources}
    unverified: list[dict] = []

    def drop(item_id: str, reason: str) -> None:
        rec.dropped.append({"id": item_id, "reason": reason})

    def common_problem(it) -> str | None:
        if not it.id.strip():
            return "empty id"
        if it.id in used_ids:
            return f"id '{it.id}' already exists (assumer is add-only)"
        if not it.supports_ids:
            return "supports_ids is empty"
        bad = [s for s in it.supports_ids if s not in known]
        if bad:
            return f"unknown supports_ids: {', '.join(bad)}"
        it.evidence = [e for e in it.evidence if e.quote.strip()]
        if not it.evidence:
            return "no evidence quote"
        return evidence_problem(it)

    def evidence_problem(it) -> str | None:
        """An assumption needs real evidence: every quote must be found in its file (else it is dropped)."""
        bad = []
        for ev in it.evidence:
            if ev.file not in by_file:
                bad.append({"id": it.id, "file": ev.file, "quote": ev.quote, "reason": "file not among the inputs"})
            elif not quote_found(ev.quote, by_file[ev.file]):
                bad.append({"id": it.id, "file": ev.file, "quote": ev.quote, "reason": "quote not found in file"})
        if not bad:
            return None
        unverified.extend(bad)
        return f"evidence not found in the documents ({bad[0]['reason']}: {bad[0]['quote'][:80]!r})"

    def accept(it) -> None:
        used_ids.add(it.id)
        known.add(it.id)
        rec.added_ids.append(it.id)

    # ---- boundaries (multi-pass so a parent listed after its child is still accepted)
    acc_b: list[AssumedBoundary] = []
    pending = list(cands_b)
    progress = True
    while pending and progress:
        progress = False
        rest = []
        for b in pending:
            if b.parent is not None and b.parent not in boundary_ids and any(p.id == b.parent for p in pending):
                rest.append(b)       # parent still pending; retry next pass
                continue
            prob = common_problem(b)
            if prob is None and b.parent is not None and b.parent not in boundary_ids:
                prob = f"unknown parent '{b.parent}'"
            if prob:
                drop(b.id, prob)
            else:
                accept(b)
                boundary_ids.add(b.id)
                acc_b.append(b)
            progress = True
        pending = rest
    for b in pending:
        drop(b.id, "boundary parent cycle or unresolved parent order")

    # ---- components
    acc_c: list[AssumedComponent] = []
    for c in cands_c:
        prob = common_problem(c)
        if prob is None and c.boundary is not None and c.boundary not in boundary_ids:
            prob = f"unknown boundary '{c.boundary}'"
        if prob:
            drop(c.id, prob)
            continue
        accept(c)
        component_ids.add(c.id)
        acc_c.append(c)

    # ---- relationships
    acc_r: list[AssumedRelationship] = []
    for r in cands_r:
        prob = common_problem(r)
        if prob is None:
            for end, val in (("source", r.source), ("target", r.target)):
                if val not in component_ids:
                    prob = f"unknown {end} '{val}'"
                    break
        if prob:
            drop(r.id, prob)
            continue
        accept(r)
        if len(r.label.split()) > MAX_LABEL_WORDS:
            rec.warnings.append(f"assumed relationship '{r.id}' label has more than {MAX_LABEL_WORDS} words")
        acc_r.append(r)

    # ---- unresolved (kept; unknown related_ids filtered)
    acc_u: list[Unresolved] = []
    for u in cands_u:
        bad = [i for i in u.related_ids if i not in known]
        if bad:
            rec.warnings.append(f"unresolved '{u.about}': unknown related_ids removed: {', '.join(bad)}")
            u.related_ids = [i for i in u.related_ids if i in known]
        acc_u.append(u)

    # ---- warnings
    threshold = max(settings.assumption_warn_min, settings.assumption_warn_ratio * len(extraction.components))
    if len(acc_c) > threshold:
        rec.warnings.append(
            f"{len(acc_c)} assumed components exceeds the cap of {threshold:g} "
            f"(ratio {settings.assumption_warn_ratio} of {len(extraction.components)} extracted, min {settings.assumption_warn_min}); review them")
    for d in rec.dropped:
        rec.warnings.append(f"dropped assumption '{d['id']}': {d['reason']}")

    out = AssumerOutput(components=acc_c, relationships=acc_r, boundaries=acc_b, unresolved=acc_u)

    # ---- FinalSpec
    spec_c = [SpecComponent(id=c.id, name=c.name, service=c.service, kind=c.kind, boundary=c.boundary,
                            origin=c.strength, icon=catalog.resolve(c.service, c.name, c.kind))
              for c in extraction.components]
    drawn_c, drawn_r, drawn_b = (acc_c, acc_r, acc_b) if draw else ([], [], [])
    spec_c += [SpecComponent(id=c.id, name=c.name, service=c.service, kind=c.kind, boundary=c.boundary,
                             origin="assumed", icon=catalog.resolve(c.service, c.name, c.kind))
               for c in drawn_c]
    spec_b = [SpecBoundary(id=b.id, type=b.type, name=b.name, parent=b.parent, origin=b.strength)
              for b in extraction.boundaries]
    spec_b += [SpecBoundary(id=b.id, type=b.type, name=b.name, parent=b.parent, origin="assumed") for b in drawn_b]
    spec_r = [SpecRelationship(id=r.id, source=r.source, target=r.target, label=r.label,
                               direction=r.direction, origin=r.strength) for r in extraction.relationships]
    spec_r += [SpecRelationship(id=r.id, source=r.source, target=r.target, label=r.label,
                                direction=r.direction, origin="assumed") for r in drawn_r]
    spec = FinalSpec(title=extraction.title, components=spec_c, relationships=spec_r, boundaries=spec_b)
    for c in spec.components:
        if not c.icon.matched and c.kind not in ("actor", "external", "generic"):
            rec.warnings.append(f"component '{c.id}' ({c.name}): no icon for service '{c.service}'; "
                                "drawn with a generic icon")

    # ---- reconcile (explicit raise, not `assert`, so it survives python -O)
    for key, final, ext_n, added in (
        ("components", spec.components, ext_counts["components"], drawn_c),
        ("relationships", spec.relationships, ext_counts["relationships"], drawn_r),
        ("boundaries", spec.boundaries, ext_counts["boundaries"], drawn_b),
    ):
        if len(final) != ext_n + len(added):
            raise AssertionError(f"final {key} {len(final)} != extracted {ext_n} + assumed {len(added)}")

    rec.out_counts = {"components": len(spec.components), "relationships": len(spec.relationships),
                      "boundaries": len(spec.boundaries), "assumed_components": len(acc_c),
                      "assumed_relationships": len(acc_r), "assumed_boundaries": len(acc_b),
                      "unresolved": len(acc_u), "dropped": len(rec.dropped),
                      "unverified_quotes": len(unverified)}
    rec.details = {"unverified_quotes": unverified}
    return out, spec, rec
