"""The v2 stages, wired from the work-package modules (see ../ADR.md "Pipeline" and INTERFACES.md).

Each stage is fn(ctx: StageContext) and passes objects to later stages through ctx.state:
  sources -> raw_extraction -> extraction -> raw_assumptions -> spec -> drawio_path/xml -> exports
LLM steps record their call info (model, fallback, tokens) via ctx.record_llm_call.
"""
from __future__ import annotations

import json
from pathlib import Path

from diagram_generator.contracts import AssumerOutput, Extraction, FinalSpec, HarnessRecord, SourceDoc

NO_ARCH_MESSAGE = "No architecture found in the documents"


def _fail(stage: str, message: str):
    from diagram_generator.pipeline import StageFailure
    return StageFailure(stage, message)


def _catalog(ctx):
    if "catalog" not in ctx.state:
        from diagram_generator.catalog.loader import load_catalog
        ctx.state["catalog"] = load_catalog(ctx.settings)
    return ctx.state["catalog"]


# ----------------------------------------------------------------------------- INGEST
def _read_design(path: Path):
    """An ADR design.json (adr_generator Design) or None when the file is not one."""
    from pydantic import ValidationError

    from adr_generator.contracts import Design
    from diagram_generator.design_spec import looks_like_design

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not looks_like_design(data):
        return None
    try:
        return Design.model_validate(data)
    except ValidationError:
        return None


def is_design_mode(ctx) -> bool:
    return ctx.state.get("design") is not None


def stage_ingest(ctx) -> None:
    from diagram_generator.ingest.extractors import ExtractionError, extract_sources

    ctx.emit("INGEST", "started", f"Reading {len(ctx.input_paths)} file(s)")
    jsons = [p for p in ctx.input_paths if p.suffix.lower() == ".json"]
    if jsons:
        if len(ctx.input_paths) != 1:
            raise _fail("INGEST", "Send one design.json on its own, or documents without a design.json")
        design = _read_design(jsons[0])
        if design is None:
            raise _fail("INGEST", f"{jsons[0].name} is not an ADR design (expected design.json with components)")
        ctx.state.update(design=design, sources=[])
        ctx.write_json("sources.json", [{"file": jsons[0].name, "kind": "design"}])
        ctx.emit("INGEST", "completed", f"Read the ADR design {jsons[0].name}",
                 counts={"components": len(design.components), "relationships": len(design.relationships),
                         "boundaries": len(design.boundaries)})
        return
    try:
        sources: list[SourceDoc] = extract_sources(ctx.input_paths, ctx.settings.max_text_chars)
    except ExtractionError as e:
        raise _fail("INGEST", str(e))
    ctx.state["sources"] = sources
    ctx.write_json("sources.json", [
        {"file": s.file, "chars_original": s.chars_original, "chars_sent": len(s.text), "truncated": s.truncated}
        for s in sources
    ])
    chars = sum(len(s.text) for s in sources)
    note = " (some text truncated to the limit)" if any(s.truncated for s in sources) else ""
    ctx.emit("INGEST", "completed", f"Extracted text from {len(sources)} file(s){note}",
             counts={"files": len(sources), "chars": chars})


# ----------------------------------------------------------------------------- LLM 1 + HARNESS 1
def _spec_from_design(ctx) -> None:
    """Design mode: the ADR's own components, boundaries and flows become the FinalSpec (code, no model call)."""
    from diagram_generator.design_spec import build_spec_from_design

    design = ctx.state["design"]
    ctx.emit("EXTRACTOR", "started", "Building the diagram spec from the ADR design (no model call)")
    res = build_spec_from_design(design, _catalog(ctx))
    if not res.spec.components:
        raise _fail("EXTRACTOR", NO_ARCH_MESSAGE)
    ctx.harness(HarnessRecord(step="design_spec", ok=True,
                              in_counts={"components": len(design.components),
                                         "relationships": len(design.relationships),
                                         "boundaries": len(design.boundaries)},
                              out_counts=res.counts, dropped=res.dropped, warnings=res.warnings))
    title = res.spec.title
    ctx.state.update(spec=res.spec, subtitle=res.subtitle, extraction=Extraction(title=title),
                     assumer_output=AssumerOutput())
    ctx.write_json("spec.json", res.spec)
    ctx.write_json("extraction.json", Extraction(title=title))
    ctx.write_json("assumptions.json", {"components": [], "relationships": [], "boundaries": []})
    ctx.write_json("unresolved.json", [])
    ctx.write_json("contradictions.json", {"contradictions": [], "resolutions": []})
    ctx.emit("EXTRACTOR", "completed", "Spec built from the ADR design", counts=res.counts)


