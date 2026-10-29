# Ingram Pipeline: web UI deployment (Amplify)

Static web UI for the pipeline backend, in the same Ingram console format as the SOW, Architecture and Fund Raise UIs:

```
Web UI (AWS Amplify Hosting, stack ingram-pipeline-ui)
  → HTTP API (stack ingram-pipeline, ../deployment)
    → API Lambda + worker Lambda (one stage per invocation)
      → SOW agent · Architecture agent · Cost agent (AgentCore runtimes)
```

This folder only creates the Amplify app. It does not change the backend or the agents.

```
deployment-ui/
├── config.env               review (gitignored; copy of config.env.example)
├── template.yaml            CloudFormation: Amplify app + main branch (manual deploys)
├── ui/                      static page
│   ├── index.html, app.js, styles.css, markdown.js
│   ├── config.js            API URL (written by deploy.sh)
│   ├── templates.js         template dropdown (written by scripts/sync_templates.py)
│   └── samples.js, samples/ the "Use sample" button (written by scripts/sync_samples.py)
├── scripts/
│   ├── deploy.sh            API URL → Amplify stack → CORS → config.js + templates + samples → CSP → upload
│   ├── update_cors.py       adds origins to the API's CORS allow-list (additive)
│   ├── sync_templates.py    ../../sow-generator-agent/resources/templates → ui/templates.js
│   └── sync_samples.py      sample engagements → ui/samples + ui/samples.js
└── tests/ui.test.js         headless test of the page (jsdom + a fake API)
```

## Deploy

Prerequisites: AWS credentials (`aws sts get-caller-identity`), `aws`, `zip`, `curl`, `python3` with `boto3`, and the
backend already deployed (`../deployment`, `sam build && sam deploy --guided`).

```bash
cd deployment-ui
cp config.env.example config.env   # first time only; set API_STACK to the backend stack name
./scripts/deploy.sh
```

`deploy.sh` reads `ApiUrl` from the backend stack, creates the Amplify app, adds the Amplify URL to the API's CORS list,
writes `ui/config.js`, regenerates the template and sample catalogues, pins the page's Content-Security-Policy to the API
host, and publishes. Re-run it to republish after any change (a new template, a new sample).

CORS is changed out-of-band (the HTTP API's own definition), so a later `sam deploy` of the backend resets it to its
`UiOrigins` parameter. Pass the Amplify URL there too: `sam deploy --parameter-overrides UiOrigins="https://main.<id>.amplifyapp.com,http://localhost:5173"`.

Local UI (optional): `python3 -m http.server 5173 --directory ui`, then open http://localhost:5173. The default CSP
allows any `execute-api` host for this; `deploy.sh` narrows it to the real API.

## The "Use sample" buttons

Each sample is a fictional engagement. Two buttons fill the **New run** form:

| Button | Fills | Then |
|---|---|---|
| **Use sample** | customer, opportunity, template, project details, intake JSON | the SOW is drafted from scratch (the long path) |
| **Use with example SOW** | the same, plus the finished SOW | drafting is skipped; the run starts at the Well-Architected review (the quick demo) |

Samples come from `../../sow-generator-agent/samples` plus this project's own `../samples`. A sample's
`upstream.json` has its **architecture and cost blocks stripped** by `sync_samples.py`: this pipeline computes both, and a
stale figure in the input would otherwise leak into a run meant to produce its own.

| Sample | Template | Notes for a demo |
|---|---|---|
| Fabrikam Insurance | AI/ML | Passes the review; the clean end-to-end run. |
| Tailspin Toys | DevOps | **Stops at the Well-Architected review**: its SOW has no backup or recovery commitment. This shows the gate; use *Continue anyway* to carry on. |
| Northwind Traders | AI/ML | Minimal inputs (no intake JSON): shows the form building a handoff from the customer name. |
| Woodgrove Bank | CVA (Full) | The EMEA USC Cloud Viability Assessment template, with the target architecture and cost link added. |

## API used by the UI

| UI action | Call |
|---|---|
| Page load | `GET /session` (quota and the stage order), `GET /runs` |
| Start the pipeline | `POST /runs` |
| Watch a run | `GET /runs/{id}` every 4 s until completed, failed or blocked |
| SOW tab | `GET /runs/{id}/sow` |
| Architecture tab | `GET /runs/{id}/diagram` |
| Continue anyway | `POST /runs/{id}/continue` |
| Download combined SOW (.docx) | `POST /runs/{id}/export`, then a navigation to the presigned S3 link |

## Things to know

- **Export needs the SOW agent wired in.** `POST /runs/{id}/export` renders the `.docx` on the SOW agent and presigns it
  from the SOW data bucket, so the backend stack needs `SowAgentRuntimeArn` and `SowDataBucketName`. Without them the
  button returns "Export is not configured". It is enabled only after the cost stage, because the SOW gets its cost
  link from there.
- **History is the backend's.** Unlike the SOW UI, runs are listed from the API, so they survive a refresh and are shared.
- **No inner HTML.** Every value from the API is set as text, and the cost link is only clickable when it points at
  `https://calculator.aws/`.
- **Tests.** `npm i jsdom && node tests/ui.test.js` loads the real page against a fake API.
