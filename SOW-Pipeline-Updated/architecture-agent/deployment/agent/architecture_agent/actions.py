"""The agent's actions (request contract: see agent.py). Every action is stateless: nothing persists between
invocations, so the caller keeps the design, the SRS text and the ADR between steps (as the UI already does).

  extract_srs       SRS file                              -> its text
  generate_adr      SRS text (+ SOW scope)                -> design, adr.md, spec.json, scope warnings
  revise_adr        design + SRS text + instruction       -> revised design, adr.md, spec.json
  estimate_usage    design + SRS text                     -> usage.json, usage_assumptions.md
  generate_diagram  design (preferred) or adr.md          -> diagram, report, assumptions, gaps, contradictions
  run_pipeline      SRS file (+ SOW scope)                -> ADR, usage and diagram in one call (the default)

The optional `scope` payload carries the SOW's agreed in-scope / out-of-scope split into the design
(adr_generator/scope.py). The availability model (how many AZs) comes from settings, not the payload
(adr_generator/availability.py).
"""
from __future__ import annotations

import base64
import binascii
import json
import queue
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from adr_generator import cli as adr_cli
from adr_generator.config import get_settings as get_adr_settings
from adr_generator.contracts import Design
from adr_generator.generator import generate
from adr_generator.ingest import InputError, read_srs
from adr_generator.llm.bedrock import LLMError, make_llm
from adr_generator.render.adr_md import render_adr
from adr_generator.render.spec_json import build_spec
from adr_generator.revise import revise_design
from adr_generator.scope import parse_scope, violations as scope_violations
from diagram_generator import cli as diagram_cli
from usage_estimator import cli as usage_cli

ALLOWED_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf"}
MAX_SRS_BYTES = 20 * 1024 * 1024   # same limit as MAX_UPLOAD_MB in both pipelines
LOG_TAIL_CHARS = 4000