def _skip(ctx, stage: str, why: str = "the spec comes from the ADR design") -> None:
    ctx.emit(stage, "completed", f"Skipped: {why}")


def stage_extractor(ctx) -> None:
    from diagram_generator.llm.extractor import extract

    if is_design_mode(ctx):
        return _spec_from_design(ctx)
    ctx.emit("EXTRACTOR", "started", "Extractor: listing components, relationships and contradictions")
    llm = ctx.llm("extractor")
    raw = extract(llm, ctx.settings, ctx.state["sources"], _catalog(ctx).extractor_prompt_section())
    ctx.record_llm_call("extractor", dict(llm.last_call))
    ctx.state["raw_extraction"] = raw
    ctx.write_json("extraction.raw.json", raw)
    c = raw if isinstance(raw, dict) else {}
    ctx.emit("EXTRACTOR", "completed", "Extractor returned",
             counts={k: len(c.get(k) or []) for k in ("components", "relationships", "boundaries", "contradictions")})


def _drop_unnamed_services(ctx, extraction: Extraction) -> Extraction:
    """Remove "inferred" AWS services that no document names (and the flows touching them).

    Stated items are kept as they are; actors, external systems and generic items are kept. This stops the
    Extractor from adding services nobody asked for, such as a WAF or a NAT gateway "implied" by the prose."""
    cat = _catalog(ctx)
    text = "\n".join(s.text for s in ctx.state["sources"])
    drop = {c.id for c in extraction.components
            if c.strength == "inferred" and c.kind in ("aws_service", "data_store")
            and not cat.is_named_in(c.service, c.name, text)}
    if not drop:
        return extraction
    names = [f"{c.name} ({c.service or 'no service'})" for c in extraction.components if c.id in drop]
    rels = [r for r in extraction.relationships if r.source not in drop and r.target not in drop]
    msg = (f"Dropped {len(drop)} inferred AWS service(s) that the documents never name: " + ", ".join(names))
    ctx.state.setdefault("package_warnings", []).append(f"HARNESS_1: {msg}")
    ctx.emit("HARNESS_1", "warning", msg)
    return extraction.model_copy(update={
        "components": [c for c in extraction.components if c.id not in drop], "relationships": rels})


def stage_harness_1(ctx) -> None:
    from diagram_generator.harness.h1_extraction import check_extraction
    from diagram_generator.llm.extractor import repair

    if is_design_mode(ctx):
        return _skip(ctx, "HARNESS_1")
    s = ctx.settings
    ctx.emit("HARNESS_1", "started", "Harness 1: checking the extraction")
    raw = ctx.state["raw_extraction"]
    extraction, record = check_extraction(raw, ctx.state["sources"], s)
    ctx.harness(record)
    attempt = 1
    while extraction is None and attempt <= s.extractor_repair_attempts:
        attempt += 1
        ctx.emit("HARNESS_1", "info", f"{len(record.errors)} blocking problem(s); asking the Extractor to repair")
        llm = ctx.llm("extractor")
        raw = repair(llm, s, ctx.state["sources"], _catalog(ctx).extractor_prompt_section(), raw, record.errors)
        ctx.record_llm_call("extractor_repair", dict(llm.last_call))
        ctx.write_json("extraction.raw.json", raw)
        extraction, record = check_extraction(raw, ctx.state["sources"], s)
        record.attempt = attempt
        ctx.harness(record)
    if extraction is None:
        raise _fail("HARNESS_1", "Extraction failed the checks: " + "; ".join(record.errors[:8]))
    if not extraction.components:
        raise _fail("HARNESS_1", NO_ARCH_MESSAGE)
    if s.named_services_only:
        extraction = _drop_unnamed_services(ctx, extraction)
        if not extraction.components:
            raise _fail("HARNESS_1", NO_ARCH_MESSAGE)
    ctx.state["extraction"] = extraction
    ctx.write_json("extraction.json", extraction)
    ctx.write_json("contradictions.json", {
        "contradictions": [c.model_dump() for c in extraction.contradictions],
        "resolutions": [r.model_dump() for r in extraction.resolutions],
    })
    ctx.emit("HARNESS_1", "completed", "Extraction accepted", counts=record.out_counts)


