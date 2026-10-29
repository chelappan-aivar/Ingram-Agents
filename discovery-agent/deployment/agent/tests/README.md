# `tests/` — offline unit tests

All tests run **without AWS and without calling Bedrock** — model calls, S3, and the AgentCore SDK are stubbed. Run the
whole suite from `deployment/agent`:

```bash
uv run python -m unittest discover -s tests -t .      # or any Python 3.12 venv with requirements.txt installed
```

## Files

| File | Covers |
|---|---|
| `__init__.py` | Marks the package. |
| `helpers.py` | Shared fixtures and stubs (fake corpus, fake model stages, fake storage) used across the pipeline tests. |
| `test_assemble.py` | Report assembly: merge, evidence-checking, de-dup, ranking. |
| `test_assemble_regressions.py` | Specific past bugs in assembly, pinned so they can't come back. |
| `test_validate_and_stages.py` | Schema validation + the `Stages` wiring. |
| `test_trim.py` | Small-talk trimming and `kept_fraction`. |
| `test_render.py` | Report JSON → Markdown rendering. |
| `test_storage.py` | `LocalStorage`/`S3Storage` behaviour, including key-safety checks. |
| `test_state_tools_orchestrator.py` | Run state, step ordering/prerequisites, checkpoints, and the orchestrator sequence. |
| `test_main.py` | The AgentCore entry point (`main.py`): action routing (start, resume_with_answers, ping), payload validation, background start, double-start guard. |
| `test_api_handler.py` | The API Lambda (`../api/handler.py`): every route, upload validation, presigned URLs (regional virtual host, signed size + SSE), cost guards, run history, clarification round, body-size guard; S3, DynamoDB and the runtime client faked. |
