"""Step 6. Produce discovery.json, README.md and the Word document (converted from the README)."""
import json
import tempfile
from pathlib import Path

from strands import tool
from strands.types.tools import ToolContext

from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "render_outputs"
CONTEXT_FILE = "_context.json"


def read_context(state: RunState) -> dict | None:
    """The earlier agents' context the API left beside the documents (input/<engagement>/_context.json), if any."""
    key = f"{state.config.input_prefix}{CONTEXT_FILE}"
    if not state.storage.exists(key):
        return None
    try:
        context = json.loads(state.storage.read_bytes(key))
    except ValueError:
        return None
    return context if isinstance(context, dict) and context else None


def compute(state: RunState) -> dict:
    context = read_context(state)
    readme = state.stages.render_markdown(state.report, state.narrative, **({"context": context} if context else {}))
    with tempfile.TemporaryDirectory() as tmp:
        docx_path = Path(tmp) / "discovery_report.docx"
        state.stages.markdown_to_docx(readme, docx_path)
        docx_bytes = docx_path.read_bytes()
    files = {
        "discovery.json": json.dumps(state.report, indent=2, ensure_ascii=False).encode(),
        "README.md": readme.encode(),
        "discovery_report.docx": docx_bytes,
    }
    if context:
        files["context.json"] = json.dumps(context, indent=2, ensure_ascii=False).encode()
    if state.narrative:
        files["narrative.json"] = json.dumps(state.narrative, indent=2, ensure_ascii=False).encode()
    for name, data in files.items():
        state.write_work_file(name, data)
    return {"files": list(files)}


def apply(state: RunState, payload: dict) -> None:
    state.files = payload["files"]


def summarize(state: RunState) -> dict:
    return {"files": state.files}


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def render_outputs(tool_context: ToolContext) -> dict:
    """Step 6. Produce discovery.json, README.md and the Word document. Takes no arguments."""
    return run(tool_context, STEP)
