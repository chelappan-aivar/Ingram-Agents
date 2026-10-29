# Agent

The agent follows a call between a partner Business Analyst (BA) and a customer,
tracks what has been learned against a checklist, and suggests what the BA should
ask next.

Built with Strands Agents on Claude Haiku 4.5 (Bedrock), hosted in AgentCore Runtime.

## How it works

1. **Turns arrive.** Each spoken line is stored as a turn. The agent doesn't run
   per line: a background worker waits about a second, then runs the agent once
   on all pending turns (up to 20).
2. **Prompt is built.** Rules, suggestion settings, the checklist, and the call's
   current state (problem summary, captured and open fields, recent suggestions).
3. **History.** Earlier turns come from AgentCore Memory.
4. **The agent acts through tools**, then the worker saves the results.

| Tool | Does |
|---|---|
| `update_problem_summary` | Keeps a 1-3 sentence summary of the customer's problem |
| `set_opportunity_type` | Adds a specialised checklist (e.g. cloud migration) when the call clearly matches |
| `record_captured_field` | Marks a checklist field as answered, with what was said and the turns (`evidence_seqs`) that say it |
| `dismiss_gap` | Drops an optional field that no longer applies |
| `suggest_next_question` | Suggests a question for the BA, with a short reason |
| `recall_prior_context` | Searches what earlier calls with this customer taught (calls with an account only) |

Each transcript line carries its turn number (`[#17 | time | speaker | name]`), so the
agent can cite turns. Cited turns outside the current batch are dropped.

## Minutes of meeting

When a call ends, the worker processes the last turns (retrying a failed last batch
once), then writes the minutes (`app/minutes.py`):

- One model pass over the whole transcript with the call's captured fields and open
  gaps as context. The model answers through one tool, `submit_minutes`, validated
  with pydantic; invalid submissions are sent back for correction.
- Every item cites turn seqs. Citations of turns that don't exist are dropped and
  items left without one are flagged `uncited`.
- Calls longer than about 50k tokens are summarised in windows, then merged.
- Each pass has a timeout and one retry. Failures store an error code only.
- Regenerate with `POST /calls/{id}/minutes` (event `session.summarize`).

## Memory across calls

Calls linked to an account (and optionally an opportunity) share what was learned,
in a long-term AgentCore Memory separate from the conversation history:

- **Two levels.** Company-wide facts (actor `acct-<account>`) and facts about one deal
  (actor `opp-<account>--<opportunity>`). A call reads only its own account and
  opportunity; the agent can't choose another.
- **Write.** When a call's minutes are ready, the worker writes curated notes per level
  (`app/call_memory.py`). Environment, compliance, data volumes, region and people go to
  the account; everything else to the opportunity (or the account, without one). A
  semantic strategy extracts facts from the notes. Live turns are never extracted.
- **Read at start.** On the first run the agent gets the remembered facts plus the
  minutes of earlier calls (read from DynamoDB, so there's no extraction lag), in a
  section marked "not said in this call".
- **Look up during the call.** `recall_prior_context(query, level)` searches the
  facts. At most 2 lookups per run with short timeouts; if memory is down the run
  carries on. Each lookup is listed in the run record.
- **Never captured.** Remembered facts can't mark a field captured; the agent confirms
  them (`use_prior_context: confirm`, default) or skips the topic (`skip`). `off` turns
  memory off for the call.
- **Notes are facts only.** No call title or date header, and no open questions, so
  extraction doesn't produce facts like "the user had a call".
- **Retention.** Facts are kept for 180 days (`DATA_RETENTION_DAYS`), like call data.
- **Forget** (`DELETE /accounts/{id}/memory`) erases the facts and stops earlier calls'
  minutes from feeding new calls. Past calls keep their own record.

## Checklists and config profiles

Each call runs with one config profile: a general checklist plus specialised
opportunity types layered on top. The built-in profile, `ingram-default`, is read
from `config/guidance/` and is read-only. Its general checklist holds nine topics:
business goal, desired outcome, scope, current hosting, data volumes, security and
compliance, target AWS region, timeline, and budget (optional).

Profiles are created and edited in the UI (clone the built-in one to start) and saved
as versions. A call copies its profile's checklists when it starts, so editing a
profile never changes a running or past call. Each field can carry timing hints,
example questions, and a `memory_scope` (`account` or `opportunity`) that decides
which memory level its captured value goes to.

Profile text goes into the agent's prompt, so it is flattened to single lines and
length-capped by the schema (`app/config_schema.py`).

## Suggestion settings

Set per call, changeable during the call.

| Setting | Options |
|---|---|
| Scope | `checklist` (open topics only), `guided` (plus follow-ups), `open` (any discovery question) |
| Frequency | `conservative`, `balanced`, `proactive` |
| Questions per run | 1-3 |
| Instructions | Free text added to the prompt |

## Guardrails (enforced in code)

- Questions can only target allowed, still-open topics.
- A question already asked recently is rejected.
- Suggestions are cleaned of em dashes and curly quotes.
- Only what was actually said is captured; call text is kept out of logs.
