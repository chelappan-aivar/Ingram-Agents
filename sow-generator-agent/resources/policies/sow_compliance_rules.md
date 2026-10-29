# SOW Draft Compliance Rule Catalog

One row per rule. These are **Layer 1 (deterministic)** rules evaluated against a
generated SOW *draft* after generation, defined in `sow_compliance_rules.yaml`
and executed by `app/services/sow_rules.py`. They are separate from the intake
completeness policies in `company_policies.yaml`.

Only these deterministic rules set legal pass/fail. The optional LLM verifier
(`prompts/verification.txt`, wired via `verify_draft_async`) contributes advisory
consistency issues only and never flips a rule result.

| id | name | severity | evaluator | evidence format |
|----|------|----------|-----------|-----------------|
| `scope-section-present` | Scope section present | required | `section_present` | matched heading text |
| `pricing-section-present` | Pricing section present | required | `section_present` | matched heading text |
| `pricing-refers-to-cost-estimate` | Pricing refers to the cost estimate | required | `upstream_link_in_sow` | calculator link found/missing, plus any restated run-rate figure or table found in the pricing section |
| `services-covered` | Architecture services covered in scope | advisory | `services_coverage` | per-service covered/missing list |
| `timeline-consistent` | SOW timeline matches intake | advisory | `timeline_match` | intake vs draft durations (normalised to weeks) |

## Status semantics

- `pass` — the rule was evaluated and satisfied.
- `fail` — the rule was evaluated and violated. Required-rule failures surface a
  blocking banner in the UI (export is not hard-blocked by default).
- `not_applicable` — the rule's inputs are absent (no upstream
  cost block, no architecture services, no intake timeline). Never blocks.

## Two-layer check (`POST /api/v1/sow/checks`)

The endpoint returns `{ rules, issues, summary, can_export }` (`DraftCheckResponse`):

- **Layer 1 — deterministic (always runs).** `check_draft()` evaluates every rule
  above and emits a `ConsistencyIssue` (`source: "deterministic"`) for each failure.
  Only this layer sets rule pass/fail and drives the blocking banner.
- **Layer 2 — LLM reviewer (opt-in via `verify: true`).** `verify_draft_async()`
  runs `prompts/verification.txt` over the draft and returns advisory issues
  (`source: "model"`). Any failure degrades to no issues; it never breaks Layer 1.

Issues from both layers are combined by `merge_issues()`: de-duplicated by
`(section, title)` with deterministic issues winning on conflict, sorted by
severity, and capped at `ISSUE_CAP` (8). `can_export` stays `True` by default.

## Legal-supplied rules

No Legal/PDF source rule set was supplied for this repository. Rather than mark
rules `not_applicable` with "rule not configured", this catalog seeds concrete,
reasonable defaults and ships matching golden mock data.

> **Deferred:** the partner-funding cap and the funding document↔source sync
> check are **not** part of this rule set. Funding is being split out into a
> separate template/document and will be reintroduced — with its own rules —
> when that template is wired. The `< 90%` cap figure (stated verbatim in
> `SOW_Project_Catalyst_Demo_Pilot.pdf` 4.11, "the partner must not receive 90
> percent or more of the funding") belongs with that future work.

Per the PDF (4.11.2.3), the agent applies **up to ten** compliance rules supplied
by Ingram Micro's Legal/Commercial Owner (due Day 12), with each result reported
as pass/fail plus evidence. The rules here are the illustrative seed set; the real
set replaces/extends them. In the target architecture (`SOW_gen_agent.drawio.png`)
these rules are retrieved from OpenSearch Serverless alongside templates and fixed
clauses — this YAML is the local stand-in for that rule store.

## Evaluator reference

See the header comment of `sow_compliance_rules.yaml` for the full input/evidence
contract of each `rule.type` primitive (`section_present`, `numeric_compare`,
`upstream_numeric_compare`, `upstream_link_in_sow`, `services_coverage`,
`timeline_match`).
