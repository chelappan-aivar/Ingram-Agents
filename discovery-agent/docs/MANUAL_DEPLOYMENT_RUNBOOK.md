# Manual Deployment Runbook (one resource at a time)
> **Superseded (deployment).** Deployment is now one script: see `../deployment/README.md` (`deployment/scripts/deploy.sh` + `deployment/template.yaml`). The manual CLI/console steps, IAM JSON files, `settings.py` constants and file paths below describe the earlier layout and are kept for reference only. The agent now reads its configuration from environment variables set by the template.


> **Configuration changed: no environment variables.** The agent no longer reads any environment variable. All settings
> (profile, region, model ids, limits, storage) are fixed in `discovery_agent/settings.py`. Where this document tells you to
> set `DISCOVERY_BUCKET`, `DISCOVERY_KMS_KEY_ID`, `DISCOVERY_LOCAL_ROOT`, `AWS_PROFILE_NAME`, `AWS_REGION`, any `*_MODEL_ID`,
> `BEDROCK_READ_TIMEOUT`, `MAX_OUTPUT_TOKENS` or `CORPUS_TOKEN_BUDGET`, **ignore it** and edit `settings.py` instead.
> `S3_BUCKET` and `S3_KMS_KEY_ID` are already set there (copied from `build/runtime_env.json`; existence not verified). The
> runtime uses S3 automatically (no local AWS profile); a development machine uses `samples/_store`. Rebuild the package/image
> after any change to `settings.py`: older artifacts under `build/` still carry the previous environment-variable setup.

Do the steps **in order**. Each step ends with a **Check** and a **Write down** line. Console menu names can differ slightly from what is written here, because AWS changes its screens; the values to enter are exact.

> **Rules for this runbook**
> - Region is **us-east-1** for everything. Check the region selector in the top-right of the console before each step.
> - **Do not upload real customer documents until step 7 (smoke test) has passed.** The smoke test uses a fictional document.
> - Nothing here is deployed yet. I have not run any of these steps against AWS. What I did verify offline is listed at the end.
> - Files referred to below are in this repository: `scripts/iam/*.json`, `scripts/*.py`, `docs/smoke/smoke_call.txt`.

---

## 0. Before you start

You need:
- AWS Console access with permission to create KMS keys, S3 buckets, ECR repositories, IAM roles, Lambda functions, CloudTrail trails and AgentCore runtimes (an administrator role is simplest for a first deployment).
- AWS CLI v2 on your Mac, logged in: `aws sso login --profile <your profile>` then `aws sts get-caller-identity --profile <your profile>`.
- **Docker Desktop running** (open the app and wait until it says it is running). The image must be built for ARM64; your Mac is ARM, so it is native.
- This repository on your Mac, and a terminal opened in its folder. `uv` is installed.

### Values sheet (copy this into a note and fill it in as you go)

| Name | Value | Filled in at |
|---|---|---|
| `AWS_PROFILE` | your profile name | now |
| `REGION` | `us-east-1` | now |
| `ACCOUNT_ID` | 12 digits (from `aws sts get-caller-identity`) | now |
| `BUCKET` | `discovery-agent-<ACCOUNT_ID>-us-east-1` | step 2 |
| `KMS_KEY_ARN` | `arn:aws:kms:us-east-1:...:key/...` | step 1 |
| `IMAGE` | `<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/discovery-agent:v1` | step 3 |
| `ROLE_ARN` | `arn:aws:iam::<ACCOUNT_ID>:role/DiscoveryAgentRuntimeRole` | step 5 |
| `RUNTIME_ID`, `RUNTIME_ARN` | printed by the script | step 6 |

Terminal setup, repeated in every new terminal window:
```bash
export AWS_PROFILE=<your profile>
export REGION=us-east-1
export ACCOUNT_ID=<12 digits>
export BUCKET=discovery-agent-$ACCOUNT_ID-us-east-1
export KMS_KEY_ARN=<from step 1>
export IMAGE=$ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/discovery-agent:v1
export ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/DiscoveryAgentRuntimeRole
```

---

## Step 1. KMS key (encrypts everything in the bucket)

