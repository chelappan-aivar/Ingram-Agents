# Live Assist on AWS (Terraform)

Deploys the Live Assist agent, API and console. A meeting link entered in the console starts a call, and the
call's Amazon Bedrock AgentCore Runtime session sends a Vexa bot into the meeting and follows it.

```
 Browser ── https ──► AWS Amplify (console files, security headers)
    │
    │  https + X-Access-Key (typed by the user; CORS allows only the Amplify origin)
    ▼
 API Gateway ──► Lambda "api"
                    │ session.start, then meeting.join
                    ▼
                                          AgentCore Runtime session = one call
                                          ├─ sends the Vexa bot, follows the meeting (keys in memory only)
                                          ├─ agent → Bedrock (Claude)
                                          └─ DynamoDB (sessions, timeline) · AgentCore Memory
 Pasted transcript / audio file ──► Lambda "replayer" ──► the same runtime session
                                    (audio: S3 upload → Amazon Transcribe)
```

## Meeting provider: Recall.ai or Vexa

`meeting_provider` (in `environments/<env>.tfvars`) picks who sends the bot into the meeting. Default `vexa`;
`dev.tfvars` sets `recall`. Setting it back to `vexa` is the rollback.

**Recall.ai (`recall`).** The API Lambda sends the bot and receives Recall's signed webhooks at
`<api_endpoint_direct>/recall/transcript`. The runtime holds nothing during the meeting. Set it up once per
environment:

1. In the Recall dashboard (Developers, API Keys & Secrets) create an API key and a **workspace verification secret**
   (`whsec_...`) in the region set by `recall_region`.
2. Apply, then put both values in the secrets Terraform created. They are never in Terraform, state or variables:
   ```bash
   aws secretsmanager put-secret-value --secret-id "$(terraform output -raw recall_api_key_secret_name)" --secret-string '<Recall API key>'
   aws secretsmanager put-secret-value --secret-id "$(terraform output -raw recall_verify_secret_name)" --secret-string 'whsec_...'
   ```
3. In the Recall dashboard (Webhooks) add the URL from `terraform output recall_status_webhook_url` and subscribe it to
   `bot.status_change`, so a call ends when its meeting ends. Without it, end calls from the console.
4. The Lambda caches the secrets for five minutes, so allow that long after changing them.

Until step 2 is done the Meeting source shows as unavailable (`meeting_not_configured`); transcripts and audio files
are not affected. The console shows no key fields for Recall.

**Vexa (`vexa`).** The Vexa API key (and the optional transcription key) are typed in the console's Meeting card for
each call. They go to the API, then to the runtime session in one `meeting.join` event, and live in that session's
memory until the meeting ends. They are not in Terraform, state, variables, Secrets Manager, environment variables,
DynamoDB or logs. To use a different Vexa account, type a different key.

## What it creates

| File | Resources |
|---|---|
| `kms.tf` | One customer-managed key (rotation on) for call data |
| `ecr.tf` | Repository for the agent image (immutable tags, scan on push) |
| `dynamodb.tf` | `sessions` and `timeline` tables (TTL, point-in-time recovery, the two indexes) |
| `agentcore.tf` | Conversation memory, long-term memory with the `CallFacts` strategy, the runtime |
| `iam_runtime.tf`, `iam_lambda.tf` | Least-privilege roles (the runtime role must contain `BedrockAgentCore`) |
| `lambda.tf`, `apigw.tf` | `api` and `replayer` functions, HTTP API with throttling and access logs |
| `s3_uploads.tf` | Audio upload bucket, only if `enable_audio_source` |
| `amplify.tf` | Amplify app and branch for the console, with security headers (files uploaded by `deploy.sh`) |
| `s3_logs.tf`, `secrets.tf` | Access-log bucket; the console access key (generated); two empty secrets for the Recall key and verification secret |

Everything is tagged `owner`, `project`, `cost-center`, `team` (the organisation's conftest policy).

## Deploy

```bash
cd Terraform
# 1. Edit environments/dev.tfvars: owner, team and cost_center.
# 2. Remote state (recommended; the state holds the generated access key):
export TF_STATE_BUCKET=my-terraform-state      # existing, versioned, encrypted bucket
# 3. Check offline, then deploy:
./deploy.sh dev check
./deploy.sh dev plan
./deploy.sh dev up          # prints the console address when done
```

`up` is safe to re-run. It creates the image registry first (the runtime needs an image to start), builds and
pushes the agent image (`linux/arm64`), applies, then sets the long-term memory expiry. `deploy.sh` writes the
new image tag into `environments/<env>.tfvars`: commit that change, so the pipeline deploys the same image.

| Command | Use |
|---|---|
| `check` | Offline: `terraform fmt`, `validate`, `test`, and tflint/tfsec/checkov if installed |
| `plan` / `up` | See / make the change. Every apply asks for approval |
| `image` | New agent code: build, push, point the runtime at it |
| `update` | New API/replayer code or console files |
| `console` | Upload only the console files to Amplify (needs `zip` and `curl`) |
| `retention` | Re-apply the memory expiry |
| `down` | Destroy (asks you to type the environment name) |

Requirements: Terraform 1.7+ (1.10+ for remote state), AWS CLI v2, jq, Python 3.11+ (the project venv
`src/.venv` is used when it exists), Docker able to build arm64, Bedrock model access enabled for `model_id`,
and credentials that can create these resources.

## Using it

Open `console_url`. Paste the access key into the **Access key** field at the top of the form (it is kept for that
browser tab only, and checked as soon as you paste it). Read it with
`aws secretsmanager get-secret-value --secret-id <access_key_secret_name> --query SecretString --output text`.
With `vexa`, enter your Vexa key; with `recall`, there is nothing to enter. Paste a Teams, Google Meet or Zoom link,
and start. Admit the bot from the lobby. Pasted transcripts and audio files need no meeting-provider key.

## Notes and limits

- **Access control.** There is no IP allow-list: anyone with the Amplify address and the shared access key can use
  the console, and the API rejects every call without the key. The key is typed by the user and kept in the
  browser's `sessionStorage` for that tab only. This is not per-user sign-in: Cognito/SSO is the planned
  replacement. Rotate the key with `terraform apply -replace=random_password.access_key`.
- **Runtime network.** The runtime uses `PUBLIC` networking so it can reach Vexa and Bedrock. Check your
  organisation's egress policy before deploying.
- **Session length.** A call is one runtime session, so `runtime_max_lifetime_seconds` (default 8 h) caps a
  meeting. A session reports busy while it follows a meeting, so the idle timeout does not end it.
- **Long-term memory expiry.** The Terraform resource has no expiry setting. `deploy.sh up` and `retention` set it
  with `src/deploy/ensure_ltm_memory.py`. A pipeline-only apply creates the memory without that expiry, so run
  `./deploy.sh <env> retention` after it.
- **Pipeline.** `configuration.json` must have the OIDC role, region, account id and `var_file` filled in before
  the CI plan/apply can run. A pipeline apply with an empty `agent_image_tag` deploys everything except the
  runtime. Merging to `main` applies automatically (`push-apply`).
- **Not deployed from here.** This was validated offline (`terraform validate`, mocked-provider tests, tflint,
  tfsec, Checkov). It has not been applied to a real account.