# ----------------------------------------------------------------------------- LLM 2 + HARNESS 2
def stage_assumer(ctx) -> None:
    from diagram_generator.llm.assumer import assume

    if is_design_mode(ctx):
        return _skip(ctx, "ASSUMER")
    if ctx.settings.assumptions_mode == "off":
        ctx.state["raw_assumptions"] = {}
        return _skip(ctx, "ASSUMER", "ASSUMPTIONS_MODE=off")
    ctx.emit("ASSUMER", "started", "Assumer: filling necessary gaps and listing unresolved items")
    llm = ctx.llm("assumer")
    raw = assume(llm, ctx.settings, ctx.state["sources"], ctx.state["extraction"],
                 _catalog(ctx).extractor_prompt_section())
    ctx.record_llm_call("assumer", dict(llm.last_call))
    ctx.state["raw_assumptions"] = raw
    ctx.write_json("assumptions.raw.json", raw)
    c = raw if isinstance(raw, dict) else {}
    ctx.emit("ASSUMER", "completed", "Assumer returned",
             counts={k: len(c.get(k) or []) for k in ("components", "relationships", "boundaries", "unresolved")})


def stage_harness_2(ctx) -> None:
    from diagram_generator.harness.h2_assumptions import check_assumptions

    if is_design_mode(ctx):
        return _skip(ctx, "HARNESS_2")
    ctx.emit("HARNESS_2", "started", "Harness 2: checking assumptions and building the final spec")
    out, spec, record = check_assumptions(ctx.state["raw_assumptions"], ctx.state["extraction"],
                                          ctx.state["sources"], ctx.settings, _catalog(ctx),
                                          draw=ctx.settings.assumptions_mode == "draw")
    ctx.harness(record)
    ctx.state["assumer_output"] = out
    ctx.state["spec"] = spec
    ctx.write_json("assumptions.json", {
        "components": [x.model_dump() for x in out.components],
        "relationships": [x.model_dump() for x in out.relationships],
        "boundaries": [x.model_dump() for x in out.boundaries],
    })
    ctx.write_json("unresolved.json", [u.model_dump() for u in out.unresolved])
    ctx.write_json("spec.json", spec)
    ctx.emit("HARNESS_2", "completed", "Final spec ready", counts=record.out_counts)


# ----------------------------------------------------------------------------- LLM 3 + HARNESS 3
def _ask_coder(ctx, call_name: str, fn) -> None:
    """Run one Diagram-coder call; store the layout or a coder error the retry can show the model."""
    from pydantic import ValidationError

    from diagram_generator.llm.bedrock import TRUNCATED, LLMError

    llm = ctx.llm("coder")
    try:
        layout = fn(llm)
        ctx.state.update(layout=layout, coder_error=None)
        ctx.write_json("layout.json", layout)
    except ValidationError as e:
        ctx.state["coder_error"] = f"The layout did not match the submit_layout schema: {str(e)[:1500]}"
    except LLMError as e:
        if e.kind != TRUNCATED:
            raise
        ctx.state["coder_error"] = ("The answer was cut off before the layout was complete. Return a more compact "
                                    "layout: no optional fields.")
    finally:
        ctx.record_llm_call(call_name, dict(llm.last_call))