1. Console → **Key Management Service (KMS)** → **Customer managed keys** → **Create key**.
2. Key type **Symmetric**; key usage **Encrypt and decrypt**; leave advanced options as they are; **Next**.
3. Alias: `discovery-agent`. Description: `Discovery agent data`. **Next**.
4. Key administrators: select your own user or role. **Next**.
5. Key users: select **your own user or role** (you need this to download the results later). **Next**.
6. Review and **Finish**.
7. Open the new key → tab **Key rotation** → **Edit** → tick **Automatically rotate this KMS key every year** → **Save**.

**Check:** the key shows state *Enabled*.
**Write down:** the key **ARN** (General configuration section) as `KMS_KEY_ARN`.

(The runtime role gets access through its IAM policy in step 5; the default key policy allows that.)

---

## Step 2. S3 bucket

1. Console → **S3** → **Create bucket**.
2. Bucket name: `discovery-agent-<ACCOUNT_ID>-us-east-1`. Region **US East (N. Virginia) us-east-1**.
3. Object Ownership: **ACLs disabled**.
4. **Block all public access**: ticked (all four boxes).
5. Bucket Versioning: **Enable**.
6. Default encryption: **Server-side encryption with AWS Key Management Service keys (SSE-KMS)** → **Choose from your AWS KMS keys** → pick `discovery-agent`. Bucket Key: **Enable**.
7. **Create bucket**.

Then, in the bucket:

8. **Management** tab → **Lifecycle rules** → **Create lifecycle rule**:
   - Name `expire-work`; scope: **Limit the scope of this rule using one or more filters**; prefix `work/`.
   - Actions: tick **Expire current versions of objects** (14 days) and **Permanently delete noncurrent versions of objects** (14 days).
   - **Create rule.** (`work/` holds full document text, so it should not live forever.)
9. **Create lifecycle rule** again: name `abort-multipart`; apply to **all objects** (tick the acknowledgement); action **Delete incomplete multipart uploads** after 7 days. **Create rule.**
10. **Permissions** tab → **Bucket policy** → **Edit** → paste the contents of `scripts/iam/bucket_policy.json` after replacing both `<BUCKET>` with your bucket name → **Save changes**. (It refuses any non-HTTPS access.)
11. **Objects** tab → **Create folder** three times: `input`, `work`, `output` (optional, for tidiness).

**Check:** Properties tab shows versioning *Enabled* and default encryption *SSE-KMS* with your key.
**Write down:** `BUCKET`.

---

## Step 3. ECR repository (holds the container image)

1. Console → **Elastic Container Registry** → **Private repositories** → **Create repository**.
2. Visibility **Private**; name `discovery-agent`.
3. **Tag immutability: Enabled.** (Re-pushing the same tag is then refused, so every release needs a new tag.)
4. **Scan on push: Enabled.** Encryption: AES-256 (default).
5. **Create repository.**

**Check:** `discovery-agent` appears in the list.
**Write down:** the repository URI; it should match `IMAGE` without the `:v1`.

---

## Step 4. Build and push the container image

Start Docker Desktop first. In the repository folder:

```bash
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
docker buildx create --use          # first time only; ignore an "already exists" message
docker buildx build --platform linux/arm64 -t $IMAGE --push .
```

If the build fails, copy the error to me: the Dockerfile has never been built before. (Its pinned requirements were resolved for Python 3.10 and the image uses Python 3.12, so a version pin may need adjusting.)

Optional local test before you rely on it (uses a temporary folder, no AWS data):
```bash
docker run --rm --platform linux/arm64 -p 8080:8080 -e DISCOVERY_LOCAL_ROOT=/tmp/store -e AWS_REGION=$REGION $IMAGE
# in a second terminal:   curl http://localhost:8080/ping      -> {"status":"Healthy", ...}
# stop the container with Ctrl+C
```

**Check:** ECR console → `discovery-agent` → an image tagged `v1` exists, size under 2 GB, scan finished.

---

## Step 5. IAM role for the runtime (what the running agent may do)

1. Console → **IAM** → **Roles** → **Create role**.
2. Trusted entity type: **Custom trust policy**. Paste `scripts/iam/runtime_trust.json` after replacing `<ACCOUNT_ID>` (two places). **Next.**
3. On "Add permissions": select nothing. **Next.**
4. Role name `DiscoveryAgentRuntimeRole`. **Create role.**
5. Open the role → **Permissions** tab → **Add permissions** → **Create inline policy** → **JSON** tab.
6. Paste `scripts/iam/runtime_permissions.json` after replacing `<ACCOUNT_ID>` (several places), `<BUCKET>` (four places) and `<KMS_KEY_ARN>` (one place). **Next.**
7. Policy name `DiscoveryAgentRuntime`. **Create policy.**

