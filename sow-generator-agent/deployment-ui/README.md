# Ingram SOW Generator: web UI deployment (Amplify)

Static web UI for the **already deployed** SowGenAgent backend:

Web UI (AWS Amplify Hosting, stack `ingram-sow-ui`) → API Gateway REST `sow-gen-agent` (stage `demo`) → Lambda
`SowGenAgent-ApiInvoker…` (response streaming) → AgentCore Runtime `sow_gen_agent` → Amazon Bedrock + S3
`sowgenagent-storagebucket…`. This folder only creates the Amplify app; it does not change the agent or the API routes.

```
deployment-ui/
├── config.env               ← review (gitignored; copy of config.env.example)
├── template.yaml            CloudFormation: Amplify app + main branch (manual deploys)
├── ui/                      static page: new SOW, live streamed draft, rules / AI check, AI editor, .docx download
│   ├── config.js            API URL (written by deploy.sh)
│   └── templates.js, templates/   template catalogue (written by scripts/sync_templates.py from ../resources/templates)
└── scripts/
    ├── deploy.sh            API URL → Amplify stack → CORS → config.js + templates → CSP → Amplify upload
    ├── update_cors.py       adds origins to the backend's CORS allow-list (additive)
    └── sync_templates.py
```

## Deploy

Prerequisites: AWS credentials for account 455162169375 (`aws sts get-caller-identity`), `aws`, `zip`, `curl`, `python3` with `boto3`.

```bash
cd deployment-ui
cp config.env.example config.env   # first time only
./scripts/deploy.sh
```

UI: **https://main.d2ql2k4kqtbcpv.amplifyapp.com**. Re-running `deploy.sh` republishes the UI (for example after a template change).

Local UI (optional): `python3 -m http.server 5173 --directory ui`, then open http://localhost:5173.

## API used by the UI

| UI action | Call | Response |
|---|---|---|
| Generate SOW | `POST /sessions`, then `POST /generations/stream` | SSE: `session`, `chunk`, `usage`, `done` / `error` |
| Check draft | `POST /checks` (`verify` = AI review) | rules, issues, summary |
| Edit with AI | `POST /edits/stream` | SSE: `text`, `tool_call_complete`, `*_proposal`, `done` / `error` |
| Download .docx | `POST /exports/docx` (`persist_output: true`) | presigned S3 link (15 min) |

All under `https://jt1cq55g59.execute-api.us-east-1.amazonaws.com/demo/api/v1/sow`.

## Things to know

- **History lives in the browser.** The API keeps no list of drafts, so drafts (markdown, status, log) are saved in
  `localStorage` (last 20). Inputs and outputs are still saved in the S3 bucket by the agent. A draft streams only
  into the tab that started it; closing the tab mid-draft marks it "Interrupted" (partial text kept).
- **CORS is patched outside CDK.** `update_cors.py` adds the Amplify URL to the invoker Lambda's
  `CORS_ALLOWED_ORIGINS` and to the OPTIONS responses, then redeploys stage `demo`. A later `cdk deploy` of
  `SowGenAgent` resets both: add `https://main.d2ql2k4kqtbcpv.amplifyapp.com` to the CDK CORS config, or re-run `deploy.sh`.
- **The API is open (no sign-in).** Only the stage throttle (5 rps, burst 10) limits callers; CORS does not stop
  non-browser clients. Consider Cognito / company SSO or AWS WAF before wider use.
- `X-User-Id` is an anonymous per-browser id, used by the backend only to group S3 objects (not for access control).
