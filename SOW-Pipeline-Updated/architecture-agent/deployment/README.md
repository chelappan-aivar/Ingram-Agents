# Ingram Architecture agent: deployment

Job: **turn a software requirements document (SRS) into an architecture decision record, an AWS architecture
diagram and a monthly usage estimate, and flag every assumption and open question for review.**
Same pattern as the Intake and Fund Raise agents (see `agent-build-playbook.md`).

```
Web UI (Amplify) -> HTTP API -> Lambda ingram-architecture-api   (open, per-IP + daily cost guards, file reads)
                                  -> Lambda ingram-architecture-worker (async, 15 min, no retries)
                                       -> AgentCore Runtime ingram_architecture_agent (container, arm64)
                                            -> Amazon Bedrock (Claude Sonnet 4.6)   ADR + usage: 2 calls per version
                                            -> draw.io + ELK in the container        diagram: 0 model calls
                                  -> DynamoDB ingram-architecture-runs   runs, versions, activity log
                                  -> S3 ingram-architecture-data-<acct>-<region>   SRS, adr.md, spec.json, usage.json, diagram.png/svg/drawio
```

## How a run works

1. `POST /runs` with pasted SRS text or one file (.md, .txt, .docx, .pdf, up to 4 MB). The API stores it in S3,
   writes the run to DynamoDB and starts the worker; the UI polls `GET /runs/{id}`.
2. The worker calls the runtime step by step (one AgentCore session per version): `extract_srs` (files only),
   `generate_adr`, `generate_diagram` (drawn from the design, no model call), `estimate_usage`. Each step writes its
   files to S3 and a log line to DynamoDB. A failed diagram or usage step keeps the ADR (status "Ready with gaps").
3. `POST /runs/{id}/revise` with an instruction creates the next version from the latest good design (`revise_adr`,
   then diagram and usage again). Up to 10 versions per run.
4. Outputs are drafts for a solutions architect to review. The agent code (`agent/`) is stateless and unchanged.

## Access

**Open: no sign-in.** Anyone with the UI or API URL can start runs and read every run in the shared history.
Cost guards instead of accounts: API Gateway throttling, `RUNS_PER_IP_HOUR` (default 6, caller IP stored only as a
SHA-256 hash) and `RUNS_PER_DAY` (default 40) runs + revisions. Don't paste confidential requirements while it is open;
to restrict it later, add a JWT authorizer (Entra ID) on the HTTP API (see the playbook, section 6).

## Test and deploy

```bash
cd deployment
./deploy.sh setup && ./deploy.sh test          # local venv + the agent's own test suite
.venv/bin/python scripts/local_e2e.py           # API + worker + real agent server + draw.io; model steps faked
.venv/bin/python scripts/local_e2e.py --bedrock # same with real Bedrock (needs credentials)
.venv/bin/python scripts/local_e2e.py --serve   # then open http://localhost:5173 (key printed)
./scripts/deploy.sh                             # image -> ECR -> sam deploy -> CORS -> config.js -> Amplify -> smoke test
```

`scripts/deploy.sh` needs AWS credentials, Docker with buildx (builds `linux/arm64`), SAM CLI and Python 3.12. The
image tag is a hash of `Dockerfile` + `agent/`, so an unchanged agent is not rebuilt. `./deploy.sh smoke [--full]`
invokes the deployed runtime directly; `./deploy.sh status` shows the stack outputs.

## Layout

```
template.yaml            SAM: table, bucket, runtime + role, HTTP API, API + worker Lambdas, Amplify
Dockerfile               runtime image: Python 3.12, draw.io desktop + Xvfb, Node (ELK, libavoid)
agent/                   the agent (architecture_agent, adr_generator, usage_estimator, diagram_generator, vendor, tests)
api/                     handler.py (routes), worker.py (runtime steps), common.py (DynamoDB / S3 helpers)
ui/                      index.html, styles.css, app.js, markdown.js (safe renderer), config.js, sample_srs.md
scripts/deploy.sh        full deploy;  scripts/local_e2e.py  local end-to-end test
deploy.sh                local helpers: setup, test, serve, local-test, smoke, status, destroy
tools/invoke.py          calls the runtime directly (SigV4) or a local agent
```

## Notes

- Log retention: the Lambda log groups keep 30 days; set one on the runtime log group (`LogGroup` output).
- The runs table and data bucket are retained when the stack is deleted.
- `./deploy.sh destroy` deletes the stack and the ECR images.
