# Folder structure

```
main.py              AgentCore Runtime entry point: validates and stores events, replies at once
Dockerfile           arm64 image for the runtime
app/                 Backend that runs in the runtime
  events.py            Input contract (ila.event.v1) and validation
  ingest.py            Stores an event and wakes the worker
  processor.py         Background worker: batches pending turns, runs the agent
  orchestrator.py      One agent run: prompt, Strands agent, apply results
  minutes.py           End-of-call minutes and summary with turn citations
  minutes_export.py    Minutes as Markdown
  memory.py            Long-term memory client (facts per account and opportunity)
  call_memory.py       What a call reads from and writes to long-term memory
  account_store.py     Accounts and opportunities (timeline table)
  prompts/             Prompt text: core.py (base instructions), suggestions.py (question rules), minutes.py (MoM)
  tools.py             Tools the agent calls
  assist_config.py     Suggestion settings
  guidance.py          Checklist sets: built-in (config/guidance) or a call's profile copy
  config_schema.py     Config profile schema (pydantic, also served as JSON Schema)
  config_store.py      Config profiles and versions (timeline table), built-in included
  timeline_store.py    Timeline table: call details, turns, runs, minutes
  session_store.py     Sessions table: checklist state, suggestions
  views.py             Call view and handoff for the API
  transcript.py        Turn shape and prompt line format
  meeting_link.py      Google Meet, Teams and Zoom links -> platform, meeting id, passcode
  meeting_join.py      meeting.join: sends the Vexa bot and follows the meeting in the background (keys in memory only)
  meeting_source.py    A Vexa bot's sentences become turns; the call ends with the meeting
  vexa_client.py       Vexa REST and WebSocket client (send bot, live transcript)
  speaker_roles.py     Which display names are the partner BA (everyone else is the customer)
  transcript_text.py   Pasted text, WebVTT or SRT -> conversation turns
  conversation_import.py  Parses recorded conversation files
config/guidance/     Checklists (general.json plus specialised types)
console/             The Live Assist console (static HTML, CSS and ES modules; no build step)
  js/                  api, sources, audio (browser-side conversion), session (polling), render
api/                 HTTP API (Lambda behind API Gateway)
  app.py               Routes, auth, call handlers
  live_sources.py      Meeting and transcript calls: validation, and the meeting.join event
  console_server.py    Console hosting rules for local use: static files, /api proxy, origin checks
  accounts.py          Account, opportunity and memory routes
  configs.py           Config profile routes
  http.py              Request, response, errors, cursors
  handler.py           Lambda entry point
  local.py             Local dev server: console and API on one port
replayer/            Sources that feed a call (Lambda)
  core.py              Replays a text recording
  transcribe.py        Streams audio through Amazon Transcribe
  lambda_handler.py    Lambda entry point for both
  clients.py           Senders for the local agent or the runtime
  cli.py               Command line for running sources
deploy/              Deployment
  deploy.sh            Create, update or delete an environment
  config.example.env   Settings template (copy to config.<env>.env)
  build_lambda.py      Builds the Lambda zip
  ensure_ltm_memory.py Creates the long-term memory and its strategy
  smoke.py             End-to-end check through the API
  templates/           IAM and bucket policy templates
scripts/             Helpers: dev_up.sh (run everything locally), create tables or memory, watch a call, import Attio transcripts
tests/               Test suite (fake model, mocked AWS)
docs/                API, agent, infrastructure, structure and table docs
```

## Kept out of git

| Path | Why |
|---|---|
| `.env`, `deploy/config.*.env`, `deploy/outputs/` | Local settings and per-environment outputs |
| `dist/` | Built Lambda zip |
| `tmp/` | Prompt and message dumps (contain call text) |