def stage_diagram_coder(ctx) -> None:
    from diagram_generator.llm.layout_coder import generate_layout

    spec: FinalSpec = ctx.state["spec"]
    what = (f"{len(spec.components)} components, {len(spec.boundaries)} groups "
            f"and {len(spec.relationships)} relationships")
    if ctx.settings.layout_engine == "elk":
        from diagram_generator.render.elk_layout import elk_layout

        ctx.emit("DIAGRAM_CODER", "started", f"Layout engine (ELK, no model call): laying out {what}")
        res = elk_layout(spec, ctx.settings, ctx.state.get("subtitle", ""))
        if res.layout is not None:
            ctx.state.update(layout=res.layout, coder_error=None, layout_source="elk")
            ctx.write_json("layout.json", res.layout)
            ctx.emit("DIAGRAM_CODER", "completed", "Layout computed by ELK",
                     counts={"nodes": len(res.layout.nodes), "groups": len(res.layout.groups)})
            return
        ctx.emit("DIAGRAM_CODER", "warning", f"ELK layout failed ({res.error}); falling back to the LLM coder")
        ctx.state.setdefault("package_warnings", []).append(f"DIAGRAM_CODER: ELK layout failed: {res.error}")
    ctx.state["layout_source"] = "llm"
    ctx.emit("DIAGRAM_CODER", "started", f"Diagram coder: laying out {what}")
    _ask_coder(ctx, "coder", lambda llm: generate_layout(llm, ctx.settings, spec))
    layout = ctx.state.get("layout")
    counts = ({"nodes": len(layout.nodes), "groups": len(layout.groups)}
              if layout and not ctx.state.get("coder_error") else {})
    ctx.emit("DIAGRAM_CODER", "completed",
             "Layout received" if counts else f"No usable layout: {ctx.state['coder_error'][:200]}", counts=counts)


def _build_and_check(ctx, spec: FinalSpec, layout, attempt: int, layout_rep) -> HarnessRecord:
    """Layout -> draw.io XML -> vendored fixers -> libavoid edge routing -> vendored validator -> coverage check.

    Routing runs after the fixers because they may move actor boxes."""
    from diagram_generator.harness.h3_drawio import check_drawio
    from diagram_generator.render.drawio_builder import build_xml
    from diagram_generator.render.drawio_tools import post_process, validate
    from diagram_generator.render.edge_router import route_drawio

    s = ctx.settings
    path = ctx.write_text("diagram.drawio", build_xml(spec, layout))
    from diagram_generator.harness.layout_check import drawn_overlaps

    pp = post_process(path, s)
    if not pp.ok:
        ctx.emit("HARNESS_3", "warning", f"Post-processing did not complete: {pp.output[-300:]}")
    moved = drawn_overlaps(path.read_text(encoding="utf-8"), spec)  # the fixers may have moved boxes
    routing = route_drawio(path, s)
    if routing.errors:
        ctx.emit("HARNESS_3", "warning", f"Edge routing skipped: {routing.errors[0][:300]}")
    validation = validate(path, s)
    record = check_drawio(path.read_text(encoding="utf-8"), spec, validation, attempt)
    record.errors = list(layout_rep.errors) + record.errors
    record.warnings = (list(layout_rep.warnings) + moved + [f"edge routing: {e}" for e in routing.errors]
                       + [f"edge '{e}' has no libavoid route; draw.io routes it" for e in routing.unrouted]
                       + record.warnings)
    record.out_counts.update(layout_rep.counts)
    record.out_counts.update(edges_routed=len(routing.routed), edges_unrouted=len(routing.unrouted))
    record.ok = not record.errors
    record.details["post_process"] = pp.output[-2000:]
    ctx.state.update(drawio_path=path, validation=validation)
    return record