**Two warnings**
- Do **not** add an `s3:prefix` condition to the `s3:ListBucket` statement. The agent uses a HEAD request to test for checkpoints, and with a prefix condition a missing file returns 403 instead of 404.
- The Bedrock statement is broad on purpose for the first deployment. Narrow it after the first successful run.

**Check:** the role shows one inline policy and the trust relationship names `bedrock-agentcore.amazonaws.com`.
**Write down:** the role ARN as `ROLE_ARN`. Wait about 10 seconds before the next step (IAM changes take a moment to spread).

---

## Step 6. AgentCore Runtime (the container host)

In the terminal (values from the sheet must be exported):
```bash
uv run python scripts/deploy_runtime.py
```
It creates the runtime named `discovery_agent`, shows `CREATING`, and waits until `READY` (a few minutes). Settings it uses: public network access, a 15-minute idle timeout, a 2-hour cap per run, and the environment variables `DISCOVERY_BUCKET`, `DISCOVERY_KMS_KEY_ID`, `AWS_REGION`. It also prints two lines to copy:
```
export RUNTIME_ID=...
export RUNTIME_ARN=...
```
**Decision for you:** the script uses `PUBLIC` network mode (the simplest). Because the documents are customer-confidential, you may want `VPC` mode with VPC endpoints instead; tell me before this step if so, and I will change the script.

If it ends with `Failed: ...`, the reason is printed; the usual causes are an image that is not ARM64, a role that cannot read ECR, or an IAM change that has not yet spread (wait a minute and re-run with a different `RUNTIME_NAME=discovery_agent2`).

**Check:** console → **Amazon Bedrock AgentCore** → Agent runtimes (or Runtime) → `discovery_agent` shows *Ready*.
**Write down:** `RUNTIME_ID` and `RUNTIME_ARN`.

---

## Step 7. Smoke test (fictional document, no trigger yet)

1. Upload the test document:
   ```bash
   aws s3 cp docs/smoke/smoke_call.txt s3://$BUCKET/input/Smoke/01_smoke_call.txt
   ```
   (Or Console → S3 → your bucket → `input/` → **Create folder** `Smoke` → **Upload** the file as `01_smoke_call.txt`.)
2. Start a run by hand:
   ```bash
   uv run python scripts/invoke_runtime.py Smoke smoke1
   ```
   Expected answer: `{"status": "accepted", "run_id": "smoke1", ...}` immediately.
3. Follow it:
   ```bash
   uv run python scripts/watch_run.py Smoke smoke1
   ```
   It prints each step as it finishes and ends with `succeeded` and the output files. For a tiny document this should take a few minutes.
4. Read the results: Console → S3 → `output/Smoke/smoke1/` → open `README.md`. You need `kms:Decrypt` on the key (you added yourself as a key user in step 1).

**Check:** status `succeeded`; four files exist (`discovery.json`, `README.md`, `discovery_report.docx`, `status.json`; `narrative.json` too when the summary succeeded); the README mentions Zendesk, Excel, SharePoint and the discount rules.

**If it fails:** `status.json` has the step and reason. Common causes: `AccessDenied` for S3 or KMS (role policy placeholders wrong), model access, or a typo in `DISCOVERY_BUCKET`. See the troubleshooting table below.

---

## Step 8. Trigger: role, Lambda function, S3 notification

### 8a. Lambda role
1. IAM → **Roles** → **Create role** → Trusted entity **AWS service** → use case **Lambda** → **Next**.
2. Attach the managed policy **AWSLambdaBasicExecutionRole** → **Next**. Name `DiscoveryAgentTriggerRole` → **Create role.**
3. Open the role → **Add permissions** → **Create inline policy** → **JSON** → paste `scripts/iam/trigger_invoke_policy.json` after replacing `<ACCOUNT_ID>` and `<RUNTIME_ID>` (two places) → name `InvokeDiscoveryRuntime` → **Create policy.**

