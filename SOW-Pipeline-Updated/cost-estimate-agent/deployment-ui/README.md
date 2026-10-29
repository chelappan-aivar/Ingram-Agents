# Ingram Cost Calculator agent: web UI deployment (Amplify)

Static web UI for the **already deployed** cost calculator backend:

Web UI (AWS Amplify Hosting, stack `ingram-cost-ui`) → HTTP API `ingram_cost_calculator_api` (`w2ydw8uim4`, stage
`prod`, route `/invoke-agent`) → Lambda `ingram_cost_calculator_proxy` (async) → AgentCore Runtime
`ingram_cost_calculator` → Amazon Bedrock + AWS Pricing Calculator. Results are in DynamoDB `cost-estimates`.
This folder only creates the Amplify app; it changes nothing in the backend.

```
deployment-ui/
├── config.env               ← review (gitignored; copy of config.env.example)
├── template.yaml            CloudFormation: Amplify app + main branch (manual deploys)
├── ui/                      static page: samples, new estimate (text or JSON file), result with calculator link, history
│   ├── config.js            API URL (written by deploy.sh)
│   └── samples.js, samples/ copied from ../samples by scripts/sync_samples.py
└── scripts/
    ├── deploy.sh            API check → Amplify stack → config.js + samples → CSP → Amplify upload
    └── sync_samples.py
```

## Deploy

Prerequisites: AWS credentials for account 455162169375, `aws`, `zip`, `curl`, `python3`.

```bash
cd deployment-ui
cp config.env.example config.env   # first time only
./scripts/deploy.sh
```

UI: **https://main.dn1qhkyrq90bs.amplifyapp.com**. Re-run `deploy.sh` to republish (for example after changing a sample).

## API used by the UI

| UI action | Call | Response |
|---|---|---|
| Start estimate | `POST /invoke-agent {user_input, session_id}` (the UI makes a random `web-…` id) | 202 |
| Poll (every 5 s, up to 20 min) | `GET /invoke-agent?session_id=…` | `{status: processing \| success \| fail, data, error}` |

`data` is the agent's answer: `calculator_url`, `data.environments.<name>.services[]` (name, description, config),
`data.unsupported_services[]`, or `{success: false, message, error}`. The agent returns the configuration, not
prices; the prices and totals are in the calculator link.

## Samples

`../samples/`: three plain-text workloads and one Architecture-agent style JSON (fictional customers), listed in
`samples.json`. `output/*.result.json` are real answers from the deployed agent (25 October 2026), shown by
"View example". `python3 ../samples/run_sample.py <file>` (or `all`) re-runs them; each run costs Bedrock tokens and
saves a public calculator estimate.

## Things to know

- **History lives in the browser.** The API has no list endpoint, so started estimates are kept in `localStorage`
  (last 25). Every running estimate is polled, and a page reload carries on where it left off.
- **The API is open.** No sign-in and CORS allows any origin (`*`) on the existing HTTP API. Anyone who knows a
  `session_id` can read that result; ids are random. Consider a JWT authorizer or AWS WAF before wider use.
- **Retention.** The proxy Lambda writes a `ttl` attribute, but TTL is disabled on `cost-estimates`, so results
  are kept until deleted. Calculator links are public to anyone who has them.
