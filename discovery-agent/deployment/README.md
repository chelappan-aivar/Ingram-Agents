# Ingram Discovery agent: deployment
Job: **read every document from one customer engagement (call transcripts, emails, MOMs, chats, .docx, .pdf) and
produce a grounded discovery report (explicit use cases, hidden opportunities, open questions) as `discovery.json`,
`README.md`, `discovery_report.docx` and `narrative.json`, asking clarifying questions when needed.**
Same pattern as the Intake and Architecture agents (see `agent-build-playbook.md`). Agent internals: `../docs/`.
```
Web UI (Amplify) --1--> HTTP API -> Lambda ingram-discovery-api   (open, per-IP + daily cost guards)
                                      -> DynamoDB ingram-discovery-runs   run history, cost-guard counters
                                      -> AgentCore Runtime ingram_discovery_agent (code zip, arm64, background thread)
                                           -> Amazon Bedrock: Sonnet (explicit needs), Opus (hidden signals,
                                              clarifications), Haiku (narrative, trimming, orchestrator)
                                           -> S3 ingram-discovery-data-<acct>-<region>
                                                input/<folder>/   work/<folder>/<run_id>/   output/<folder>/<run_id>/
          --2--> presigned PUT / GET straight to S3 (documents in, reports out; never through the Lambda)
```
## How a run works
1. `POST /engagements/{name}/upload-urls` with `{"files": [{"name", "size"}]}` returns one presigned PUT per file
   (size and SSE-KMS headers are signed; `.txt .md .eml .docx .pdf`, 25 MB each, 200 files). The browser PUTs each
   file to `input/<folder>/` (`"Acme Corp"` -> `Acme_Corp`). Earlier uploads for the same engagement are part of the run.
2. `POST /runs` with `{"engagement", "instructions"?, "clarify"?}` takes a cost-guard slot, calls the runtime
   (`action: start`) and records `RUN#<folder>/<run_id>/META` in DynamoDB. The runtime answers in about a second and
   keeps working in a background thread (`add_async_task`, session HealthyBusy, up to 8 h).
3. The orchestrator runs seven tools in order (`load_documents -> extract_explicit_needs -> find_hidden_signals ->
   merge_and_rank -> write_narrative -> render_outputs -> publish_outputs`), checkpointing each step under `work/` and
   rewriting `output/<folder>/<run_id>/status.json` after every step. The UI polls `GET /runs/{id}?engagement=` every 5 s;
   the API caches the state in the META item for the history.
4. With "ask me clarifying questions" the run pauses after `merge_and_rank` (`awaiting_clarification`). The UI shows
   `GET /runs/{id}/questions`; `POST /runs/{id}/answers` writes `work/.../answers.json` once and calls the runtime with
   `action: resume_with_answers`, which restores the checkpoints, resolves the answers (Opus) and finishes.
5. `GET /runs/{id}/results?engagement=` signs 5-minute download links for the four outputs.
## API
| Route | Request | Response |
|---|---|---|
| `GET /session` | | `{"ok", "runs_today", "runs_per_day", "runs_per_ip_hour"}` |
| `POST /engagements/{name}/upload-urls` | `{"files": [{"name", "size"}]}` | `{"engagement", "input_prefix", "expires_in", "method": "PUT", "required_headers", "urls": {name: url}}` |
| `POST /runs` | `{"engagement", "instructions"?, "clarify"?, "narrative"?}` | 202 `{"run_id", "engagement", "session_id", "status_key", "runtime_answer"}`; 400 no documents; 429 cost guard |
| `GET /runs` | | `{"runs": [{"run_id", "engagement", "status", "steps_done", "created_at", "finished_at", "clarify", "instructions"}]}` (50 newest) |
| `GET /runs/{id}?engagement=` | | `status.json`: `{"run_id", "engagement", "state", "completed_steps", "steps", "error", "instructions", "output_keys", "updated_at"}`; state `starting` / `running` / `awaiting_clarification` / `succeeded` / `failed` |
| `GET /runs/{id}/results?engagement=` | | `{"run_id", "engagement", "expires_in", "urls": {file: url}}`; 409 until succeeded |
| `GET /runs/{id}/overview?engagement=` | | `{"header", "summary": [..], "stakeholders": {ingram, partner, customer, unplaced}, "timeline": [{date, title, summary}]}`; works while the run is going |
| `GET /runs/{id}/report?engagement=` | | `{"report", "narrative", "context"}` (discovery.json, narrative.json, context.json); 409 until succeeded |
| `GET /runs/{id}/questions?engagement=` | | `{"run_id", "engagement", "questions": [{"id", "question", "type", "reason"}]}`; 409 unless awaiting |
| `POST /runs/{id}/answers` | `{"engagement", "answers": {id: text}}` | 202 `{"run_id", "engagement", "resumed": true, "status_key"}`; 409 not awaiting or already answered |