### 8b. Lambda function
1. Console → **Lambda** → **Create function** → **Author from scratch**.
2. Name `discovery-agent-trigger`; Runtime **Python 3.12**; Architecture x86_64.
3. Expand **Change default execution role** → **Use an existing role** → `DiscoveryAgentTriggerRole`. **Create function.**
4. **Code** tab: delete the sample code, paste the whole of `scripts/trigger_lambda.py` into `lambda_function.py`, click **Deploy**.
5. **Code** tab → scroll to **Runtime settings** → **Edit** → **Handler** = `lambda_function.handler` (the default `lambda_handler` will not work) → **Save.**
6. **Configuration** → **General configuration** → **Edit** → Timeout **1 min 0 sec** → **Save.**
7. **Configuration** → **Environment variables** → **Edit** → **Add** key `RUNTIME_ARN`, value = your `RUNTIME_ARN` → **Save.**

8. **Check the bundled boto3 knows the AgentCore client.** Lambda's built-in boto3 can be older than this service. In the Lambda page → **Test** tab → create a test event with this JSON and click **Test**:
   ```json
   {"Records": [{"eventTime": "2026-01-01T00:00:00.000Z", "s3": {"object": {"key": "input/x/not_a_marker"}}}]}
   ```
   The key is not a `_run` marker, so the function does nothing and returns success. If instead it fails with `UnknownServiceError` for `bedrock-agentcore` (or the import line fails), the bundled boto3 is too old: build a package with a current boto3 and upload it as a zip:
   ```bash
   mkdir -p /tmp/lambda_pkg && uv pip install --target /tmp/lambda_pkg boto3 && cp scripts/trigger_lambda.py /tmp/lambda_pkg/lambda_function.py
   (cd /tmp/lambda_pkg && zip -qr ../trigger.zip .)      # then Lambda → Code → Upload from → .zip file → /tmp/trigger.zip
   ```

### 8c. S3 event notification
1. Console → S3 → your bucket → **Properties** → **Event notifications** → **Create event notification**.
2. Name `run-marker`. Prefix `input/`. Suffix `_run`.
3. Event types: **All object create events**.
4. Destination: **Lambda function** → choose `discovery-agent-trigger`. **Save changes.** (The console adds the permission that lets S3 call the function.)

**Check:** Lambda function page shows an **S3** trigger.

---

## Step 9. Test the trigger

1. Create an empty marker file and upload it:
   ```bash
   touch _run
   aws s3 cp _run s3://$BUCKET/input/Smoke/_run
   ```
2. Console → Lambda → `discovery-agent-trigger` → **Monitor** → **View CloudWatch logs** → newest log stream. You should see one JSON line with `"answer": "{\"status\": \"accepted\", ...}"` and a `run_id`.
3. Follow that run: `uv run python scripts/watch_run.py Smoke <that run_id>`.
4. **Duplicate check:** upload `_run` again after the run succeeded. A *new* run starts (new event time = new run id); this is expected. The agent only refuses to start the *same* run id twice.

**Check:** exactly one run per upload; it ends `succeeded`.

---

## Step 10. Log retention

Console → **CloudWatch** → **Logs** → **Log management**. Find:
- `/aws/lambda/discovery-agent-trigger`
- `/aws/bedrock-agentcore/runtimes/<RUNTIME_ID>-DEFAULT` (it appears after the first run)

For each: select it → **Actions** → **Edit retention setting** → choose a period agreed with your data-protection owner (for example 30 days) → **Save.** These logs contain step names, counts and timings, never document text.

---

## Step 11. CloudTrail (audit)

1. Console → **CloudTrail** → **Trails**. If your organisation already has a trail that records **S3 data events**, confirm it covers this bucket and skip to step 12.
2. Otherwise **Create trail**: name `discovery-agent-trail`; storage: **Create new S3 bucket**; **Next.**
3. Events: tick **Management events** (Read and Write) and **Data events**; for data events choose source **S3** and select your bucket (log reads and writes). **Next → Create trail.**

S3 data events are billed per event. The volume here is small (a few objects per run).

---

## Step 12. Budget alert (the Opus pass is the main cost)

Console → **Billing and Cost Management** → **Budgets** → **Create budget** → **Use a template** → **Monthly cost budget** → enter an amount you are comfortable with and your email → **Create.**

---

## Step 13. First real run

