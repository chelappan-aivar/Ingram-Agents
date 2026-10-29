"""Render stored minutes (see ``app/minutes.py``) as Markdown with ``[#seq]`` citations."""

from __future__ import annotations

import re
from typing import Any

_SECTIONS = (
    ("key_points", "Key points"),
    ("decisions", "Decisions"),
    ("action_items", "Action items"),
    ("risks", "Risks and concerns"),
    ("open_questions", "Open questions"),
    ("next_steps", "Next steps"),
)


def minutes_markdown(view: dict[str, Any]) -> str:
    """Markdown for a call view whose minutes are ready."""
    minutes = view["minutes"].get("minutes") or {}
    out = [f"# Minutes: {_md(view.get('title') or 'Call')}", ""]
    out.append(f"- Call: `{view['session_id']}`")
    if view.get("created_at"):
        out.append(f"- Started: {view['created_at']} (UTC)")
    if view.get("ended_at"):
        out.append(f"- Ended: {view['ended_at']} (UTC)")
    if view["minutes"].get("generated_at"):
        out.append(f"- Minutes generated: {view['minutes']['generated_at']} (UTC)")
    attendees = [
        _md(a.get("name") or "") + f" ({'Partner BA' if a['speaker'] == 'ba' else 'Customer'})"
        for a in minutes.get("attendees", [])
    ]
    if attendees:
        out.append("- Attendees: " + ", ".join(a.strip() for a in attendees))
    paragraphs = [p for p in (minutes.get("summary") or "").split("\n") if p.strip()]
    out += ["", "## Summary", "", "\n\n".join(_md(p) for p in paragraphs)]

    for key, title in _SECTIONS:
        items = minutes.get(key) or []
        if not items:
            continue
        out += ["", f"## {title}", ""]
        for item in items:
            extra = []
            if item.get("owner"):
                extra.append(f"owner: {_md(item['owner'])}")
            if item.get("due"):
                extra.append(f"due: {_md(item['due'])}")
            line = f"- {_md(item['text'])}" + (f" ({'; '.join(extra)})" if extra else "")
            refs = " ".join(f"[#{s}]" for s in item.get("citations", []))
            out.append(f"{line} {refs}".rstrip() if refs else f"{line} _(no citation)_")
    return "\n".join(out) + "\n"


def _md(text: str) -> str:
    """Single line, with Markdown control characters escaped (model output is untrusted)."""
    text = " ".join(str(text).split())
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", text)