def stage_harness_3(ctx) -> None:
    from diagram_generator.contracts import Layout
    from diagram_generator.harness.layout_check import check_layout, fit_groups
    from diagram_generator.llm.layout_coder import retry_layout

    s = ctx.settings
    spec: FinalSpec = ctx.state["spec"]
    total = 1 + max(0, s.coder_max_retries)
    record: HarnessRecord | None = None
    ctx.emit("HARNESS_3", "started", "Harness 3: checking the layout, building and validating the diagram")
    for attempt in range(1, total + 1):
        layout = ctx.state.get("layout")
        geometry_bad = True
        if ctx.state.get("coder_error") or layout is None:
            record = HarnessRecord(step="harness_3", attempt=attempt, ok=False,
                                   errors=[ctx.state.get("coder_error") or "No layout returned"],
                                   in_counts={"components": len(spec.components),
                                              "relationships": len(spec.relationships)})
        else:
            layout, grown = fit_groups(spec, layout)
            ctx.state["layout"] = layout  # the retry and the final drawing start from the grown groups
            rep = check_layout(spec, layout)
            rep.warnings = grown + rep.warnings
            geometry_bad = bool(rep.errors)
            if rep.errors:  # do not build a drawing we already know is wrong; ask for a fix first
                record = HarnessRecord(step="harness_3", attempt=attempt, ok=False, errors=rep.errors,
                                       warnings=rep.warnings, out_counts=rep.counts,
                                       in_counts={"components": len(spec.components),
                                                  "relationships": len(spec.relationships),
                                                  "boundaries": len(spec.boundaries)})
            else:
                record = _build_and_check(ctx, spec, layout, attempt, rep)
        ctx.harness(record)
        if record.ok or attempt == total:
            break
        if ctx.state.get("layout_source") == "elk" and not geometry_bad:
            # ELK's geometry passed; a model retry cannot fix validator or coverage problems, so do not pay for one
            break
        ctx.state["layout_source"] = "llm"
        ctx.emit("HARNESS_3", "info", f"Attempt {attempt}: {len(record.errors)} problem(s); asking the Diagram "
                                      f"coder to fix them (retry {attempt}/{total - 1})")
        prev = ctx.state.get("layout") or Layout()
        errs = list(record.errors)
        _ask_coder(ctx, f"coder_retry_{attempt}", lambda llm: retry_layout(llm, s, spec, prev, errs))

    assert record is not None
    layout = ctx.state.get("layout")
    if not ctx.state.get("drawio_path") or not record.ok:
        if layout is None:
            raise _fail("HARNESS_3", f"No usable layout after {total} attempt(s): " + "; ".join(record.errors[:4]))
        # best effort: draw the last layout we have, so the SA still gets a diagram plus the list of problems.
        # Remaining layout problems become warnings; validator and coverage problems stay errors.
        from diagram_generator.harness.layout_check import LayoutReport
        rep = check_layout(spec, layout)
        soft = LayoutReport(warnings=[f"exported with layout problem: {e}" for e in rep.errors] + rep.warnings,
                            counts=rep.counts)
        record = _build_and_check(ctx, spec, layout, total, soft)
        ctx.harness(record)
    ctx.state["h3_record"] = record
    validation = ctx.state.get("validation")
    if validation is not None and not validation.ok:
        raise _fail("HARNESS_3", "Diagram XML failed validation: " + "; ".join(validation.errors[:6]))
    if record.ok and not record.warnings:
        ctx.emit("HARNESS_3", "completed", "Diagram covers the full spec with a clean layout", counts=record.out_counts)
    elif record.ok:
        ctx.emit("HARNESS_3", "completed", f"Diagram covers the full spec ({len(record.warnings)} warning(s))",
                 counts=record.out_counts)
    else:
        ctx.emit("HARNESS_3", "warning", "Diagram exported with problems: " + "; ".join(record.errors[:4]),
                 counts=record.out_counts)


# ----------------------------------------------------------------------------- EXPORT
def stage_export(ctx) -> None:
    from diagram_generator.render.drawio_tools import export

    ctx.emit("EXPORT", "started", "Exporting PNG and SVG with draw.io")
    res = export(ctx.state["drawio_path"], ctx.job_dir, ctx.settings)
    png_ok = bool(res.png and Path(res.png).exists() and Path(res.png).stat().st_size > 0)
    svg_ok = bool(res.svg and Path(res.svg).exists() and Path(res.svg).stat().st_size > 0)
    record = HarnessRecord(step="export", ok=png_ok and svg_ok, in_counts={"drawio": 1},
                           out_counts={"png": int(png_ok), "svg": int(svg_ok)}, errors=list(res.errors))
    ctx.harness(record)
    ctx.state["export_record"] = record
    if not png_ok:
        raise _fail("EXPORT", "draw.io export failed: " + "; ".join(res.errors[:3] or ["no PNG produced"]))
    ctx.emit("EXPORT", "completed", "Exported diagram.png and diagram.svg" if svg_ok else "Exported diagram.png (SVG failed)",
             counts=record.out_counts)


