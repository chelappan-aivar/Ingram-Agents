import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path

from ..analysis.transcript import clean_transcript, speakers

_TS = re.compile(r"^\d+:\d{2}(:\d{2})?$", re.M)
_CHAT = re.compile(r"^\[?\d{1,2}[/:.-]\d{1,2}.*?\]?\s*[^:\n]{2,40}:\s", re.M)

# ── PII redaction (applied to email bodies before the model ever sees them) ────────────────────────────────────
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# phone numbers: +cc, separators, 7+ digits total (kept conservative to avoid eating ordinary numbers)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_URL_RE = re.compile(r"\bhttps?://\S+\b")


def _display_name(address_header: str | None) -> list[str]:
    """Return the human display names from a From/To header, discarding every email address.

    'Bo Chen <bo@acme.com>, ops@acme.com' -> ['Bo Chen'].  An address with no display name is dropped entirely
    (we expose names only, never contact details)."""
    if not address_header:
        return []
    names = []
    for display, addr in getaddresses([address_header]):
        name = (display or "").strip().strip('"')
        if not name and addr:
            # fall back to the local-part only if there is no display name AND it is clearly a person-like token;
            # otherwise drop it, to avoid leaking the address.
            continue
        if name:
            names.append(name)
    return names


def redact_pii(text: str) -> str:
    """Replace email addresses, phone numbers and URLs with neutral placeholders. Names and content are preserved."""
    text = _EMAIL_RE.sub("[email]", text)
    text = _URL_RE.sub("[link]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


@dataclass
class Document:
    id: str
    kind: str  # transcript | email | chat | document
    text: str
    speakers: set[str] = field(default_factory=set)
    label: str = ""  # human-readable source name used in reports, e.g. 'Sep 22 · Bill, Cost & FinOps'
    kept_fraction: float = 1.0  # below 1.0 when small talk was trimmed away before analysis


def _read(path: Path) -> tuple[str, str | None]:
    """Return (text, forced_kind) for a file of any supported type."""
    suffix = path.suffix.lower()
    if suffix == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        body = msg.get_body(preferencelist=("plain", "html"))
        body_text = redact_pii(body.get_content() if body else "")
        # Expose only human NAMES (sender + recipients) and the subject — never email addresses, Cc, or Date headers.
        from_names = _display_name(msg["From"])
        to_names = _display_name(msg["To"])
        lines = []
        if from_names:
            lines.append("From: " + ", ".join(from_names))
        if to_names:
            lines.append("To: " + ", ".join(to_names))
        if msg["Subject"]:
            lines.append("Subject: " + str(msg["Subject"]))
        head = "\n".join(lines)
        return f"{head}\n\n{body_text}".strip(), "email"
    if suffix == ".docx":
        from docx import Document as Docx

        d = Docx(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
        return "\n".join(parts), "document"
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages), "document"
    return path.read_text(encoding="utf-8", errors="replace"), None


def _label(raw: str, fallback: str) -> str:
    """'Sep 22' + 'Re: [EXTERNAL]Re: Title' header lines -> 'Sep 22 · Title'; otherwise the file name."""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()][:3]
    if len(lines) >= 2 and re.match(r"^[A-Z][a-z]{2} \d{1,2}$", lines[0]):
        title = re.sub(r"^(?:re:\s*|\[external\]\s*)+", "", lines[1], flags=re.I).strip()
        return f"{lines[0]} \u00b7 {title}"
    return fallback


def load_document(path: Path) -> Document:
    text, kind = _read(path)
    label = _label(text, path.name)
    if kind is None:
        if len(_TS.findall(text)) >= 10:
            kind = "transcript"
        elif re.match(r"\s*(From|Subject|To):", text, re.I):
            kind = "email"
        elif len(_CHAT.findall(text)) >= 10:
            kind = "chat"
        else:
            kind = "document"
    if kind == "transcript":
        text = clean_transcript(text)
    return Document(id=path.name, kind=kind, text=text.strip(), speakers=speakers(text) if kind == "transcript" else set(), label=label)


def load_documents(paths: list[Path]) -> list[Document]:
    docs = [load_document(p) for p in paths]
    ids = [d.id for d in docs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate document names: {ids}")
    return docs
