# Live Assist API

HTTP API for the console. Start a call, feed it turns (typed, from an uploaded
recording, or from uploaded audio through Amazon Transcribe), and poll the live
view: turns, agent runs, suggested questions and the checklist state.

- Base URL: `API_URL` from `deploy/deploy.sh <env> outputs`
  (for example `https://abc123.execute-api.us-east-1.amazonaws.com`). Local
  development: `http://127.0.0.1:8090` (see [Local development](#local-development)).
- JSON in and out. Times are UTC ISO-8601 (`2026-10-13T10:00:18Z`).
- CORS is off until the React host is known (`CORS_ALLOW_ORIGINS` in the
  deployment config). Until then, call the API from a dev proxy or a server.

## Authentication

Every endpoint except `GET /health` needs the shared key:

```
X-Access-Key: <key>
```

Read the key with the command printed by `deploy/deploy.sh <env> outputs`. Keep
it out of source control and browser bundles that are public. This header is
temporary: the org auth service will replace it with a bearer token, and the
endpoints will stay the same.

## Errors

Non-2xx responses have one shape:

```json
{"error": {"code": "session_ended", "message": "This session has ended."}}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_json` | Body is not a JSON object |
| 400 | `invalid_request` | A field is missing or out of range (message says which) |
| 400 | `invalid_event` | The agent rejected the event, e.g. bad `speaker` or settings |
| 400 | `invalid_upload` | The uploaded recording or audio manifest is invalid |
| 401 | `unauthorized` | Missing or wrong `X-Access-Key` |
| 404 | `not_found` | No such route |
| 404 | `call_not_found` | No call with this id |
| 404 | `unknown_session` | Write to a call that doesn't exist |
| 404 | `upload_not_found` | `upload_id` has no uploaded file |
| 405 | `method_not_allowed` | Route exists, method doesn't |
| 409 | `session_ended` | The call has ended |
| 409 | `source_mismatch` | Manual line on a call fed by a recording or audio |
| 409 | `session_live` | Minutes regenerate on a call that hasn't ended |
| 409 | `minutes_not_ready` | Markdown minutes requested before they are `ok` |
| 400 | `unknown_account` | `POST /calls` with an `account_id` that doesn't exist |
| 400 | `unknown_opportunity` | `opportunity_id` doesn't exist or belongs to another account |
| 404 | `account_not_found`, `opportunity_not_found` | No such account, or no such opportunity for it |
| 502 | `memory_unavailable` | Long-term memory didn't answer |
| 400 | `invalid_config` | A config profile doesn't match the schema; `error.details` lists `{path, message}` |
| 400 | `unknown_config` | `POST /calls` with an unknown or archived `config_id` (or version) |
| 400 | `opportunity_type_not_in_config` | The opportunity's preset type isn't in the chosen config |
| 403 | `builtin_config` | Editing or archiving the built-in profile |
| 404 | `config_not_found` | No such config profile or version |
| 409 | `version_conflict` | The profile changed since `expected_version`; reload and reapply |
| 413 | `body_too_large` | Body over 64 KB |
| 429 | (API Gateway) | Throttled; retry with backoff |
| 502 | `agent_unavailable` | The agent runtime didn't answer |
| 502 | `source_start_failed` | Recording or audio couldn't start; the call is ended |
| 500 | `internal_error` | Bug; check the API Lambda logs |

## Polling

`GET /calls/{id}` is the only read the live screen needs. Poll it every 1.5 s
while the call is open, and send the last `ETag` back:

```
GET /calls/{id}
If-None-Match: "3f2a..."
```

`304 Not Modified` (empty body) means nothing changed. Stop polling once
`status` is `ended`, `processing.busy` is `false` and `minutes.status` is `ok`,
`empty` or `error`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, no key needed |
| GET | `/options` | Settings choices, limits and checklist topics for forms |
| POST | `/uploads` | Get presigned URLs to upload a recording or audio |
| POST | `/calls` | Start a call (manual, recording or audio) |
| GET | `/calls` | List calls, newest first |
| GET | `/calls/{id}` | Live view of one call (ETag) |
| POST | `/calls/{id}/turns` | Add a typed line (manual calls only) |
| PATCH | `/calls/{id}` | Pause, resume, or change suggestion settings |
| POST | `/calls/{id}/end` | End the call |
| GET | `/calls/{id}/handoff` | Structured capture for SOW drafting |
| GET | `/calls/{id}/minutes` | Minutes of meeting and final summary (`?format=md` for Markdown) |
| POST | `/calls/{id}/minutes` | Regenerate the minutes of an ended call |
| POST | `/accounts` | Create an account (customer company) |
| GET | `/accounts` | List accounts, newest first |
| GET | `/accounts/{id}` | One account with its opportunities |
| POST | `/accounts/{id}/opportunities` | Create an opportunity |
| GET | `/accounts/{id}/opportunities` | List the account's opportunities |
| GET | `/accounts/{id}/calls` | The account's calls (`?opportunity_id=` for one opportunity) |
| GET | `/accounts/{id}/memory` | What is remembered, per level (`?opportunity_id=`) |
| DELETE | `/accounts/{id}/memory` | Erase the account's memory, or one opportunity's (`?opportunity_id=`) |
| GET | `/configs` | Checklist config profiles (`?include_archived=true`); the built-in one first |
| GET | `/configs/schema` | JSON Schema of a profile, for editors |
| POST | `/configs/validate` | Check a profile without saving |
| POST | `/configs` | Create a profile, or clone one with `{"from_id": "..."}` |
| GET | `/configs/{id}` | A profile (`?version=` for an older one) |
| PUT | `/configs/{id}` | Save a new version (needs `expected_version`) |
| DELETE | `/configs/{id}` | Archive a profile |
| GET | `/configs/{id}/versions` | Its versions, newest first |

### GET /options

```json
{
  "assist_config": {
    "defaults": {"scope": "checklist", "frequency": "balanced", "max_suggestions": 1, "instructions": ""},
    "scope": ["checklist", "guided", "open"],
    "frequency": ["conservative", "balanced", "proactive"],
    "max_suggestions": {"min": 1, "max": 3},
    "instructions_max_chars": 2000
  },
  "sources": {
    "manual": {},
    "recording": {"speed": {"min_exclusive": 0, "max": 50, "default": 10},
                  "max_gap_seconds": {"min_exclusive": 0, "max": 60, "default": 8}},
    "audio": {"speed": {"min": 0.5, "max": 2.0, "default": 1},
              "limit_seconds": {"min_exclusive": 0, "default": null},
              "upload": "proxy"}   // "direct": the browser PUTs to the presigned URL itself (bucket CORS allows its origin)
  },
  "topics": [{"field_id": "business_goal", "label": "Business goal or driver", "category": "Business",
              "required": true, "description": "..."}]
}
```

`assist_config` meaning:

| Field | Values |
|---|---|
| `scope` | `checklist`: only open checklist topics. `guided`: checklist first, plus follow-ups on what was just said. `open`: any discovery question |
| `frequency` | `conservative`: only at clear openings. `balanced`: whenever there is substance. `proactive`: every run except small talk |
| `max_suggestions` | Questions per agent run, 1-3 |
| `instructions` | Free text added to the agent's prompt |

### POST /uploads

Recording (one conversation JSON file):

```json
{"kind": "recording"}
```

Audio (a manifest from `make_audio.py` plus its speaker tracks; list every file
you will upload, using the paths the manifest refers to):

```json
{"kind": "audio", "files": ["manifest.json", "speakers/ravi_menon.pcm", "speakers/helen_brooks.pcm"]}
```

`201`:

```json
{
  "upload_id": "9f1c2b0e4d6a4c8e9a7b5d3f1e2c4a6b",
  "kind": "audio",
  "expires_in": 900,
  "files": [
    {"path": "manifest.json", "method": "PUT", "url": "https://...amazonaws.com/uploads/audio/9f1c.../manifest.json?X-Amz-..."}
  ]
}
```

Upload each file with a plain `PUT` of the raw bytes to its `url` within 15
minutes, then start the call with the `upload_id`. Paths: up to 20 files, letters,
digits, `.`, `_`, `-`, and up to three `/` levels. Uploads are deleted after 180 days.

File formats:
- Recording: `{"conversation": [{"timestamp": "...", "speaker": "ba"|"cx", "name": "...", "text": "..."}]}`
- Audio: `manifest.json` with `audio: {encoding: "pcm_s16le", sample_rate: 16000|8000, channels: 1}`,
  `speakers: [{speaker_id, name, role: "ba"|"cx", pcm: "<path>"}]`, and optionally `turns`
  with `start_sec`/`end_sec`/`speaker_id` (used to cut the audio in silences and skip
  silent speakers). Each `.pcm` is raw 16-bit little-endian mono, one track per speaker.

### POST /calls

```json
{
  "title": "Pharmacovigilance discovery",
  "assist_config": {"scope": "guided", "frequency": "balanced", "max_suggestions": 2},
  "source": {"type": "audio", "upload_id": "9f1c...", "speed": 1, "limit_seconds": 300}
}
```

| `source.type` | Other fields |
|---|---|
| `manual` (default) | none. Add lines with `POST /calls/{id}/turns` |
| `recording` | `upload_id`, `speed` (default 10), `max_gap_seconds` (default 8) |
| `audio` | `upload_id`, `speed` 0.5-2 (default 1), `limit_seconds` (optional) |
| `meeting` | `meeting_url`, `ba_names`, `bot_name` (optional). See [Live meetings](#live-meetings) |
| `transcript` | `text`, `ba_names`, `speed` (default 5, up to 100). See [Transcript calls](#transcript-calls) |

`201`:

```json
{"session_id": "call-4e2b0c...", "status": "live", "source": "audio", "title": "Pharmacovigilance discovery",
 "account_id": null, "opportunity_id": null}
```

Optional `config_id` (from `/configs`, default the built-in `ingram-default`) and
`config_version` (default its current version) pick the checklists. The call keeps a
copy, so later edits to the profile don't change it.

Optional `account_id` and `opportunity_id` (from `/accounts`) link the call to a customer,
which turns on memory across calls. `opportunity_id` needs `account_id`. If the
opportunity has an `opportunity_type`, the call starts with that checklist set and locked.

The call exists immediately. For recording and audio, turns start arriving in
the view within seconds; audio turns appear a few seconds after they are spoken.
When the recording finishes, the call ends by itself. If the source fails, the
call ends with `end_reason` starting `source failed`.

### Live meetings

`source.type = "meeting"` sends a Vexa bot to a Google Meet, Microsoft Teams or Zoom meeting. Its sentences
reach the agent as they are spoken, so suggestions appear while the meeting runs.

```json
{"title": "Acme discovery",
 "source": {"type": "meeting", "meeting_url": "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34",
            "vexa_api_key": "vxa_…", "vexa_tx_key": "vxa_tx_…",
            "ba_names": "Ravi Menon", "bot_name": "Ingram Live Assist"}}
```

- `meeting_url`: https links on `meet.google.com`, `teams.microsoft.com`, `teams.live.com` or `zoom.us`
  (Teams needs the short `/meet/<number>` form). Google Meet `/meet/<code>` links are accepted.
- `vexa_api_key` (required) and `vexa_tx_key` (optional): the caller's own Vexa keys. They are forwarded once to the
  agent runtime in a `meeting.join` event and kept in its memory for the meeting. They are never stored, logged
  or returned. `vexa_tx_key` is only for a key that cannot read transcripts (Vexa keys have scopes; reading needs `tx`).
- `ba_names`: the partner's own name(s) as shown in the meeting, a list or comma-separated text. Anyone else
  is the customer, and so is a speaker the service could not name. Matching ignores case, word order and
  `(Partner)`-style suffixes; a single first name matches only itself.
- The bot joins as a guest. The host must admit it from the lobby, and meetings that allow only signed-in
  users refuse guests. `GET /calls/{id}` then returns `meeting: {"platform", "bot"}`, where `bot` is
  `requested`, `joining`, `awaiting_admission`, `active`, `stopping`, `completed` or `failed`.
- A call follows one meeting. The call ends when the meeting does, or when you
  `POST /calls/{id}/end`, which also removes the bot. Typed lines (`/turns`) are refused (`source_mismatch`).
- Pausing does not apply to live meetings.

Errors: `invalid_request` (400: a missing or malformed key, never echoed), `invalid_meeting_link` (400),
`meeting_busy` (409: a bot is already in the meeting), `meeting_limit` (429), `meeting_not_authorized` (502: Vexa
refused the key, or it cannot read transcripts), `meeting_unavailable` (502), `meeting_not_configured` (502).
When the agent refuses the join, the call that was just created is ended, so it does not stay live without a source.

The source runs inside the agent runtime session that owns the call, so the API works the same on Lambda.

#### With Recall.ai (`MEETING_PROVIDER=recall`)

`GET /options` then reports `sources.meeting.provider = "recall"` and `needs_vexa_key = false`. The request is the
same without the two Vexa keys (any that are sent are ignored and never stored or forwarded). Instead of the runtime
holding a connection, the API Lambda sends a Recall bot and Recall pushes signed webhooks back to it:

| Route | Sent by | What it does |
|---|---|---|
| `POST /recall/transcript` | Recall, once per transcript utterance (set per bot when it is created) | Turns the utterance into a `turn` event for the call. The session id and `ba_names` come back in the bot's `metadata`. |
| `POST /recall/status` | Recall, for `bot.status_change` (set once in the Recall dashboard) | `call_ended` or `fatal` ends the call, so the minutes are written. |

- These two routes take no `X-Access-Key`. Every request is verified instead: HMAC-SHA256 over
  `webhook-id.webhook-timestamp.body` with the workspace verification secret (the `svix-*` header names also work),
  compared in constant time, and refused when older than five minutes. A bad or missing signature is `401 invalid_signature`.
- A turn for a call that has ended makes the bot leave (`leave_call`). A turn for an unknown call is acknowledged
  (200) and dropped, so Recall does not resend it. If the agent is unreachable the route answers 502, so Recall resends.
- The call ends when the meeting ends only if the status webhook is configured in the Recall dashboard. Otherwise end
  it with `POST /calls/{id}/end`; the bot leaves when its next utterance arrives.
- `GET /calls/{id}` returns `meeting: null` for Recall calls (the bot's lobby state is not tracked).
- Recall errors use the same codes as above: `meeting_not_authorized`, `meeting_limit`, `invalid_meeting_link`,
  `meeting_unavailable` (including Recall's 507 "no bot available"), `meeting_not_configured` (a Recall secret is missing).
- Settings: `RECALL_REGION`, `PUBLIC_API_URL`, `RECALL_API_KEY_SECRET`, `RECALL_VERIFY_SECRET_NAME`,
  `RECALL_STREAMING_MODE` (default `prioritize_low_latency`, English only), `RECALL_LANGUAGE_CODE`,
  `RECALL_SEPARATE_STREAMS` (see `.env.example`).

**Timing (Recall).** In low-latency mode Recall documents about 1 to 3 seconds from an utterance being finalised.
Webhooks reach the agent one after another, so the API answers each one quickly. After `call_ended` the API waits
a few seconds before ending the call so the last sentences still arrive.

**Timing (Vexa).** Vexa confirms a sentence some time after it is spoken. A draft that stays unchanged for
`VEXA_PROMOTE_AFTER_SECONDS` (default 5, the speaker paused) is used without waiting. If Vexa later withdraws
a sentence the agent already used, the stored turn is kept (turns are immutable) and the retraction is logged.

#### Meeting title, description and pre-questions

A live meeting call can carry what the BA knows about the meeting, and the agent turns it into questions to prepare:

```json
{"title": "Acme data centre migration",
 "description": "Lease ends in May. About 120 VMs, mostly Windows. Finance wants a budget figure by quarter end.",
 "source": {"type": "meeting", "meeting_url": "https://meet.google.com/abc-defg-hij", "ba_names": "Ravi Menon"}}
```

- `title` (existing) and `description` (new, optional text, at most 2,000 characters, line breaks kept). A longer or
  non-text description is `invalid_request` (400), and no call is created.
- When the request has a `title` or a `description`, the agent writes about 6 questions in the background as the call
  starts: tailored to the meeting and tied to checklist topics. It does not delay the call. Without either, none are written.
  Other source types ignore this.
- `GET /calls/{id}` returns `description` and `prequestions`: `{"status": "none" | "generating" | "ok" | "error",
  "questions": [{"question", "topic_id", "topic_label", "why"}], "generated_at", "duration_ms", "model_id"}`.
  `none` means none were asked for. The questions are available a few seconds after the call is created, even while the
  bot is still joining, and a failure (`error`) does not affect the live suggestions.
- The description is text from a person. It is length-capped, stripped of control characters, and given to the model
  as information to read, never as instructions. Do not put secrets in it.

### Transcript calls

`source.type = "transcript"` plays pasted text into the call at speaking pace, like a recording, so the
assist builds up as it would have live.

```json
{"source": {"type": "transcript", "text": "Ravi Menon: Tell me about the project.\nHelen Brooks: We run on-prem.",
            "ba_names": "Ravi Menon", "speed": 5}}
```

Accepted text: `Name: text` lines (optionally led by `[mm:ss]`), WebVTT and SRT as exported by Teams and Zoom
(`<v Name>` voice tags or `Name:` prefixes), or the `{"conversation": [...]}` JSON used by recordings. Lines with
no speaker are the customer. When the text has no times they are estimated from the amount of speech.
`400 invalid_upload` explains what is wrong (empty, over 60,000 characters, no spoken lines).

### GET /calls

Query: `limit` (1-100, default 20), `cursor` (from the previous page).

```json
{
  "calls": [{"session_id": "call-...", "title": "...", "status": "live", "source": "transcribe",
             "created_at": "...", "ended_at": null, "turn_count": 42}],
  "next_cursor": "eyJzZXNz..."
}
```

`source` here is the stored source: `manual`, `recording` or `transcribe` (audio).

### GET /calls/{id}

```json
{
  "session_id": "call-...",
  "status": "live",                  // live | paused | ended
  "title": "...",
  "source": "manual",                // manual | recording | transcribe
  "accepts_manual_turns": true,      // show the "add a line" box only when true
  "assist_config": {"scope": "guided", "frequency": "balanced", "max_suggestions": 2, "instructions": ""},
  "created_at": "...", "ended_at": null, "end_reason": null,
  "processing": {"latest_seq": 12, "last_processed_seq": 10, "pending_turns": 2, "run_count": 5, "busy": true},
  "turns": [
    {"seq": 1, "speaker": "ba", "speaker_name": "Ravi Menon (Partner)", "text": "...",
     "spoken_at": "...", "received_at": "...", "source": "transcribe", "event_id": "...",
     "processed": true, "run_first_seq": 1}
  ],
  "runs": [
    {"first_seq": 1, "last_seq": 3, "turn_count": 3, "status": "ok", "run_number": 1,
     "started_at": "...", "duration_ms": 8100,
     "narration": ["Captured the business goal..."],
     "suggestions": [{"question": "...", "target_field_id": "desired_outcomes",
                      "target_label": "Desired outcome and success criteria", "rationale": "..."}],
     "captured_fields": ["business_goal"], "added_gaps": [], "dismissed_gaps": [],
     "opportunity_type": null, "opportunity_type_just_resolved": false,
     "settings": {"scope": "guided", "frequency": "balanced", "max_suggestions": 2, "instructions": ""},
     "error": null}
  ],
  "suggestions": [
    {"run_number": 1, "question": "...", "target_field_id": "desired_outcomes",
     "target_label": "...", "rationale": "...", "trigger_seq": 3, "created_at": "..."}
  ],
  "meeting": null,                   // live meeting calls: {"platform", "bot"}, written by the agent as the bot progresses
  "state": {
    "problem_summary": "...",
    "opportunity_type": null,
    "open_gaps": [{"field_id": "scope", "label": "In-scope and out-of-scope workloads", "description": "...",
                   "required": true, "category": "Technical", "source": "guidance"}],
    "captured": [{"field_id": "business_goal", "label": "Business goal or driver", "value": "...",
                  "speaker": "cx", "category": "Business", "updated_at": "...",
                  "evidence": [2, 5]}],
    "dismissed": [{"field_id": "budget_range", "label": "Budget range", "reason": "..."}]
  },
  "minutes": {"status": "none"},    // see GET /calls/{id}/minutes
  "account_id": null, "opportunity_id": null,
  "config_id": "ingram-default", "config_version": 0,   // checklists the call runs with
  "prior_context": null              // calls with an account: what the agent was told from earlier calls
}
```

Notes for the UI:
- Group turns under their run with `turns[].run_first_seq` = `runs[].first_seq`.
  Turns with `run_first_seq: null` are not processed yet.
- A run with `status: "error"` failed; it retries on the next turn.
- `suggestions` (top level) is every suggestion so far, oldest first. `runs[].suggestions`
  are the ones from that run. `target_label: null` means a follow-up question.
- Turns are processed in batches, so several turns can share one run.
- `prior_context` is `{"account": [...], "opportunity": [...], "previous_calls": [...]}`,
  loaded on the call's first agent run. Show it apart from this call's captures.
- `runs[].memory_lookups` lists the agent's memory searches in that run:
  `{"query", "level", "hits", "ok", "ms"}`.
- `captured[].evidence` and `suggestions[].trigger_seq` are turn `seq` numbers (citations).
  Older calls may have empty `evidence` or `trigger_seq: null`.

### POST /calls/{id}/turns

```json
{"speaker": "cx", "text": "We run everything on-prem today.", "speaker_name": "Helen"}
```

`speaker` is `ba` or `cx`; `speaker_name` is optional. `202 {"seq": 13}`.
Only for calls with `accepts_manual_turns: true`; otherwise `409 source_mismatch`.

### PATCH /calls/{id}

Any of:

```json
{"status": "paused", "assist_config": {"scope": "open", "max_suggestions": 3}}
```

`status` is `live` or `paused` (pausing stops a recording or audio source from
advancing; use `/end` to end). New `assist_config` applies from the next agent
run. `200 {"status": "paused", "assist_config": {...}}`.

### POST /calls/{id}/end

Body optional: `{"reason": "Customer had to drop"}`. `200 {"status": "ended", "ended_now": true}`
(`ended_now: false` if it was already ended). Pending turns are still processed,
then the minutes are written; keep polling until `processing.busy` is `false` and
`minutes.status` is final.

### GET /calls/{id}/handoff

```json
{
  "session_id": "...", "title": "...", "status": "ended", "created_at": "...", "ended_at": "...",
  "generated_at": "...",
  "complete": true,                  // ended and every turn processed
  "problem_summary": "...", "opportunity_type": null,
  "captured": [...], "open_gaps": [...], "dismissed": [...],
  "suggested_questions": ["..."],
  "turn_count": 42,
  "summary": "...",                  // final call summary, null until minutes are ok
  "minutes": {...}                   // same as GET /calls/{id}/minutes, without call_status
}
```

### GET /calls/{id}/minutes

Written once after the call ends and its last turns are processed.

```json
{
  "session_id": "...", "call_status": "ended",
  "status": "ok",                    // none | generating | ok | empty (no turns) | error
  "generated_at": "...", "started_at": "...", "duration_ms": 9400,
  "model_id": "...", "prompt_version": "minutes-v1",
  "turn_count": 42, "transcript_last_seq": 42, "windows": 1,
  "error_code": null,                // timeout | not_submitted | model_error when status is error
  "minutes": {
    "summary": "1-3 paragraphs",
    "attendees": [{"speaker": "cx", "name": "Helen"}],
    "key_points":     [{"text": "...", "citations": [4, 7], "uncited": false}],
    "decisions":      [{"text": "...", "citations": [12], "uncited": false}],
    "action_items":   [{"text": "...", "owner": "cx", "due": "by Friday", "citations": [30], "uncited": false}],
    "risks":          [{"text": "...", "citations": [], "uncited": true}],
    "open_questions": [{"text": "...", "field_id": "target_region", "citations": [18], "uncited": false}],
    "next_steps":     [{"text": "...", "citations": [41], "uncited": false}]
  }
}
```

- `citations` are turn `seq` numbers; each one is checked against the call's turns and
  unknown ones are dropped. `uncited: true` means no valid citation was left.
- `due` is the deadline as said in the call, not a parsed date.
- `?format=md` returns `text/markdown` with `[#seq]` citations; `409 minutes_not_ready`
  until `status` is `ok`.

### POST /calls/{id}/minutes

No body. Regenerates the minutes of an ended call (for example after `status: error`).
`202 {"minutes_status": "queued"}`; poll `GET /calls/{id}` for `minutes.status`.
`409 session_live` if the call hasn't ended.

### Config profiles

TODO(auth): with the shared key, every key holder can edit profiles, and profile text
goes into the agent's prompt.

A profile is the full checklist set: `{name, description, general: {label, description,
fields}, opportunity_types: [{id, label, description, fields}]}`. Each field:
`{id, label, description, required, category, ask_when, examples, memory_scope}`;
`memory_scope` is `account` (a fact about the company, shared by its opportunities) or
`opportunity` (default). `GET /configs/schema` has every rule; the main ones:

- Field and type ids: lowercase letters, digits and `_`, 2-64 characters, starting with a letter.
- Ids are unique within a checklist. A specialised type may reuse a general field id to refine it.
- Up to 50 fields per checklist, 30 types, 60 KB in total. Text is flattened to one line.

`GET /configs/{id}` returns `{config_id, name, description, builtin, current_version,
archived, created_at, updated_at, version, version_created_at, body}`. The built-in
profile is `ingram-default` (version 0, read-only; clone it).

`PUT /configs/{id}` takes the full profile plus `expected_version` (the version you
loaded) and saves `current_version + 1`. If someone saved in between: `409 version_conflict`.
Archiving hides a profile and stops new calls from using it; past calls keep their copy.

`GET /options?config_id=` returns that profile's `topics` and `opportunity_types`.

### Accounts and opportunities

TODO(auth): with the shared key, every key holder can read and erase every account's memory.

`POST /accounts` `{"name": "Acme Corp"}` -> `201 {"account_id": "acme-corp-1a2b3c", "name": "Acme Corp", "created_at": "..."}`.
Ids are generated from the name. `GET /accounts` takes `limit` and `cursor` like `GET /calls`.

`POST /accounts/{id}/opportunities` `{"name": "ERP move", "opportunity_type": "cloud_migration"}`
(`opportunity_type` optional, a type id) -> `201` with `opportunity_id`. When a call
starts, the type must exist in the call's config, or `400 opportunity_type_not_in_config`.

`GET /accounts/{id}/calls` returns the same rows as `GET /calls`.

### GET /accounts/{id}/memory

```json
{
  "enabled": true,                   // false: long-term memory is off; previous_calls still work
  "account_id": "acme-corp-1a2b3c", "opportunity_id": "erp-move-4d5e6f",
  "account":     [{"level": "account", "text": "Acme runs VMware in Dallas", "created_at": "..."}],
  "opportunity": [{"level": "opportunity", "text": "The ERP move must finish by June", "created_at": "..."}],
  "previous_calls": [{"session_id": "...", "title": "...", "ended_at": "...", "summary": "...",
                      "decisions": ["..."], "open_questions": ["..."]}]
}
```

Facts come from each ended call's minutes. They appear a minute or two after the
minutes, and are kept for 180 days. `previous_calls` are the minutes of the same
opportunity's earlier calls (or the account's, without `opportunity_id`).

### DELETE /accounts/{id}/memory

Forget: erases remembered facts for the whole account (its own and all its
opportunities') or one opportunity (`?opportunity_id=`), and records `forgotten_at`.
New calls then stop using earlier calls that ended before that time. Past calls keep
their own transcript, minutes and `prior_context` as call history (until they expire); calls already
running keep what they loaded. Works without long-term memory too (`deleted: 0`).
`200 {"deleted": 12, "account_id": "...", "opportunity_id": null, "forgotten_at": "..."}`.
`GET .../memory` also returns `forgotten_at`.

## Limits

| Limit | Value |
|---|---|
| Request body | 64 KB (256 KB for `POST /calls`, which can carry a transcript) |
| Transcript text | 60,000 characters, 1,500 turns |
| Upload URL lifetime | 15 minutes |
| Recording file | 5 MB |
| Audio speed | 0.5-2x real time |
| Turn text | 10,000 characters |
| Title | 200 characters |
| Instructions | 2,000 characters |
| Throttling | 20 requests/s, burst 40 (per deployment config) |
| Call data retention | 180 days (`DATA_RETENTION_DAYS`) |
| Remembered facts | 180 days |
| Accounts and opportunities | Kept until deleted (names only, no call content) |

## Local development

Run the agent and the API on your machine against real DynamoDB, S3 and Bedrock:

```bash
python main.py                                                  # agent on :8080
ACCESS_KEY=dev UPLOAD_BUCKET=<bucket> python -m api.local      # API on :8090
curl -H 'X-Access-Key: dev' http://127.0.0.1:8090/options
```

Locally, recording and audio sources run in a background thread instead of Lambda.

## Intake opportunities

Funding-checked opportunities from the Intake Agent (table `INTAKE_TABLE_NAME`, read-only) and the call history map.
Off (`enabled: false`) when `INTAKE_TABLE_NAME` is empty.

| Method | Path | Purpose |
|---|---|---|
| GET | `/intake/opportunities` | Checked opportunities, newest check first: decision, `call_count`, `last_call_at` |
| GET | `/intake/opportunities/{id}` | One: decision path, open questions, facts used, `link`, `calls` |
| POST | `/intake/opportunities/{id}/link` | The Live Assist `account_id`/`opportunity_id` to start a call with (created once) |
| POST | `/intake/opportunities/{id}/calls` | `{"session_id"}`: add a call started for that opportunity to its history (idempotent) |
| GET | `/intake/opportunities/{id}/calls` | The call history map, each with its current status |

Errors: `intake_not_configured`, `intake_opportunity_not_found` (404), `call_not_found` (404), `call_not_linked` (409:
the call was not started for this opportunity), `intake_unavailable` (502).

### Discovery agent (two-way)
`DISCOVERY_API_URL` / `DISCOVERY_UI_URL` turn it on. `GET /intake/opportunities/{id}/export` returns the documents the
Discovery agent imports (Intake record, ended calls' transcripts and minutes). The Discovery API registers its run with
`POST /intake/opportunities/{id}/discovery {"engagement", "run_id", "call_ids"}`. `GET /intake/opportunities/{id}/discovery`
lists the runs with live status; a finished run's summary (use cases, hidden opportunities, open questions) is read
from the Discovery API's `GET /runs/{id}/report` once (executive summary, use cases, hidden opportunities, pain points,
open questions) and cached on the item `intake#<id>` / `discovery#<started_at>#<run_id>`. Summaries cached before this
field set existed keep the old shape until the run is re-run.
The export also carries `context` for the Discovery page: `header` (customer, seller, opportunity size and its basis,
CRM link, AWS program and funding status), `participants` (name, side `ingram`/`partner`/`customer`) and `calls` (date,
title, short summary). `seller`, `estimated_value_usd` and `crm_url` are shown only when the Intake record has them;
`CRM_BASE_URL` builds the Dynamics link from the opportunity id. Missing values are omitted, not guessed.
