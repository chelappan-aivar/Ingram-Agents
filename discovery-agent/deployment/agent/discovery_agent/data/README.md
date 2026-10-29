# `discovery_agent/data/` — input documents and storage

The two edges where the agent touches the outside world: reading the input documents, and reading/writing bytes to
either local disk (development) or S3 (the runtime).

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Marks the package. |
| `documents.py` | Reads and **classifies** every input file. Handles `.txt/.md` (plain), `.eml` (email headers + body), `.docx` (paragraphs + table cells), and `.pdf` (text extraction via pypdf). Sniffs plain-text files into `transcript` / `email` / `chat` / `document`, derives a human-readable label, and extracts transcript speakers. |
| `storage.py` | One small `Storage` interface over two backends: `LocalStorage` (a folder, for dev/tests) and `S3Storage` (SSE-KMS writes, for the runtime). `default_storage()` picks the backend automatically — S3 in the runtime, local folder on a dev machine. All keys are validated to block path traversal. |
