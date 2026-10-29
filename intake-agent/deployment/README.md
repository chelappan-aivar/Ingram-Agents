# Ingram Intake agent: deployment

Implements `../docs/architecture-ui.png`:

Web UI (AWS Amplify Hosting) → API Gateway (HTTP API, Entra ID JWT) → Lambda `ingram-intake-api` → AgentCore Runtime `ingram_intake_agent`
→ Microsoft Graph (Outlook) + Amazon Bedrock (Claude). Every run, email decision, opportunity and log line goes to DynamoDB `ingram-intake-runs`.

```
deployment/
├── config.env            ← review and fill in (gitignored)
├── template.yaml         SAM: DynamoDB, AgentCore Runtime + role, HTTP API + JWT authorizer, Lambda, Amplify app
├── agent/                AgentCore Runtime code (direct code deploy, Python 3.12, arm64)
│   ├── main.py           entrypoint: token swap, then background run
│   └── intake/           auth (on-behalf-of) · mail (Graph) · extractor (Bedrock) · pipeline · store (DynamoDB) · prompt.md
├── api/handler.py        POST /runs, POST /opportunities, GET /runs/{id}
├── ui/                   static page: Microsoft sign-in, add an opportunity, 1/7/30-day mailbox run, results with gaps flagged
└── scripts/deploy.sh     secret → agent zip → sam deploy → ui/config.js
```

## 1. Entra ID app registration (Ingram Intake)

| Setting | Value |
|---|---|
| Certificates & secrets | Create a **new** client secret; delete the old one (it was shared in chat) |
| Expose an API | Application ID URI `api://<client-id>`; add scope **`access_as_user`** (admins and users) |
| Manifest | `"requestedAccessTokenVersion": 2` (API Gateway validates the v2 issuer) |
| Authentication | Add platform **Single-page application** with redirect URIs: the Amplify `UiUrl` printed by `deploy.sh` (after the first deploy), and `http://localhost:5173` for local testing |
| API permissions | Microsoft Graph **Delegated**: `Mail.Read`, `User.Read` (already consented) |

The agent reads mail with the signed-in user's delegated permission (on-behalf-of flow), so it only ever sees that user's inbox. No application `Mail.Read` is needed.

## 2. Review `config.env`

Set `ENTRA_CLIENT_SECRET` to the new secret value. Check region, model ID (must be enabled in Bedrock for the account) and `MAX_EMAILS`.
The secret is stored in AgentCore Identity by `scripts/set_entra_secret.py`; it never goes into the template or the stack.

## 3. Deploy

Prerequisites: valid AWS credentials (`aws sts get-caller-identity`), `sam`, `uv`, `zip`.

```bash
cd deployment
./scripts/deploy.sh
```

The script creates `ingram-intake-artifacts-<account>-<region>` for the agent zip, deploys the stack, writes the API URL into `ui/config.js` and publishes `ui/` to **AWS Amplify Hosting** (`https://main.<app-id>.amplifyapp.com`, printed as `UI:`).
After the first deploy, add that URL as an SPA redirect URI in Entra (§1). Re-running `deploy.sh` republishes the UI.

Local UI (optional): `python3 -m http.server 5173 --directory ui`, then open http://localhost:5173.

## How a run works

1. `POST /runs {days}`: Lambda writes `RUN#id / META` (QUEUED), invokes the Runtime with the user's token and returns `run_id` (202).
2. The agent swaps the token for an Outlook token, marks the run RUNNING and continues in the background (up to 8 h).
3. Emails are grouped into threads; Claude captures / structures / flags each thread (4 in parallel). Calendar replies are skipped without a model call.
4. `GET /runs/{id}`: the UI polls every 4 s and shows status, counts, opportunities with missing information, and the agent log.

**Add an opportunity (seller input):** `POST /opportunities {title?, text}` (up to 20,000 characters). The same Lambda → Runtime path, but the agent answers synchronously (about 10 s, one Claude call). It is logged as its own run (`source: manual`, `OPP#manual`) and returns the structured opportunity with missing information flagged.

DynamoDB items per run: `META`, `EMAIL#<msg>`, `OPP#<thread>`, `LOG#<time>`. Users can only read their own runs.

## Not verified yet (needs the first deploy)

- The Secrets Manager path AgentCore Identity uses for API-key providers (`bedrock-agentcore-identity!default/apikey/<name>*`). If the agent log shows `AccessDenied` on `GetResourceApiKey`, compare with `apiKeySecretArn` printed by `aws bedrock-agentcore-control get-api-key-credential-provider --name ingram-intake-entra-secret`.
- The workload identity name pattern `ingram_intake_agent-*` used in the Runtime role.

## Intake record (what the agent produces)

For an email, CRM record or seller note the agent returns:
- **Fields**: up to 40 (`agent/intake/fields.py`, 7 groups, key fields marked). Each has a value and the
  exact source passage; `verified` is set in code when the passage is found word for word in the input.
