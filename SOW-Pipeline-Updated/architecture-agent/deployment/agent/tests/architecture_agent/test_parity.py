"""The Strands migration must not change any hard-coded model input: tool specs (names, descriptions, schemas),
prompt files, the diagram catalog, or the Settings defaults. The golden hashes were taken from the pre-migration
code (Architecture-Agent-MVP @ a30e0f5); a deliberate change to any of these must update golden_fingerprints.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import adr_generator.config as adr_config
import diagram_generator.config as diagram_config
from adr_generator import generator
from diagram_generator.llm import assumer, extractor, layout_coder

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((Path(__file__).parent / "golden_fingerprints.json").read_text())
SKIP = {"vendor_dir", "data_dir", "prompts_dir"}


def _h(x) -> str:
    return hashlib.sha256(x if isinstance(x, bytes) else x.encode()).hexdigest()


def _j(o) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


def _files(pkg: str, pattern: str, label: str) -> dict:
    base = ROOT / pkg
    return {f"{label}:{p.relative_to(base / ('prompts' if label == 'prompt' else 'catalog'))}": _h(p.read_bytes())
            for p in sorted(base.rglob(pattern)) if (label == "prompt") == ("prompts" in p.parts)}


def _settings(cfg) -> str:
    return _h(_j({k: str(v) for k, v in cfg.Settings(_env_file=None).model_dump().items() if k not in SKIP}))


def test_adr_generator_unchanged():
    got = {"spec:submit_design": _h(_j(generator.build_tool_spec())), "settings": _settings(adr_config)}
    got |= _files("adr_generator", "*.md", "prompt")
    assert got == GOLDEN["adr_generator"]


def test_diagram_generator_unchanged():
    got = {f"spec:{n}": _h(_j(m.build_tool_spec())) for n, m in
           (("extractor", extractor), ("assumer", assumer), ("layout_coder", layout_coder))}
    got["settings"] = _settings(diagram_config)
    got |= _files("diagram_generator", "*.md", "prompt")
    got |= {f"catalog:{p.name}": _h(p.read_bytes()) for p in sorted((ROOT / "diagram_generator" / "catalog").glob("*.yaml"))}
    assert got == GOLDEN["diagram_generator"]