# ----------------------------------------------------------------------------- PACKAGE
def stage_package(ctx) -> None:
    from diagram_generator.harness.log import read_records

    ctx.emit("PACKAGE", "started", "Writing the report")
    records = read_records(ctx.job_dir)
    extraction: Extraction = ctx.state["extraction"]
    out: AssumerOutput = ctx.state["assumer_output"]
    spec: FinalSpec = ctx.state["spec"]
    h3: HarnessRecord = ctx.state["h3_record"]
    exp: HarnessRecord = ctx.state["export_record"]

    warnings = [f"{r.step}: {w}" for r in records for w in r.warnings] + list(ctx.state.get("package_warnings", []))
    drawn = {c.id for c in spec.components} | {r.id for r in spec.relationships} | {b.id for b in spec.boundaries}
    suggestions = [{"id": x.id, "type": t, "name": getattr(x, "name", "") or getattr(x, "label", ""),
                    "service": getattr(x, "service", None), "reasoning": x.reasoning}
                   for t, items in (("component", out.components), ("relationship", out.relationships),
                                    ("boundary", out.boundaries)) for x in items if x.id not in drawn]
    added = [c.name for c in spec.components if c.origin == "assumed"]
    if added:  # the ADR's spec.json and usage.json are built before the diagram and never see these
        warnings.append(f"PACKAGE: The diagram added {len(added)} assumed component(s) that are not in the ADR: "
                        f"{', '.join(added)}. spec.json and usage.json do not include them; update the ADR once a "
                        f"reviewer confirms them.")
    if not (h3.ok and exp.ok):
        status = "fail"
    elif warnings or extraction.contradictions or out.unresolved:
        status = "pass_with_warnings"
    else:
        status = "pass"

    by_origin = lambda items: {o: sum(1 for i in items if i.origin == o) for o in ("stated", "inferred", "assumed")}
    report = {
        "status": status,
        "summary": {
            "title": spec.title,
            "components": len(spec.components), "relationships": len(spec.relationships),
            "boundaries": len(spec.boundaries),
            "components_by_origin": by_origin(spec.components),
            "relationships_by_origin": by_origin(spec.relationships),
            "contradictions": len(extraction.contradictions),
            "resolutions": len(extraction.resolutions),
            "assumptions": len(out.components) + len(out.relationships) + len(out.boundaries),
            "unresolved": len(out.unresolved),
        },
        "input": "design" if is_design_mode(ctx) else "documents",
        "layout_engine": ctx.state.get("layout_source"),
        "model_calls": len(ctx.llm_calls),
        "suggestions": suggestions,   # accepted assumptions that were NOT drawn (ASSUMPTIONS_MODE=suggest)
        "steps": [r.model_dump(exclude={"details"}) for r in records],
        "diagram_coverage": {"ok": h3.ok, "errors": h3.errors, **h3.details},
        "warnings": warnings,
        "outputs": ["diagram.png", "diagram.svg", "diagram.drawio", "assumptions.json",
                    "unresolved.json", "contradictions.json"],
    }
    ctx.write_json("report.json", report)
    ctx.state["report_status"] = status
    ctx.emit("PACKAGE", "completed", f"Report: {status}", counts=report["summary"] | {})


def default_stages() -> list[tuple[str, object]]:
    return [
        ("INGEST", stage_ingest),
        ("EXTRACTOR", stage_extractor),
        ("HARNESS_1", stage_harness_1),
        ("ASSUMER", stage_assumer),
        ("HARNESS_2", stage_harness_2),
        ("DIAGRAM_CODER", stage_diagram_coder),
        ("HARNESS_3", stage_harness_3),
        ("EXPORT", stage_export),
        ("PACKAGE", stage_package),
    ]


__all__ = ["default_stages", "NO_ARCH_MESSAGE", "json"]