- **Gaps**: up to 12, each with priority, why it matters and one draft follow-up question.
- **Programme fit card**: MAP (fit, suggested phase, criteria met / not met / unknown with evidence, next step)
  and MPE. Criteria live in `agent/intake/programmes.md`; edit that file to change the scoring.

## Consolidated run: Outlook + CRM records + seller notes (no sign-in page)

The UI opens straight on the dashboard. The browser gets an anonymous session (`POST /session`) that owns its
runs. Three inputs, in any combination, go into one `POST /runs`:

| Source | Needs | One record per |
|---|---|---|
| Outlook inbox (1 / 7 / 30 days) | "Connect Microsoft" (device code, delegated Mail.Read) | email thread |
| CRM records (.csv / .xlsx, up to 3 files, 50 rows) | nothing | row (row 1 = headers) |
| Seller notes (.pdf / .docx, up to 5 files, or pasted text) | nothing | file |

The agent (`agent/intake/pipeline.py`): read → triage every record (short call: opportunity? customer, project,
contacts) → match (one call over all triage profiles; same customer AND same project, across sources and
duplicates within a source; falls back to customer-name matching) → build one intake record per opportunity from
all its documents (fields with source passages attributed to their document, gaps, conflicts between sources,
MAP / MPE fit). An opportunity is **matched** when 2+ sources have it, **unmatched** otherwise, with
`missing_sources` (e.g. "Not in CRM"). Records appear in the UI as each one is built.

**Any one source is enough**; with two or more the agent also matches them.

**Saved runs:** every run stays in DynamoDB (no TTL). `GET /runs` lists the browser's runs newest first (index
`owner-created`, 20 per page with `?cursor=`); the UI's "Previous runs" card opens any of them again. The
browser session lasts 90 days and renews while used; the Microsoft connection expires after 12 hours on its own
(runs stay). Clearing browser storage starts a new session, so its earlier runs are no longer listed.

Files are base64 in the JSON body (3 MB raw in total); `api/uploads.py` extracts the text and returns 400 for
anything unreadable (.xls, .doc, scanned or password-protected PDFs).

**The API is open (no sign-in for CRM / notes).** Cost guards: per-route throttling (`POST /runs` 1 rps,
`POST /session` 2 rps), `RunsPerIpHour` (default 15) and `RunsPerDay` (default 300) counters in DynamoDB
(`QUOTA#…`, 429 when exceeded), capped rows / files / emails per run. Runs are readable only by the session
that started them. Consider AWS WAF or putting the UI behind company SSO before wider use.

Local test: `python scripts/local_e2e.py` (CRM + notes from `../test-data`) or pipe a Graph token and add
`--outlook --days 7` for all three sources.

## Funding check (new flow): opportunity form → Dynamics 365 → AWS funding decision

Page: `<UiUrl>/funding.html` (linked as "Funding check →" from the Intake page). The Intake flow is unchanged;
both flows share the stack, the API, the browser session and the AgentCore Runtime (`action: funding_check`).

```
funding.html ── POST /funding/opportunities {answers} ──> Lambda ──> Dynamics 365: opportunity + note with the answers (JSON)
             ── POST /funding/checks {opportunity_id} ──> Lambda ──> AgentCore Runtime (background)
                                                                     1. fetch the opportunity + answers from Dynamics
                                                                     2. facts: form answers, then Claude on the free text
                                                                     3. decide: funding/rules.py (the framework, as code)
                                                                     4. note "AWS funding check: <program>" on the opportunity
             ── GET /funding/checks/{id} (polls every 2.5 s) ──> decision, path, facts with evidence, questions
```

| Piece | File |
|---|---|
| The form: every question in `Form input questions .xlsx` (113), sections shown per request type, plus an **AWS funding** section with what the framework needs (segment, workload, VMs, Gen AI stage/type, MRR/ARR) | `shared/python/opportunity_form.py` → `ui/form.js` (`scripts/gen_form_js.py`) |
| Dynamics 365 (Dataverse Web API v9.2) client, demo store fallback | `shared/python/dynamics.py` (Lambda layer + agent zip) |
| Decision framework as code | `agent/funding/rules.py` |
| Facts: form → Claude (quotes verified in code) → merge, conflicts | `agent/funding/facts.py` |
| Pipeline and DynamoDB record | `agent/funding/pipeline.py` |
| API routes | `api/funding_api.py` |

**How decisions are made.** Segment → workload → ARR (MRR × 12), exactly as in the PDF. "Above X" is strict and
bands include their top (500,000 ARR is MAP Lite, 264,000 is the startup 100k–264k band, 100,000 is below the
MAP Lite floor). An unknown fact is evaluated both ways: if both give the same funding it decides (e.g. any
segment above 500k ARR → MAP), otherwise the result is **Needs information**, with both outcomes and the
question to ask. SAP / Oracle → **To confirm** (no rules yet); ISV → promotional credits; anything else →
**Not covered**. Claude never decides funding; it only classifies facts, and a fact is used only when its quote
is found word for word in the opportunity (form answers always win; disagreements are listed).