# ----------------------------------------------------------------------------- request helpers
def _read_request(payload: dict) -> tuple[str, bytes]:
    name = Path(str(payload.get("srs_filename") or "srs.md")).name
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported SRS file type; allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    if payload.get("srs_base64"):
        try:
            data = base64.b64decode(payload["srs_base64"], validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("srs_base64 is not valid base64") from e
    elif isinstance(payload.get("srs_text"), str) and payload["srs_text"].strip():
        data = payload["srs_text"].encode("utf-8")
    else:
        raise ValueError("Send srs_text or srs_base64")
    if len(data) > MAX_SRS_BYTES:
        raise ValueError(f"SRS is larger than {MAX_SRS_BYTES // (1024 * 1024)} MB")
    return name, data


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Send {key}")
    return value


def _failed(stage: str, error: str, log: str = "") -> dict:
    return {"status": "failed", "stage": stage, "error": error, "log_tail": log}


def _run(main, argv: list[str], **kwargs) -> tuple[int, str]:
    """Run one pipeline's CLI main() in this process; return its exit code and the tail of its status lines."""
    lines: list[str] = []

    def out(line: str) -> None:
        lines.append(line)
        print(line, flush=True)  # AgentCore ships stdout to CloudWatch Logs

    code = main(argv, out=out, **kwargs)
    return code, "\n".join(lines)[-LOG_TAIL_CHARS:]


def _say(stage: str, message: str, counts: dict | None = None) -> None:
    """One live status line, in the same format as the CLI runners (AgentCore ships stdout to CloudWatch Logs)."""
    print(adr_cli.status_line(stage, message, counts), flush=True)


def _say_done(stage: str, response: dict) -> None:
    t = response["tokens"]
    _say(stage, f"Design received in {response['seconds']}s ({t['input']} in / {t['output']} out tokens, "
                f"stop: {response['stop_reason']})", response["counts"])


def _only_dir(parent: Path) -> Path | None:
    dirs = [p for p in parent.glob("*") if p.is_dir()] if parent.is_dir() else []
    return dirs[0] if len(dirs) == 1 else None


def _b64(path: Path) -> str | None:
    return base64.b64encode(path.read_bytes()).decode("ascii") if path.is_file() else None


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# ----------------------------------------------------------------------------- ADR actions
def extract_srs(payload: dict) -> dict:
    """Read an uploaded SRS file (any allowed type) into text, for the UI's "check the text" step."""
    try:
        name, data = _read_request(payload)
    except ValueError as e:
        return _failed("request", str(e))
    work = Path(tempfile.mkdtemp(prefix="arch-agent-"))
    try:
        path = work / name
        path.write_bytes(data)
        try:
            _, text, truncated = read_srs(path, get_adr_settings())
        except InputError as e:
            return _failed("ingest", str(e))
        return {"status": "completed", "srs_text": text, "filename": name, "truncated": truncated}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _counts(design: Design) -> dict:
    return {"requirements": len(design.requirements), "decisions": len(design.decisions),
            "components": len(design.components), "relationships": len(design.relationships),
            "assumptions": len(design.assumptions), "assumed_components": sum(1 for c in design.components if c.assumed)}


def _design_response(run_id: str, design: Design, name: str, llm, scope=None) -> dict:
    now = datetime.now(timezone.utc)
    call = llm.last_call or {}
    # Advisory, not fatal: report an out-of-scope service rather than silently dropping a component.
    warnings = scope_violations(design, scope)
    return {
        "status": "completed",
        "run_id": run_id,
        "design": design.model_dump(mode="json"),
        "adr_md": render_adr(design, source_name=name, run_date=now.strftime("%Y-%m-%d")),
        "spec_json": build_spec(design, source_name=name, generated_at=now.isoformat()),
        "scope": scope.model_dump(mode="json") if scope else None,
        "scope_warnings": warnings,
        "counts": _counts(design),
        "tokens": {"input": call.get("input_tokens", 0), "output": call.get("output_tokens", 0)},
        "seconds": call.get("seconds", 0.0),
        "model_id": call.get("model_id") or llm.model_id,
        "fallback_used": bool(call.get("fallback_used")),
        "stop_reason": call.get("stop_reason"),
    }


def generate_adr(payload: dict) -> dict:
    """SRS text -> Design -> adr.md and spec.json (one model call)."""
    try:
        srs_text = _text(payload, "srs_text")
    except ValueError as e:
        return _failed("request", str(e))
    name = Path(str(payload.get("srs_filename") or "srs.md")).name
    scope = parse_scope(payload)
    settings = get_adr_settings()
    llm = make_llm(settings)
    _say("INGEST", f"Read {name}", {"chars": len(srs_text)})
    if scope:
        _say("SCOPE", "Applying the SOW scope",
             {"in_scope": len(scope.in_scope), "out_of_scope": len(scope.out_of_scope)})
    _say("DESIGN", f"Calling {llm.model_id} (one call, this can take a few minutes)")
    try:
        design, _ = generate(llm, settings, name, srs_text, scope)
    except LLMError as e:
        _say("FAILED", f"DESIGN: {e} [{e.kind}]")
        return _failed("adr", f"{e} [{e.kind}]")
    response = _design_response(adr_cli.new_run_id(), design, name, llm, scope)
    _say_done("DESIGN", response)
    for warning in response["scope_warnings"]:
        _say("SCOPE", warning)
    return response


def revise_adr(payload: dict) -> dict:
    """Design + SRS text + instruction -> revised Design -> adr.md and spec.json (one model call)."""
    try:
        srs_text, instruction = _text(payload, "srs_text"), _text(payload, "instruction")
        if not isinstance(payload.get("design"), dict):
            raise ValueError("Send design")
        design_in = Design.model_validate(payload["design"])
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        return _failed("request", str(e))
    run_id = str(payload.get("run_id") or adr_cli.new_run_id())
    scope = parse_scope(payload)
    settings = get_adr_settings()
    llm = make_llm(settings)
    _say("REVISE", f"Calling {llm.model_id} (one call, this can take a few minutes)")
    try:
        design, _ = revise_design(llm, settings, design_in, srs_text, instruction, scope)
    except LLMError as e:
        _say("FAILED", f"REVISE: {e} [{e.kind}]")
        return _failed("adr", f"{e} [{e.kind}]")
    response = _design_response(run_id, design, design_in.title or run_id, llm, scope)
    _say_done("REVISE", response)
    for warning in response["scope_warnings"]:
        _say("SCOPE", warning)
    return response


# ----------------------------------------------------------------------------- usage action
def estimate_usage(payload: dict) -> dict:
    """Design + SRS text -> usage.json and usage_assumptions.md (one model call). The UI calls it silently after
    generate_adr and after every revise_adr."""
    try:
        srs_text = _text(payload, "srs_text")
        if not isinstance(payload.get("design"), dict):
            raise ValueError("Send design")
        design = Design.model_validate(payload["design"])
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        return _failed("request", str(e))
    name = Path(str(payload.get("srs_filename") or "srs.md")).name
    settings = get_adr_settings()
    llm = make_llm(settings)
    _say("USAGE", f"Calling {llm.model_id} (one call)")
    try:
        result = usage_cli.run(llm, settings, design, name, srs_text)
    except LLMError as e:
        _say("FAILED", f"USAGE: {e} [{e.kind}]")
        return _failed("usage", f"{e} [{e.kind}]")
    call = result.call
    response = {
        "status": "completed",
        "run_id": str(payload.get("run_id") or ""),
        "usage_json": result.usage,
        "usage_assumptions_md": result.assumptions_md,
        "counts": usage_cli.counts(result),
        "tokens": {"input": call.get("input_tokens", 0), "output": call.get("output_tokens", 0)},
        "seconds": call.get("seconds", 0.0),
        "model_id": call.get("model_id") or llm.model_id,
        "fallback_used": bool(call.get("fallback_used")),
        "stop_reason": call.get("stop_reason"),
    }
    t = response["tokens"]
    _say("USAGE", f"Estimate received in {response['seconds']}s ({t['input']} in / {t['output']} out tokens, "
                  f"stop: {response['stop_reason']})", response["counts"])
    return response


def _usage_stage(run_dir: Path) -> dict:
    """ADR run folder -> usage files in the same folder. A failure is reported here and never stops the diagram."""
    code, log = _run(usage_cli.main, [str(run_dir)])
    if code != 0 or not (run_dir / "usage.json").is_file():
        return {"status": "failed", "stage": "usage", "error": "Usage estimation failed", "log_tail": log}
    return {"status": "completed", "usage_json": _json(run_dir / "usage.json"),
            "usage_assumptions_md": (run_dir / "usage_assumptions.md").read_text(encoding="utf-8"),
            "usage_metadata": _json(run_dir / "usage_metadata.json")}


# ----------------------------------------------------------------------------- diagram action
def _diagram_stage(adr_path: Path, out_dir: Path, on_event: Callable[[dict], None] | None = None) -> dict:
    """adr.md -> diagram (Extractor -> Assumer -> Diagram coder -> draw.io export). Returns the response body."""
    code, log = _run(diagram_cli.main, [str(adr_path), "--out", str(out_dir)], on_event=on_event)
    job_dir = _only_dir(out_dir / "jobs")
    job = _json(job_dir / "job.json") if job_dir else None
    result = {
        "job_id": job_dir.name if job_dir else None,
        "diagram": {
            "png_base64": _b64(job_dir / "diagram.png") if job_dir else None,
            "svg_base64": _b64(job_dir / "diagram.svg") if job_dir else None,
            "drawio_xml": (job_dir / "diagram.drawio").read_text(encoding="utf-8")
            if job_dir and (job_dir / "diagram.drawio").is_file() else None,
            "report": _json(job_dir / "report.json") if job_dir else None,
            "assumptions": _json(job_dir / "assumptions.json") if job_dir else None,
            "unresolved": _json(job_dir / "unresolved.json") if job_dir else None,
            "contradictions": _json(job_dir / "contradictions.json") if job_dir else None,
        },
        "diagram_metadata": _json(job_dir / "metadata.json") if job_dir else None,
    }
    if code != 0 or not job or job.get("status") != "COMPLETED":
        error = (job or {}).get("error") or {}
        return result | {"status": "failed", "stage": "diagram",
                         "error": error.get("message") or "Diagram generation failed", "log_tail": log}
    return result | {"status": "completed", "report_status": job.get("report_status")}


def _diagram_input(payload: dict, work: Path) -> Path:
    """The diagram's input file. A `design` (from generate_adr / revise_adr) is preferred: the diagram is then
    drawn from the design itself, with no model call and no re-reading of the ADR prose. `adr_md` alone falls
    back to the document pipeline (Extractor + Assumer)."""
    inp = work / "input"
    inp.mkdir()
    design = payload.get("design")
    if design is not None:
        if not isinstance(design, dict):
            raise ValueError("design must be an object")
        Design.model_validate(design)   # raises ValueError on a malformed design
        path = inp / "design.json"
        path.write_text(json.dumps(design, ensure_ascii=False), encoding="utf-8")
        return path
    path = inp / "adr.md"
    path.write_text(_text(payload, "adr_md"), encoding="utf-8")
    return path


def generate_diagram(payload: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="arch-agent-"))
    try:
        try:
            src = _diagram_input(payload, work)
        except ValueError as e:  # pydantic's ValidationError is a ValueError
            return _failed("request", str(e))
        return _diagram_stage(src, work / "diagram")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def stream_diagram(payload: dict) -> Iterator[dict]:
    """Same as generate_diagram, but yields {"event": "progress", ...} for every pipeline event (stage, kind,
    message, counts, progress 0-100) and ends with {"event": "result", ...}. Sent as server-sent events."""
    work = Path(tempfile.mkdtemp(prefix="arch-agent-"))
    events: queue.Queue = queue.Queue()
    done = object()
    try:
        try:
            adr_path = _diagram_input(payload, work)
        except ValueError as e:
            yield {"event": "result"} | _failed("request", str(e))
            return

        def work_fn() -> None:
            try:
                events.put({"event": "result"} | _diagram_stage(adr_path, work / "diagram",
                                                              lambda ev: events.put({"event": "progress"} | ev)))
            except Exception as e:  # never leave the stream hanging
                events.put({"event": "result"} | _failed("diagram", f"{type(e).__name__}: {e}"))
            finally:
                events.put(done)

        threading.Thread(target=work_fn, daemon=True).start()
        while (item := events.get()) is not done:
            yield item
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ----------------------------------------------------------------------------- whole pipeline
def run_pipeline(payload: dict) -> dict:
    """SRS file -> ADR -> usage -> diagram in one call."""
    try:
        name, data = _read_request(payload)
    except ValueError as e:
        return _failed("request", str(e))
    # Each request gets its own scratch directory, removed afterwards; nothing persists between invocations.
    work = Path(tempfile.mkdtemp(prefix="arch-agent-"))
    try:
        srs = work / "input" / name
        srs.parent.mkdir(parents=True)
        srs.write_bytes(data)

        # Stage 1: SRS -> ADR (one model call, see adr_generator/generator.py)
        adr_out = work / "adr"
        argv = [str(srs), "--out", str(adr_out)]
        scope = parse_scope(payload)
        if scope:
            scope_file = work / "input" / "scope.json"
            scope_file.write_text(json.dumps(scope.model_dump(mode="json")), encoding="utf-8")
            argv += ["--scope", str(scope_file)]
        code, log = _run(adr_cli.main, argv)
        run_dir = _only_dir(adr_out / "runs")
        if code != 0 or run_dir is None or not (run_dir / "adr.md").is_file():
            return _failed("adr", "ADR generation failed", log)

        # Stage 2: design -> usage (one model call, see usage_estimator/estimator.py)
        usage = _usage_stage(run_dir)

        # Stage 3: design.json -> diagram (no model call); adr.md only if the design file is missing
        design_file = run_dir / "design.json"
        diagram = _diagram_stage(design_file if design_file.is_file() else run_dir / "adr.md", work / "diagram")
        scope_out = _json(run_dir / "scope.json")
        return {
            "run_id": run_dir.name,
            "adr": {"adr_md": (run_dir / "adr.md").read_text(encoding="utf-8"),
                    "spec_json": _json(run_dir / "spec.json"),
                    "design": _json(run_dir / "design.json")},
            "adr_metadata": _json(run_dir / "metadata.json"),
            "scope": scope_out,
            "scope_warnings": (scope_out or {}).get("violations") or [],
            "usage": usage,
        } | diagram
    finally:
        shutil.rmtree(work, ignore_errors=True)


ACTIONS = {
    "extract_srs": extract_srs,
    "generate_adr": generate_adr,
    "revise_adr": revise_adr,
    "estimate_usage": estimate_usage,
    "generate_diagram": generate_diagram,
    "run_pipeline": run_pipeline,
}


def dispatch(payload: dict) -> dict | Iterator[dict]:
    action = payload.get("action") or "run_pipeline"
    if action == "generate_diagram" and payload.get("stream"):
        return stream_diagram(payload)
    handler = ACTIONS.get(action)
    if handler is None:
        return _failed("request", f"Unknown action {action!r}; allowed: {', '.join(ACTIONS)}")
    return handler(payload)
