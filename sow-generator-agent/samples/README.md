# SOW Generator: sample inputs and outputs

Three fictional engagements to test or demo the SOW generator, in the web UI
(https://main.d2ql2k4kqtbcpv.amplifyapp.com) or from the command line. All companies, people and e-mail addresses
are made up (`example.com`).

| Folder | Template | Inputs | What it shows |
|---|---|---|---|
| `01-aiml-fabrikam-insurance/` | AI/ML | details, call transcript (.docx), process notes (.pdf), diagram (.png), upstream JSON | Generative AI claims assistant (FNOL, Bedrock, RAG, Textract). Full upstream data: all 5 rules pass |
| `02-devops-tailspin-toys/` | DevOps | details, call transcript (.txt), application inventory (.xlsx), diagram (.png), upstream JSON | CI/CD, EKS, observability for an e-commerce site. Same customer as the Intake agent test data |
| `03-quick-test-northwind-traders/` | AI/ML | project details only | Fastest run (about 1 minute); rules that need upstream data show "n/a" |

Each folder has an `output/` with what the deployed agent produced on 25 October 2026: `sow.md` (draft),
`check.json` (rules and AI review), `<Customer>_SOW.docx` and `run.json` (timings, tokens).

| Sample | Draft | Time | Rules | AI review issues |
|---|---|---|---|---|
| 01 Fabrikam Insurance | 32k characters | 116 s | 5 / 5 pass | 8 (4 high) |
| 02 Tailspin Toys | 29k characters | 92 s | 5 / 5 pass | 8 (4 high) |
| 03 Northwind Traders | 21k characters | 61 s | 2 pass, 3 n/a | not requested |

Outputs differ from run to run (the model is not deterministic).

## Use a sample in the web UI

1. **Customer name** and **Opportunity id**: from the table below.
2. **Template**: as listed.
3. **Project details**: paste the contents of `project-details.txt`.
4. **Transcripts and notes**: add the `.docx` / `.pdf` / `.txt` / `.xlsx` files from the folder.
5. **Architecture images**: add `architecture-diagram.png`.
6. **Upstream data** (optional panel): paste the contents of `upstream.json`. This turns on the pricing, service
   coverage and timeline cross-checks.
7. **Generate SOW**, then **Check draft** (with AI review), try **Fix with AI** on an issue, and **Download .docx**.

| Sample | Customer name | Opportunity id | Template |
|---|---|---|---|
| 01 | Fabrikam Insurance | OPP-2026-0517 | AI/ML — Ai Ml |
| 02 | Tailspin Toys | OPP-2026-0498 | DevOps — Devops Standard |
| 03 | Northwind Traders | (none) | AI/ML — Ai Ml |

Good editor instructions to try: "Add a 2-week hypercare phase after go-live", "Add a Risks section with mitigations",
"Change the delivery timezone to US Eastern time".

## Command line

```bash
cd samples
python3 run_sample.py 01-aiml-fabrikam-insurance --ai-review
python3 run_sample.py 02-devops-tailspin-toys --ai-review
python3 run_sample.py 03-quick-test-northwind-traders --customer "Northwind Traders" --template aiml/ai-ml
```

`run_sample.py` makes the same API calls as the UI (session → generation stream → check → .docx export) and
overwrites `output/`. Standard library only. Each run costs Bedrock tokens; `--ai-review` adds one more model call.
`--no-upstream` sends the files without `upstream.json`.

## Rebuild the inputs

The input files are generated from the text in `build_samples.py` (edit there, not in the files):

```bash
pip install python-docx openpyxl reportlab pillow jsonschema
python3 build_samples.py
```

It also validates each `upstream.json` against `../resources/contracts/upstream_inputs.v1.schema.json` and checks
that the cost line items add up to `monthly_total` and that `annual_total` = 12 × monthly.
