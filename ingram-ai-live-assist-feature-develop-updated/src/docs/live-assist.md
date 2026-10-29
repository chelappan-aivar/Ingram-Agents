# Live Assist for meetings

A partner BA shares a meeting link, an audio recording or a transcript. The agent follows the
conversation, checks it against the discovery checklist and shows what to ask next, while the
conversation is going on. The console never shows the transcript back: it shows guidance.

```
                     ┌─ Meeting link ── Vexa bot joins ── confirmed sentences ─┐
 Console (console/) ─┼─ Transcript ──── pasted text played in at speaking pace ─┼─► turns ─► agent (Bedrock) ─┐
   3 inputs          └─ Audio file ──── browser → 16 kHz PCM → Amazon Transcribe ┘                            │
        ▲                                                                                                     │
        └──────────────── polls GET /calls/{id} every 1.5 s: next question, facts, gaps, minutes ◄────────────┘
```

Every source ends in the same place, `ila.event.v1` turn events. The agent, checklists, memory and
minutes are unchanged. Only the ways in are new.

## Run it

```bash
cd src
scripts/dev_up.sh                 # needs AWS credentials that can call Bedrock
```

Open http://127.0.0.1:8090. `dev_up.sh` starts a local DynamoDB stand-in, creates the two tables, starts
the agent (`python main.py`) and the console and API (`python -m api.local`). Calls are forgotten when it stops.

Source by source:

| Source | Needs | Notes |
|---|---|---|
| Meeting link | a Vexa key, typed in the console | The bot joins as a guest; admit it from the lobby. Meetings limited to signed-in users refuse it. |
| Transcript | nothing extra | `Name: text`, WebVTT or SRT. Tested end to end. |
| Audio file | a deployed environment | Real S3 and Amazon Transcribe. The browser converts the file; the existing audio source transcribes it. Not available with `dev_up.sh`. |

## Deploy on AWS

`../Terraform/` deploys the agent on AgentCore Runtime with the API, the console (AWS Amplify, protected by the shared access key)
and the stores: `Terraform/deploy.sh <env> up`. See `Terraform/README.md`. A meeting link then starts a call, and that
call's runtime session sends the Vexa bot and follows the meeting. The Vexa key is typed in the console, never deployed.

## Who is the BA?

The agent labels every line `ba` or `cx`. Meeting platforms only give display names, so the console asks for
**your name in the conversation**; everyone else is the customer, and so is a speaker the service could not name.
Without your name, everyone counts as the customer and the agent may capture your own words as customer facts.
Teams often gives no name at first; the unnamed lines are then treated as the customer's.

## Vexa keys

Vexa keys have scopes. A **Bot Key** (`vxa_bot_`) starts bots but is refused on transcripts (`403 Insufficient
scope`): the bot joins and nothing is ever heard. The API checks this before sending the bot and says so. Use a key
that has the transcript scope, or type one in the console's "Vexa transcription key" field.
The keys are typed in the console for each call. They travel to the agent in one `meeting.join` event, live in its
memory while the meeting runs, and are never saved to DynamoDB, logs, files or the browser.

## Real-time behaviour

- A turn reaches the agent a moment after Vexa confirms the sentence. Vexa can take a while to confirm, so a draft
  that stays unchanged for `VEXA_PROMOTE_AFTER_SECONDS` (default 5) is used early. Set `0` to wait for confirmation.
- The agent batches turns that arrive together (`PROCESS_DEBOUNCE_SECONDS`, 1 s) and runs once per batch.
- The console polls with `ETag`, so an unchanged call costs a `304`.

## Limits worth knowing

- The meeting source runs inside the agent (the AgentCore Runtime session that owns the call), not in the API. A runtime
  session stays alive while it follows a meeting, up to its maximum lifetime (`max_lifetime`, default 8 hours).
- A retracted sentence the agent already used stays in the call (stored turns are immutable); it is logged.
- Speaker names depend on the meeting platform. Two people talking at once can be mislabeled or unnamed.
- One recording per speaker (customer and partner) gives correct attribution. A single mixed file cannot tell them apart.
- Pausing does not apply to live meetings. Call text is never written to logs.

## Security notes

- The console server listens on 127.0.0.1 and adds the access key itself. `/api` and `/upload` answer only requests
  from the console page: `Host` and `Origin` must be the server's own and a custom header must be present, so another
  web page in your browser, or a DNS-rebinding page, cannot use it.
- Uploads are forwarded only to the upload bucket's presigned URLs. The page loads no scripts or assets from other sites
  and runs under a strict Content-Security-Policy.
- Meeting links are accepted only for known hosts over https. Keys come from the environment and are never logged or sent to the page.