1. Upload the real documents, **numbered oldest to newest** (the agent reads them in file-name order):
   ```bash
   aws s3 cp <file> s3://$BUCKET/input/<Engagement>/01_<name>
   aws s3 cp <file> s3://$BUCKET/input/<Engagement>/02_<name>
   ```
   Engagement name: letters, digits, space, `.`, `_`, `-` only. Supported files: no extension, `.txt`, `.md`, `.eml`, `.docx`, `.pdf`. Total about 150,000 tokens (about 520 KB of text) at most.
2. Upload the marker: `aws s3 cp _run s3://$BUCKET/input/<Engagement>/_run`
3. Watch: the Lambda log shows the `run_id`; `uv run python scripts/watch_run.py <Engagement> <run_id>`. Expect roughly 10 to 30 minutes.
4. Results in `output/<Engagement>/<run_id>/` (note: spaces in the engagement name become `_` in the output folder).
5. **Resume a failed run** without paying for finished steps: `uv run python scripts/invoke_runtime.py <Engagement> <run_id> --resume`.

---

## Teardown (reverse order)

1. S3 → bucket → **Properties → Event notifications**: delete `run-marker`.
2. Lambda: delete `discovery-agent-trigger`; IAM: delete `DiscoveryAgentTriggerRole`.
3. AgentCore: delete the runtime `discovery_agent`.
4. ECR: delete the images, then the repository.
5. IAM: delete `DiscoveryAgentRuntimeRole`.
6. S3: **empty** the bucket (this includes old versions), then delete it.
7. KMS: **Schedule key deletion** (minimum waiting period 7 days). Deleting the key makes any remaining encrypted data unreadable.
8. CloudTrail trail (and its bucket), budget, and the log groups.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `invoke_runtime.py` says access denied | Your own identity needs `bedrock-agentcore:InvokeAgentRuntime` on the runtime |
| `watch_run.py` access denied on S3 or KMS | Your identity needs `s3:GetObject` on `output/*` and `kms:Decrypt` on the key (add yourself as a key user) |
| Lambda log shows `AccessDeniedException` | Step 8a policy: wrong `<RUNTIME_ID>`/account, or the ARN format; paste the exact error to me |
| Lambda fails with `UnknownServiceError` / `bedrock-agentcore` | Lambda's bundled boto3 is too old: upload a zip with a current boto3 (step 8b-8) |
| Lambda log shows `Runtime.HandlerNotFound` | Handler is not `lambda_function.handler` (step 8b-5) |
| Nothing happens after uploading `_run` | The file must be named exactly `_run` and sit directly under `input/<Engagement>/`; check the notification's prefix and suffix and the Lambda's S3 trigger |
| `status.json` says "no input documents found" | Wrong engagement folder, files start with `_` or `.`, or an unsupported file type |
| `status.json` says "corpus too large" | More than about 150,000 tokens even after trimming: split the engagement |
| `status.json` shows `ReadTimeoutError` | Bedrock read timeout: set the runtime environment variable `BEDROCK_READ_TIMEOUT` higher (default 900 seconds) |
| `AccessDenied` for `s3:PutObject` or `kms:GenerateDataKey` | Role policy placeholders wrong, or `DISCOVERY_KMS_KEY_ID` is not the key ARN |
| A 403 appears for a checkpoint check | `s3:ListBucket` has a prefix condition: remove it (step 5 warning) |
| Run disappears after about 15 minutes with no status update | Session idle timeout: the entrypoint registers the run as busy; check the runtime logs for errors and tell me |
| Runtime creation fails with an image error | Image must be `linux/arm64` and readable by the role; push a new tag (tags are immutable) and re-run step 6 |
| Re-pushing `v1` is refused | Immutable tags: build as `:v2` and update `IMAGE` |

---

## What I verified offline (and what I could not)

**Verified without AWS:** the exact parameters of `create_agent_runtime` and `invoke_agent_runtime` against boto3's real service definitions (including `environmentVariables`, the statuses and `failureReason`); the trigger Lambda's logic with a fake client (URL-encoded keys, rejected bad keys, same session for a duplicate event, payload accepted by the agent); all JSON policy files parse; the scripts compile; the 80 unit tests pass.

**Could not verify:** the Docker build, whether Lambda's bundled boto3 includes the AgentCore client, console screen labels, the IAM policies against real AWS (the AWS-documented baseline plus my S3/KMS statements), the `InvokeAgentRuntime` resource ARN format in the Lambda policy, and a full run inside the deployed runtime. Treat the first run as the test, and send me any error text you see.
