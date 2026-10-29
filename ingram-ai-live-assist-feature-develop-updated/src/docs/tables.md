# Table schemas

Both tables are DynamoDB, pay per request.

## `<prefix>-timeline`

Everything that happens in a call. Key: `session_id` (partition) + `sk` (sort).
Call items expire after `DATA_RETENTION_DAYS` (180 by default; `expires_at`, TTL).

**Call details** (`sk = "meta"`)

| Field | Description |
|---|---|
| `status` | `live`, `paused` or `ended` |
| `source` | `manual`, `recording` or `transcribe` |
| `exclusive_source` | Set for recording and audio calls; other senders are rejected |
| `title` | Call title |
| `assist_config` | Suggestion settings |
| `next_seq` | Number of turns stored |
| `created_at`, `updated_at`, `ended_at`, `end_reason` | Timestamps and why it ended |
| `gsi1pk` | Always `"call"`, for the calls list index |
| `account_id`, `opportunity_id` | Customer the call belongs to, if any |
| `gsi2pk` | `"account#<account_id>"`, for the calls-by-account index |
| `preset_opportunity_type` | Checklist set from the opportunity at start |
| `config_id`, `config_version` | Config profile the call runs with (`ingram-default`, 0 for the built-in one) |
| `guidance` | Copy of a custom profile's checklists, taken at start (absent for the built-in one) |

**Turn** (`sk = "turn#000017"`)

| Field | Description |
|---|---|
| `seq` | Turn number, in arrival order |
| `speaker` | `ba` or `cx` |
| `speaker_name`, `text` | Who said it and what |
| `spoken_at`, `received_at` | When it was said and stored |
| `source`, `event_id` | Where it came from |

**Agent run** (`sk = "run#000017"`, keyed by the first turn it processed)

| Field | Description |
|---|---|
| `first_seq`, `last_seq`, `turn_count` | Turns covered |
| `status` | `ok` or `error` |
| `run_number`, `started_at`, `duration_ms` | Run info |
| `narration` | The agent's note |
| `suggestions` | Questions suggested in this run |
| `captured_fields`, `dismissed_gaps`, `added_gaps` | What changed |
| `opportunity_type`, `opportunity_type_just_resolved` | Specialised checklist, if set |
| `settings` | Suggestion settings used |
| `error` | Failure reason, if any |

**Minutes** (`sk = "minutes"`, written after the call ends)

| Field | Description |
|---|---|
| `status` | `generating`, `ok`, `empty` (no turns) or `error` |
| `minutes` | Summary, attendees, key points, decisions, action items, risks, open questions, next steps; items carry `citations` (turn seqs) |
| `started_at`, `generated_at`, `duration_ms` | Timing |
| `model_id`, `prompt_version` | What produced it |
| `turn_count`, `transcript_last_seq`, `windows` | Transcript covered; `windows > 1` means a long call was summarised in parts |
| `error_code` | `timeout`, `not_submitted` or `model_error` |
| `memory_written_for` | `generated_at` of the minutes last written to long-term memory |

A `generating` claim older than 15 minutes is treated as a crashed attempt and can be retaken.

**Account** (`session_id = "account#<account_id>"`, `sk = "meta"`, no TTL): `account_id`,
`name`, `created_at`, `gsi1pk = "account"`.

**Opportunity** (`session_id = "account#<account_id>"`, `sk = "opp#<opportunity_id>"`, no TTL):
`opportunity_id`, `account_id`, `name`, `opportunity_type`, `created_at`.

**Config profile** (`session_id = "config#<config_id>"`, `sk = "meta"`, no TTL): `config_id`,
`name`, `description`, `current_version`, `archived`, `created_at`, `updated_at`, `gsi1pk = "config"`.

**Config version** (`session_id = "config#<config_id>"`, `sk = "v#000003"`, no TTL): `version`,
`body` (the profile), `created_at`. Never changed after it is written.

**Index** `calls-by-created`: `gsi1pk` + `created_at`, lists calls (`"call"`), accounts
(`"account"`) and config profiles (`"config"`) newest first.

**Index** `calls-by-account`: `gsi2pk` + `created_at`, lists one account's calls newest first.

## `<prefix>-sessions`

The agent's state per call, one item per call. Key: `session_id`. Written only by
the background worker.

| Field | Description |
|---|---|
| `opportunity_type`, `opportunity_type_locked` | Specialised checklist, if matched |
| `problem_summary` | The agent's summary of the customer's problem |
| `fields` | Map of checklist field id to its state (below) |
| `suggestions` | Every suggestion so far |
| `chunk_count` | Agent runs completed |
| `last_seq` | Last turn the agent processed |
| `prior_context` | Facts and earlier calls loaded from long-term memory on the first run |
| `created_at`, `updated_at` | Timestamps |

Each entry in `fields`: `label`, `description`, `required`, `source` (`guidance`
or `agent`), `order`, `status` (`missing`, `captured` or `not_applicable`),
`value`, `last_speaker`, `note` (dismissal reason), `updated_at`, `evidence` (turn
seqs that hold the value). Suggestions also store `trigger_seq`, the last turn of the
run that prompted them.

**Intake call history map** (timeline table, `app/intake_link_store.py`):
`session_id = "intake#<intake opportunity id>"`, `sk = "link"`: the Live Assist `account_id` and `opportunity_id`,
`call_count`, `last_call_at` (no TTL). `sk = "call#<created_at>#<session_id>"`: one call with its title, source and the
funding decision at that time (`expires_at`). `session_id = "intake#customer#<slug>"`, `sk = "link"`: one Live Assist
account per Intake customer.
