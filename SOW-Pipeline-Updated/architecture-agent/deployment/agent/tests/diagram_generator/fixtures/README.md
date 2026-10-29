# Architecture Agent - test fixtures

Mock input documents (all companies and systems fictional) plus machine-readable expectations.
Copied from MVP v1; `multi_a_ingest.md` + `multi_b_analytics.md` are a two-file pair (one fictional architecture split across both) for multi-file tests. `coder_sample_spec.json` belongs to WP3.

| File | Format | What it tests | Expected result |
|---|---|---|---|
| `a_clean.md` | Markdown | Explicit, well-structured architecture (voice AI platform: VPC, 3 AZs, public/private subnets, ECS on EC2, external LiveKit/Deepgram, Bedrock, Qdrant, DocumentDB, security services), numbered flow, components table. Baseline happy path, boundaries and nesting. | COMPLETED, all components, flow steps 1-11, no unclear items |
| `b_prose.pdf` | Text PDF (3 pages) | Architecture spread through prose paragraphs among non-architecture sections (summary, scope, timeline, commercial assumptions). Serverless e-commerce order processing with an external payment provider (PayFlow). Tests PDF text extraction plus finding architecture in noise. | COMPLETED, serverless components, no invented networking |
| `c_tables.docx` | DOCX | Architecture mostly in two tables (component inventory: Component / AWS Service / Placement / Purpose; flow table: From / To / Protocol / Notes). Includes an on-prem corporate data center, Site-to-Site VPN, DMS, S3 data lake, Glue, Athena, Redshift Serverless, QuickSight, Lake Formation, VPC endpoints. Tests table extraction. | COMPLETED, `on_prem` boundary, flow from the table rows |
| `d_vague.md` | Markdown | Deliberately vague "final" design: "a database", "some queue", "runs in containers", unnamed CRM, no region or subnets. Tests that gaps go to `unclear_items`/`assumptions` and no concrete service is invented. | COMPLETED with >= 3 unclear items; none of the `must_not_have_components` |
| `e_scanned.pdf` | Image-only PDF | A rasterised page with no text layer. Tests the scanned-PDF guard. | FAILED with "scanned/image-only PDFs are not supported yet" |
| `expectations.json` | JSON | Per-fixture expectations: `expect_status`, `expect_error_contains`, `must_have_components`, `must_have_connections`, `must_not_have_components`, `expect_unclear_items_min`, `boundaries_expected`. Names are meant for case-insensitive substring matching against component `name`, `aws_service` or `id`. | n/a |
| `_generate.py` | Python | Regenerates `b_prose.pdf`, `c_tables.docx`, `e_scanned.pdf`. | n/a |
| `_check.py` | Python | Verifies parsing, that every `must_have` item exists in its source text, that no `must_not_have` item does, and that `e_scanned.pdf` has no text layer. | prints `ALL CHECKS PASSED` |

`a_clean.md` and `d_vague.md` are hand-written; the generated binaries are committed.

## Regenerate and verify

```
cd tests/diagram_generator/fixtures
uv run --python 3.12 --with reportlab --with python-docx --with pillow python _generate.py
uv run --python 3.12 --with pypdf --with python-docx python _check.py
```

## Notes

- `d_vague.md` is expected to complete (it does contain some architecture) rather than fail. If the pipeline decides to fail it with a "no architecture found" style message, that is a judgement call to review, not necessarily a bug.
- `must_have_connections` pairs are the direction stated in the documents; a test may reasonably accept either direction for bidirectional flows.
- Documents use the word "customer" for the business party.
