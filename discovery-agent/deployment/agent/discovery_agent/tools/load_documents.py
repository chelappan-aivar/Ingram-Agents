"""Step 1. Load and classify every input document for the run, and build the corpus."""
import tempfile
from pathlib import Path

from strands import tool
from strands.types.tools import ToolContext

from ..data.documents import Document, load_documents as load_document_files
from ..runtime.state import RunState
from .runner import StepSpec, register, run

STEP = "load_documents"
DOC_SUFFIXES = {"", ".txt", ".md", ".eml", ".docx", ".pdf"}  # anything else in the input folder is ignored


def _is_input_document(key: str) -> bool:
    name = key.rsplit("/", 1)[-1]
    return not name.startswith(("_", ".")) and Path(name).suffix.lower() in DOC_SUFFIXES  # '_run' marker etc. skipped


def compute(state: RunState) -> dict:
    keys = [k for k in state.storage.list_keys(state.config.input_prefix) if _is_input_document(k)]
    if not keys:
        raise ValueError("no input documents found under the input prefix")
    names = [k.rsplit("/", 1)[-1] for k in keys]
    if len(set(names)) != len(names):
        raise ValueError("input documents must have unique file names")
    with tempfile.TemporaryDirectory() as tmp:  # downloaded files are untrusted: parsed only, never executed
        paths = []
        for key, name in zip(keys, names):
            path = Path(tmp) / name
            path.write_bytes(state.storage.read_bytes(key))
            paths.append(path)
        docs = load_document_files(paths)
    _, docs = state.stages.prepare_corpus(docs)  # trims small talk only when the corpus is over budget
    return {"documents": [{"id": d.id, "kind": d.kind, "label": d.label, "text": d.text,
                           "speakers": sorted(d.speakers), "kept_fraction": d.kept_fraction} for d in docs]}


def apply(state: RunState, payload: dict) -> None:
    state.docs = [Document(d["id"], d["kind"], d["text"], set(d["speakers"]), d["label"], d.get("kept_fraction", 1.0))
                  for d in payload["documents"]]
    state.corpus = state.stages.build_corpus(state.docs)  # already trimmed if needed; raises CorpusTooLarge otherwise


def summarize(state: RunState) -> dict:
    out = {"documents": [{"id": d.id, "kind": d.kind, "label": d.label} for d in state.docs],
           "est_tokens": int(len(state.corpus) / 3.5)}
    trimmed = {d.id: d.kept_fraction for d in state.docs if d.kept_fraction < 1}
    if trimmed:
        out["trimmed_to_fraction"] = trimmed
    return out


register(StepSpec(STEP, compute, apply, summarize))


@tool(context=True)
def load_documents(tool_context: ToolContext) -> dict:
    """Step 1. Load and classify every input document for this run. Takes no arguments. Call this first."""
    return run(tool_context, STEP)
