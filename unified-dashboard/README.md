# Pipeline Runs (unified dashboard tab)

A static page at `/pipeline/` on https://d257wmhppqdfnv.cloudfront.net, plus a "Pipeline Runs" link in the portal sidebar.
It runs the six agents as one connected pipeline, or any one agent on its own.

```
Intake ─► Live Assist ─► Discovery ─► Live Assist (on the report) ─► SOW draft + Well-Architected review
                                                                          │
        final SOW ◄─ final review ◄─ combined SOW (regenerated) ◄─ Cost ◄─ Architecture
```

## Where things are

| Path | What |
|---|---|
| `pipeline-app/` | Source of the page (plain ES modules, no build step). Deployed to `s3://…/pipeline/`. |
| `pipeline-app/js/agents/` | One client per agent (Intake, Live Assist, Discovery, SOW, Architecture, Cost). |
| `pipeline-app/js/domain/` | Pure functions: how one agent's output becomes the next agent's input. |
| `pipeline-app/js/pipeline/` | Stage definitions, stage runners, orchestrator (order, gates, stop, resume, clear, exit). |
| `pipeline-app/tests/` | `npm test` (19 tests, no network). |
| `scripts/deploy.sh` | Test, upload `/pipeline`, add sidebar link, update CSP, invalidate CloudFront. |
| `scripts/rollback.sh` | Undo all of the above. |
| `scripts/live_contract_check.mjs` | Calls the real Intake and SOW APIs with the page's own client code. |
| `_backup_live_site/`, `_backup_infra/` | Copies of the live site, API CORS, CloudFront config and headers policy taken before the change. |

## How the agents are connected

* Browser orchestration: the page calls each agent's own API in turn. Nothing new runs server-side, and every agent stays independently callable.
* Live Assist is called through the portal's own `/api/v1/live-assist` proxy (cookie sign-in + CSRF). The Live Assist access key never reaches the browser.
* The page requires the portal sign-in (`/api/v1/me`) and redirects to `/login/` otherwise.
* All state is in memory. "Exit pipeline" (or leaving the page) clears it. Nothing is written to storage.

| Stage | Input | Output |
|---|---|---|
| Intake | seller notes, CRM `.csv/.xlsx`, notes `.pdf/.docx` | ranked opportunities; one is selected |
| Live Assist | Intake record as a briefing transcript, plus an optional real call transcript | captured fields, open gaps, questions |
| Discovery | Intake record + Live Assist handoff (+ call transcript) as documents | use cases, hidden needs, open questions |
| Live Assist follow-up | Discovery report as a transcript | questions for the next conversation |
| SOW draft | `upstream` = Intake + Live Assist + Discovery | draft + Well-Architected review |
| Architecture | the SOW draft | design, ADR, usage, diagram |
| Cost | usage figures rendered from the architecture | Pricing Calculator estimate |
| Combined SOW | `upstream` + architecture + cost; calculator link written into Pricing | combined SOW |
| Final review | combined SOW | Well-Architected review, rule check, `.docx`, open-items list |

Gaps added beyond the brief: opportunity selection (Intake can return several), pause on high Well-Architected findings,
quota pre-flight, stop/resume from the failed stage, per-stage timeouts, a consolidated "items to confirm" list,
and a download-everything bundle before exit.

## Deploy / roll back

```bash
cd unified-dashboard
scripts/deploy.sh      # needs AWS credentials for account 455162169375
scripts/rollback.sh
```

Changes outside `pipeline/`: one `<script>` tag in each portal page, two hosts added to the CSP `connect-src`
(SOW and Cost APIs), and the CloudFront origin added to the SOW and Cost API CORS (also written to their `config.env`
`CORS_EXTRA_ORIGINS` so their next deploy keeps it). The portal's Next.js source is not in this workspace; if its
team redeploys the portal, re-run `scripts/deploy.sh` (or add the page and the tag in their source).

## Known limits

* A run lives in the browser tab. Closing it loses an in-flight run (the agents' own run history is unaffected).
* Intake has no delete API: "Clear" discards the page's results and uses a fresh session; server-side run records remain.
* Discovery takes 10–30 minutes and is limited to 4 runs per IP per hour and 20 per day.
* Outlook mailbox scanning (device-code sign-in) is not wired into the pipeline.