Errors are `{"error": "..."}`. A run with no status update for 2 hours (or never started after 15 minutes) is
reported as failed. Runtime actions (`agent/main.py`): `start` (default), `resume_with_answers`, `ping` (0 model calls).
## Access
**Open: no sign-in.** Anyone with the UI or API URL can upload documents, start runs and read every run in the
shared history (they need the engagement name and run id to read a run, both visible in the history). Cost guards
instead of accounts: API Gateway throttling (POST /runs and /answers 1 per second), `RUNS_PER_IP_HOUR` (default 4, the
caller IP is stored only as a SHA-256 hash) and `RUNS_PER_DAY` (default 20). Each run is several Sonnet and Opus calls
over the whole corpus. Do not upload real customer documents while it is open; to restrict it, add a JWT authorizer
(Entra ID) on the HTTP API (playbook, section 6). The data bucket is private, SSE-KMS (aws/s3 by default, or
`KMS_KEY_ARN`), TLS-only, CORS limited to the UI origins.
## Test and deploy
```bash
cd deployment
cp config.env.example config.env                     # then edit; nothing in it is secret
cd agent && uv run python -m unittest discover -s tests -t . && cd ..   # agent + API unit tests (no AWS)
python scripts/local_e2e.py                          # real API handler + agent server, S3/DynamoDB faked, models stubbed
python scripts/local_e2e.py --serve                  # then open http://localhost:5173 (same fakes)
python scripts/local_e2e.py --bedrock                # real Bedrock model calls (credentials; costs money)
./scripts/deploy.sh                                  # zip -> S3 -> sam deploy -> CORS -> config.js -> CSP -> Amplify -> ping
```
`local_e2e.py` needs a Python 3.12 venv with `agent/requirements.txt` installed. `scripts/deploy.sh` needs AWS
credentials, the AWS CLI, SAM CLI and uv. The code zip key is a hash of `agent/requirements.txt`, `main.py`,
`discovery_agent/` and `schema/`, so unchanged code is neither rebuilt nor redeployed. deploy.sh pins the UI's CSP
`connect-src` to the API origin and the data bucket's regional host (`DataOrigin` output), because the browser uploads
straight to S3.

Local CLI without AWS infrastructure (calls Bedrock directly):
`cd agent && AWS_PROFILE=<profile> python -m discovery_agent samples/Acme/*.txt --engagement "Acme"`.
Name input files oldest to newest (`01_...`, `02_...`): they are read in file-name order.
## DynamoDB (`ingram-discovery-runs`, pk/sk, TTL `ttl`, GSI `owner-created`)
| Item | Attributes |
|---|---|
| `RUN#<folder>/<run_id>` / `META` | `run_id, engagement, owner="shared", created_at, updated_at, status, steps_done, clarify, narrative, instructions, finished_at, error` |
| `DAY#<yyyy-mm-dd>` / `COUNT` | `n` runs started that day, `ttl` 3 days |
| `IP#<sha256>#<yyyy-mm-ddThh>` / `COUNT` | `n` runs from one caller IP that hour, `ttl` 2 hours |
## config.env
`AWS_REGION`, `AWS_PROFILE` (optional), `STACK_NAME`, `RUNTIME_NAME`, `EXTRACTION_MODEL_ID`, `HIDDEN_SIGNALS_MODEL_ID`,
`SMALL_MODEL_ID`, `INFERENCE_PROFILE_PATTERN`, `KMS_KEY_ARN` (optional), `RUNS_PER_DAY`, `RUNS_PER_IP_HOUR`, `UI_ORIGIN`,
`CORS_EXTRA_ORIGINS`. The model ids are the agent's current defaults; check that each is enabled in Bedrock for the
target account.
## Layout
```
template.yaml            SAM: runs table, data bucket, runtime + role, HTTP API, API Lambda, Amplify
agent/main.py            AgentCore entrypoint: start | resume_with_answers | ping
agent/discovery_agent/   the agent (analysis, data, prompts, rendering, runtime, tools, settings.py)
agent/schema/            report.schema.json (sits next to the package; validate.py and the zip rely on it)
agent/tests/             unit tests for the agent and api/handler.py
agent/requirements.txt   runtime pins; pyproject.toml + uv.lock for local development
api/handler.py           the API Lambda (ROUTES)
ui/                      index.html, styles.css, app.js, config.js (written by deploy.sh)
scripts/                 deploy.sh, package_agent.sh, local_e2e.py
tools/                   invoke_runtime.py (ping / start), watch_run.py, upload_client.py (CLI client for the API)
```
## Notes
- Log retention: the Lambda log group keeps 30 days; set one on the runtime log group (`LogGroup` output).
- The runs table and data bucket are retained when the stack is deleted; the artifacts bucket is outside the stack.
- Engagement folders are shared: uploading a file with an existing name replaces it for the next run.

## Pipeline: Intake Agent + Live Assist (two-way)
The "From Intake and Live Assist" card lists the opportunities the Intake Agent checked for funding, read through the
Live Assist API with the user's own Live Assist access key (`X-Access-Key`, kept in the browser tab; the Lambda forwards
it per request and stores nothing). **Import and run** writes `00_intake_record.md` plus each ended call's
`*_transcript.txt` (Discovery's `Name` / `m:ss` / text format, so speakers and quotes are verified) and `*_minutes.md`
into `input/<engagement>/`, then starts a normal run with `pipeline_opportunity_id`. The run is registered back in Live
Assist, which shows the report summary on the opportunity and can turn its open questions into the next call's agenda.
Routes: `GET /pipeline/opportunities`, `POST /pipeline/opportunities/{id}/import` (`api/pipeline.py`). Setting:
`LIVE_ASSIST_API_URL` (config.existing.env). The Live Assist console links here with `#opportunity=<id>` / `#run=<engagement>/<run_id>`.
