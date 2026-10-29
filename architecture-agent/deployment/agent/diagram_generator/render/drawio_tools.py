"""draw.io toolchain: vendored fixers, vendored validator, CLI export, light-mode SVG.

Every function here is failure-tolerant: tool problems are returned as data (ok=False,
errors=[...]) and never raised, so the pipeline can log them and retry.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MACOS_APP_CLI = "/Applications/draw.io.app/Contents/MacOS/draw.io"
EXPORT_BORDER = 10
PNG_SCALE = 2
# draw.io desktop's PNG renderer silently cuts off the bottom of very large images (a 36-box diagram at scale 2,
# 8180x4613 px, was blank below 76% of its height; 21 Mpx rendered fully). Large pages get a smaller scale.
# In the AgentCore container (no GPU, Xvfb) the export fails above ~6 Mpx, so the image sets PNG_MAX_PIXELS lower.
PNG_MAX_PIXELS = int(os.environ.get("PNG_MAX_PIXELS") or 20_000_000)
_PAGE_RE = re.compile(r'pageWidth="(\d+(?:\.\d+)?)"\s+pageHeight="(\d+(?:\.\d+)?)"')


def png_scale(drawio_path: Path) -> float:
    """PNG_SCALE, reduced so the exported image stays under PNG_MAX_PIXELS."""
    try:
        m = _PAGE_RE.search(Path(drawio_path).read_text(encoding="utf-8", errors="replace")[:4000])
    except OSError:
        m = None
    if not m:
        return PNG_SCALE
    w, h = float(m.group(1)) + 2 * EXPORT_BORDER, float(m.group(2)) + 2 * EXPORT_BORDER
    fit = (PNG_MAX_PIXELS / max(1.0, w * h)) ** 0.5
    return round(max(0.5, min(PNG_SCALE, fit)), 2)
LIGHT_STYLE = "color-scheme: light; background-color: #ffffff"


@dataclass
class ToolResult:
    ok: bool
    output: str


@dataclass
class ValidationOutcome:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class ExportResult:
    png: Optional[Path]
    svg: Optional[Path]
    errors: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- subprocess helper
def _run(cmd: list[str], timeout: float, env: Optional[dict] = None) -> tuple[Optional[int], str]:
    """Run a command; returns (exit_code, merged output). exit_code None = could not run / timeout."""
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, env=env, text=True, errors="replace",
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, f"{out}\nTIMEOUT after {timeout}s"
    except OSError as e:
        return None, f"cannot run {cmd[0]}: {e}"


# The vendored fixers do `import defusedxml.ElementTree as ET` and use ET.Element / ET.ElementTree / ET.indent
# at import time. defusedxml 0.7.1 does not export them (newer releases do), so the scripts crash on import.
# We cannot edit vendor/, so run them through this tiny launcher that fills the missing names from the stdlib
# (only when absent; a no-op on a newer defusedxml). The parsing itself stays defusedxml (safe).
_FIXER_LAUNCHER = (
    "import sys, runpy, defusedxml.ElementTree as D; import xml.etree.ElementTree as S\n"
    "for n in ('Element', 'ElementTree', 'indent', 'SubElement'):\n"
    "    if not hasattr(D, n): setattr(D, n, getattr(S, n))\n"
    "script = sys.argv[1]; sys.argv = sys.argv[1:]\n"
    "runpy.run_path(script, run_name='__main__')\n"
)


def _script(settings, name: str) -> Path:
    return Path(settings.plugin_lib_dir) / name


# ----------------------------------------------------------------------------- fixers / validator
def post_process(drawio_path: Path, settings) -> ToolResult:
    """Run the vendored post_process_drawio.py on the file, in place."""
    script = _script(settings, "post_process_drawio.py")
    if not script.exists():
        return ToolResult(False, f"missing vendored script: {script}")
    code, out = _run([sys.executable, "-I", "-c", _FIXER_LAUNCHER, str(script), str(drawio_path)],
                     settings.tool_timeout_sec)
    ok = code == 0 and "internal error" not in out and "Error parsing file" not in out
    return ToolResult(ok, out.strip())


def validate(drawio_path: Path, settings) -> ValidationOutcome:
    """Run the vendored validate_drawio.py; parse its ERROR:/INFO: lines."""
    script = _script(settings, "validate_drawio.py")
    if not script.exists():
        msg = f"missing vendored script: {script}"
        return ValidationOutcome(False, [msg], [], msg)
    code, out = _run([sys.executable, "-I", str(script), str(drawio_path)], settings.tool_timeout_sec)
    errors: list[str] = []
    warnings: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("ERROR:"):
            errors.append(s[len("ERROR:"):].strip())
        elif s.startswith("INFO:"):
            msg = s[len("INFO:"):].strip()
            if msg.startswith("AWS4 shapes used:"):      # summary line, not a warning (stays in raw)
                continue
            warnings.append(msg)
    if "internal error" in out:
        errors.append("validator internal error")
    if code is None:
        errors.append(out.strip().splitlines()[-1] if out.strip() else "validator did not run")
    elif code != 0 and not errors:
        errors.append(f"validator exited with code {code}")
    return ValidationOutcome(ok=(code == 0 and not errors), errors=errors, warnings=warnings, raw=out)


# ----------------------------------------------------------------------------- export
def find_drawio_cli(settings) -> Optional[str]:
    """settings.drawio_cli, then PATH, then the macOS app bundle."""
    configured = getattr(settings, "drawio_cli", None)
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        found = shutil.which(configured)
        if found:
            return found
    found = shutil.which("drawio")
    if found:
        return found
    if os.path.isfile(MACOS_APP_CLI) and os.access(MACOS_APP_CLI, os.X_OK):
        return MACOS_APP_CLI
    return None


def _export_one(cli: str, src: Path, dest: Path, fmt: str, timeout: float, env: dict) -> Optional[str]:
    """Export one format to dest. Returns an error string or None on success."""
    cmd = [cli, "-x", "-f", fmt, "-b", str(EXPORT_BORDER)]
    if fmt == "png":
        cmd += ["-s", str(png_scale(src))]
    cmd += ["-o", str(dest), str(src)]
    if dest.exists():
        dest.unlink()
    code, out = _run(cmd, timeout, env)
    tail = out.strip()[-500:]
    if code is None:
        return f"{fmt} export failed: {tail}"
    if not dest.exists() or dest.stat().st_size == 0:
        return f"{fmt} export produced no output (exit {code}): {tail}"
    if code != 0:
        return f"{fmt} export exit code {code}: {tail}"
    return None


def export(drawio_path: Path, out_dir: Path, settings) -> ExportResult:
    """Export diagram.png (scale 2) and diagram.svg (no -e embed) into out_dir."""
    errors: list[str] = []
    cli = find_drawio_cli(settings)
    if not cli:
        return ExportResult(None, None, ["draw.io CLI not found (set DRAWIO_CLI or install draw.io desktop)"])
    drawio_path = Path(drawio_path).resolve()
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return ExportResult(None, None, [f"cannot create output dir: {e}"])
    if not drawio_path.exists():
        return ExportResult(None, None, [f"input not found: {drawio_path}"])

    png_path, svg_path = out_dir / "diagram.png", out_dir / "diagram.svg"
    timeout = settings.export_timeout_sec
    png: Optional[Path] = None
    svg: Optional[Path] = None

    # Electron wants a writable HOME/TMPDIR; give each call its own scratch so parallel runs never share state.
    with tempfile.TemporaryDirectory(prefix="drawio_export_") as scratch:
        scratch_p = Path(scratch)
        work_in = scratch_p / "in.drawio"
        try:
            shutil.copyfile(drawio_path, work_in)
        except OSError as e:
            return ExportResult(None, None, [f"cannot copy input: {e}"])
        env = dict(os.environ)
        env["TMPDIR"] = str(scratch_p)
        if not os.access(env.get("HOME", ""), os.W_OK):
            env["HOME"] = str(scratch_p)
        for fmt, dest in (("png", png_path), ("svg", svg_path)):
            err = _export_one(cli, work_in, dest, fmt, timeout, env)
            if err:
                errors.append(err)
            elif fmt == "png":
                png = dest
            else:
                svg = dest

    # Output file existence + non-empty is the truth; a non-zero exit with a good file is only a warning-level quirk,
    # but _export_one reports it, so drop errors whose file is fine.
    if png and png.exists() and png.stat().st_size > 0:
        errors = [e for e in errors if not e.startswith("png export exit code")]
    if svg and svg.exists() and svg.stat().st_size > 0:
        errors = [e for e in errors if not e.startswith("svg export exit code")]
    if svg is None and svg_path.exists() and svg_path.stat().st_size > 0 and any(e.startswith("svg export exit code") for e in errors):
        svg = svg_path

    if svg is not None and getattr(settings, "svg_force_light", True):
        try:
            force_light_svg(svg)
        except Exception as e:  # never raise from export
            errors.append(f"force_light_svg failed: {e}")
    return ExportResult(png=png, svg=svg, errors=errors)


# ----------------------------------------------------------------------------- light-mode SVG
_SVG_TAG_RE = re.compile(r"<svg\b[^>]*>", re.DOTALL)
_STYLE_ATTR_RE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.DOTALL)
_DROP_DECL_RE = re.compile(r"^\s*(color-scheme|background-color|background)\s*:", re.IGNORECASE)


def force_light_svg(svg_path: Path) -> None:
    """Force light mode on the root <svg>: color-scheme: light + white background.

    Only the root start tag is edited; existing color-scheme/background declarations are replaced
    (CSS last-wins would work too, but one clean declaration is easier to read), every other byte is kept.
    """
    path = Path(svg_path)
    data = path.read_bytes()
    text = data.decode("utf-8")
    m = _SVG_TAG_RE.search(text)
    if not m:
        raise ValueError(f"no <svg> root element in {path}")
    tag = m.group(0)
    sm = _STYLE_ATTR_RE.search(tag)
    if sm:
        kept = [d.strip() for d in sm.group(3).split(";")
                if d.strip() and not _DROP_DECL_RE.match(d)]
        style = "; ".join(kept + [LIGHT_STYLE]) + ";"
        new_tag = tag[:sm.start()] + f'{sm.group(1)}{sm.group(2)}{style}{sm.group(2)}' + tag[sm.end():]
    else:
        insert_at = len(tag) - (2 if tag.endswith("/>") else 1)
        new_tag = tag[:insert_at].rstrip() + f' style="{LIGHT_STYLE};"' + tag[insert_at:]
    new_text = text[:m.start()] + new_tag + text[m.end():]
    path.write_bytes(new_text.encode("utf-8"))
