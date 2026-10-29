# `discovery_agent/rendering/` — report JSON → Markdown → Word

Turns the validated report JSON into the human-facing deliverables. No analysis happens here — only formatting.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package. |
| `readme.py` | Renders the report JSON into `README.md` — the standard discovery document (executive summary, ranked use cases, hidden opportunities, open questions), with every claim citing a verbatim quote. |
| `word.py` | Converts that Markdown into `discovery_report.docx` (python-docx) for sharing with reviewers. |
