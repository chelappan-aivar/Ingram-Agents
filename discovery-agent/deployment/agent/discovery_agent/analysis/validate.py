import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from .transcript import normalize

# schema/ sits next to the discovery_agent package: deployment/agent/schema locally, the zip root in the runtime
# (scripts/package_agent.sh copies both).
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "report.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
_validator = Draft202012Validator(SCHEMA)

# Numbers (metrics, volumes, durations, costs) are valuable context, but only when the customers' own words
# support them. A number in analysis text must appear in that item's cited quotes; otherwise it is an estimate.
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ID = re.compile(r"^(UC|H|Q)-\d+$")
_ID_TOKEN = re.compile(r"\b(?:UC|H|Q)-\d+\b")  # a report id mentioned inside a sentence ("see UC-3") is not a figure
_CLIENTS = re.compile(r"\bclients?\b", re.I)


def quote_is_verbatim(quote: str, body: str) -> bool:
    """True when every '...'-separated part of `quote` occurs in `body` (an already-normalised document text)."""
    parts = [p for p in re.split(r"\.{3}|…", str(quote)) if normalize(p)]
    return bool(parts) and all(normalize(p) in body for p in parts)


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(_ID_TOKEN.sub(" ", text))}


def _walk(node, path=""):
    """Yield (path, string) for every string outside evidence blocks."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "evidence":
                continue
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}/{i}")
    elif isinstance(node, str):
        yield path, node


def _evidence(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "evidence" and isinstance(v, list):
                for i, e in enumerate(v):
                    if isinstance(e, dict):
                        yield f"{path}/evidence/{i}", e
            else:
                yield from _evidence(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _evidence(v, f"{path}/{i}")


def _systems(node, path=""):
    """Yield (path, name) for every entry of every `systems_involved` list in the output."""
    if isinstance(node, dict):
        for name in node.get("systems_involved") or []:
            yield path, name
        for k, v in node.items():
            if k != "evidence":
                yield from _systems(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _systems(v, f"{path}/{i}")


_nums = numbers_in


def quoted_numbers(report: dict) -> set[str]:
    """Every number that appears in a quote anywhere in the report: the figures a narrative may repeat."""
    return _nums(" ".join(str(e.get("quote", "")) for _, e in _evidence(report)))


def _check_numbers(node, allowed: set[str], path="") -> list[str]:
    """Numbers in analysis text must appear in the cited quotes of the nearest enclosing item.

    `allowed` is inherited from the parent; an item with its own evidence narrows it to its own quotes.
    """
    errors = []
    if isinstance(node, dict):
        if isinstance(node.get("evidence"), list):
            allowed = _nums(" ".join(str(e.get("quote", "")) for e in node["evidence"] if isinstance(e, dict)))
        for k, v in node.items():
            if k != "evidence":
                errors += _check_numbers(v, allowed, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            errors += _check_numbers(v, allowed, f"{path}/{i}")
    elif (isinstance(node, str) and not _ID.match(node) and not path.endswith(("/id", "/use_case_id", "/source"))
          and "/systems_involved/" not in path
          and "/resolution" not in path):  # reviewer-authored answers/stands are not model claims
        bad = _nums(node) - allowed
        if bad:
            errors.append(f"number(s) {sorted(bad)} at {path} are not in the cited quotes; remove them or cite "
                          "a quote that contains them (do not estimate)")
    return errors


def check_content(data: dict, docs: dict) -> list[str]:
    """Evidence and wording checks that apply to any pass output. docs maps document id -> Document."""
    errors = []
    bodies = {i: normalize(d.text) for i, d in docs.items()}
    for path, ev in _evidence(data):
        src = ev["source"] if isinstance(ev.get("source"), str) else None
        if src not in docs:
            errors.append(f"unknown source {src!r} at {path}; use one of {sorted(docs)}")
            continue
        if not quote_is_verbatim(ev.get("quote", ""), bodies[src]):
            errors.append(f"quote not found verbatim in {src} at {path}: {str(ev.get('quote'))[:80]!r}")
        spk = ev.get("speaker")
        if spk and docs[src].speakers and spk not in docs[src].speakers:
            errors.append(f"speaker {spk!r} not in {src} at {path}")
    for path, name in _systems(data):  # a system the customers never named is an invention
        if not (isinstance(name, str) and normalize(name) and any(normalize(name) in b for b in bodies.values())):
            errors.append(f"system {str(name)[:60]!r} at {path} does not appear in any source document")
    errors += _check_numbers(data, _nums(" ".join(str(e.get("quote", "")) for _, e in _evidence(data))))
    for path, s in _walk(data):
        if _CLIENTS.search(s):
            errors.append(f"use 'customers', not 'clients', at {path}")
    return errors[:25]


def check_wording(data: dict, reference: dict) -> list[str]:
    """Wording/number checks for outputs with no evidence of their own (the narrative). Numbers must come from
    the quotes in `reference` (the validated report)."""
    errors = _check_numbers(data, quoted_numbers(reference))
    return errors + [f"use 'customers', not 'clients', at {p}" for p, s in _walk(data) if _CLIENTS.search(s)]


def validate_report(report: dict, docs: dict) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors = [f"schema: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in _validator.iter_errors(report)]
    if errors:
        return errors[:25]  # structural problems first; the checks below assume a valid shape
    errors = check_content({k: v for k, v in report.items() if k not in ("meta", "schema_version")}, docs)

    ucs = report["use_cases"]
    if sorted(u["rank"] for u in ucs) != list(range(1, len(ucs) + 1)):
        errors.append("use_cases ranks must be unique consecutive integers starting at 1")
    if len({u["id"] for u in ucs}) != len(ucs):
        errors.append("use_cases ids must be unique")
    hidden = {u["id"] for u in ucs if u["category"] != "EXPLICIT"}
    for t in report["top_hidden_opportunities"]:
        if t["use_case_id"] not in hidden:
            errors.append(f"top_hidden_opportunities references non-hidden or unknown use case {t['use_case_id']!r}")
    return errors[:25]