**Choice options are assumptions.** The xlsx lists questions only. Options for the choice questions (request
type, vendor, timeline, etc.) are in `opportunity_form.py`; replace them with the real Microsoft Forms options.

**Sign-in.** Funding routes need Microsoft connected (the same device-code sign-in), because they write to
Dynamics. Opportunities and checks are owned by the Microsoft user, not the browser.

### Connecting Dynamics 365 (until then: demo store)

With `DYNAMICS_URL` empty, opportunities are kept in DynamoDB (`DYNOPP#…`) with the same shape, and the page
shows "Demo store". To use real Dynamics:

1. **Entra ID:** register an app (or reuse one) and create a client secret. No Graph permissions are needed.
2. **Power Platform admin center** → Environments → *your environment* → Settings → Users + permissions →
   **Application users** → *New app user* → pick the app, business unit, and a security role that can
   create/read **Opportunity**, **Account** and **Note** (e.g. *Salesperson*, or a custom role).
3. `config.env`: `DYNAMICS_URL=https://<org>.crm<n>.dynamics.com`, `DYNAMICS_CLIENT_ID`, `DYNAMICS_CLIENT_SECRET`
   (and `DYNAMICS_TENANT_ID` if the app is in another tenant). Run `./scripts/deploy.sh`. The secret goes to
   Secrets Manager `ingram-intake/dynamics-client-secret`; only its ARN reaches the stack, and only the API
   Lambda and the agent role may read it.

What lands in Dynamics: an **opportunity** (name = opportunity title, `estimatedvalue` = ARR, description,
customer **account** found by name or created), a **note** "Ingram intake form answers (JSON)" with all answers,
and one **note** per funding check with the decision, path and open questions (`DYNAMICS_WRITE_BACK=false` turns
that off). No Dataverse customisation is needed. The "In Dynamics" tab lists recent opportunities, and any
opportunity can be checked by its ID. For opportunities created outside the form, the agent reads the
name, description and notes.

### Tests

```bash
uv venv build/testenv --python 3.12 && uv pip install --python build/testenv/bin/python -r agent/requirements.txt pytest pypdf openpyxl
build/testenv/bin/python -m pytest tests -q                 # framework rows + boundaries, form, Dataverse mock (no AWS)
build/testenv/bin/python scripts/local_funding_e2e.py       # API + agent + demo store + real Bedrock
```

### Mock Dynamics 365 (dummy data, until a real environment is available)

`DYNAMICS_URL=mock` in `config.env` deploys a Dataverse-compatible Web API (`mock-dataverse/app.py`, Lambda +
HTTP API) over three dummy DynamoDB tables named after the Dataverse tables: `ingram-mock-dynamics-accounts`,
`-opportunities`, `-annotations` (notes). The API and the agent use their **real** Dynamics client against it:
client-credentials token (`/oauth2/v2.0/token`, secret generated in Secrets Manager `ingram-intake/mock-dynamics-client`),
then OData `GET/POST /api/data/v9.2/...` with `$select`, `$filter` (`eq`, `contains`, `and`), `$orderby`, `$top`
and formatted values. Unsupported OData returns a Dataverse-style 400 instead of a wrong answer.

```bash
./scripts/deploy.sh                       # with DYNAMICS_URL=mock
python3 scripts/seed_dynamics.py          # 15 dummy opportunities (scripts/dynamics_seed_data.py); safe to re-run
python3 scripts/seed_dynamics.py --list
```

The dummy pipeline covers every framework branch: some opportunities carry intake-form answers, others only a
description and seller notes (the agent finds the facts in the text), plus won/lost and non-AWS deals. Edit
`dynamics_seed_data.py` to add your own. To move to real Dynamics: follow the steps above, set `DYNAMICS_URL`
to the environment, deploy (the mock resources are removed), and optionally load the same data with
`python3 scripts/seed_dynamics.py --real`.

### Intake form columns in Dynamics (`ingr_`)

Every form question is a custom column on the Opportunity table (118: 113 questions + 5 Microsoft Forms response
fields; choice questions are choice columns). Catalogue: `docs/dynamics-columns.md`; source of truth:
`shared/python/opportunity_form.py` (`COLUMNS`, `COLUMN_RENAMES` to match existing column names). The form writes
them, the agent reads them (no model call when the columns settle the decision), and the Funding Desk shows each
answer with its column. Real Dynamics: `python3 scripts/create_dynamics_columns.py` (dry run), then `--apply
--solution <name>` with System Customizer rights. Environments without the columns: `DYNAMICS_FORM_STORAGE=note`
(answers as a JSON note; reading accepts both). Mock: `python3 scripts/seed_dynamics.py --reset` reloads the 15
opportunities with full answers.
