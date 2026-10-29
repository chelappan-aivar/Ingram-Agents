# Discovery Agent — Live Demo Script
> **Note.** Account ids, API ids and resource names below come from the earlier manual deployment. After `deployment/scripts/deploy.sh` use the stack outputs it prints (UI, API, runtime, data bucket).


A screen-share walkthrough: what the agent is, how a request flows through the AWS ecosystem (UI → API Gateway →
Lambda → AgentCore Runtime → Bedrock + S3), and the logs that prove each hop. Total time ≈ 12–15 min.

---

## 0. Before you start (do this 5 min early, off-camera)

```bash
cd ~/Documents/Ingram_Discovery-agent
export AWS_PROFILE=document_extraction AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
aws sso login --profile document_extraction          # refresh the token
aws sts get-caller-identity --query Account --output text   # must print 455162169375

python3 -m http.server 8000 --directory ui &          # serve the UI (NOT file://)
# open http://localhost:8000 in the browser
```

Open these tabs so you can switch quickly:
1. **Browser** → `http://localhost:8000` (the UI)
2. **Terminal A** → repo root (for curl / status)
3. **Terminal B** → `aws logs tail ... --follow` (runtime logs, started in Act 4)
4. **AWS Console** (optional, nice visual) → S3 bucket `ingram-discovery-agent`, and CloudWatch Logs

Have a small input file ready, e.g. `docs/smoke/smoke_call.txt`.

**Key facts to say out loud:**
- Account **455162169375** (`document_extraction`), region **us-east-1**
- Everything is named **`ingram_discovery_agent`** (bucket is `ingram-discovery-agent` — S3 forbids underscores)
- API: `https://eaxi52af2c.execute-api.us-east-1.amazonaws.com`

---

## Act 1 — What the Discovery Agent is (1–2 min, no screen action)

> "The Discovery Agent reads all the documents from **one customer engagement** — call transcripts, emails, meeting
> minutes, chats, PDFs, Word docs — and produces a **discovery report**: what the customer explicitly said, what the
> documents *imply but don't say*, a ranked list of use cases with **verbatim evidence**, and open questions for the
> next call. A solutions architect reviews it."

> "Two things make it trustworthy: every finding **cites a real quote** from the documents, and anything the documents
> don't support is **stripped out by plain code** after the model runs. It's not just 'ask an LLM'."

**Inputs** → show the file types: `.txt .md .eml .docx .pdf`.
**Outputs** → `discovery.json` (for other agents), `README.md` (the report), `discovery_report.docx` (Word), `narrative.json`.

---

## Act 2 — The architecture, in one breath (1–2 min)

Open `docs/target_architecture_aws.drawio` (draw.io) **or** just narrate over the UI.

> "It's **request-driven** — someone presses Run, there are no event triggers. Five steps:"
>
> 1. Browser asks the **API** for presigned upload URLs
> 2. Browser uploads files **straight to S3** (bypassing the API, so no file-size limit)
> 3. Browser calls **POST /runs**; a thin **Lambda** calls **InvokeAgentRuntime** and returns in under a second
> 4. The **AgentCore Runtime** works in the background — reads from S3, calls **Bedrock** (Sonnet, Opus, Haiku)
> 5. Browser **polls status.json** and then downloads the results via presigned URLs

> "The Lambda never does analysis and never touches file bytes — it just signs URLs and starts the runtime. The heavy
> lifting is all in the runtime."

---

## Act 3 — Run it from the UI (3–4 min) — the main event

On `http://localhost:8000`:

1. **Type an engagement name** — e.g. `DemoLive`.
   > "This name becomes the S3 folder — `input/DemoLive/`. It's validated the same way the runtime validates it, so a
   > key can't be forged."
2. **Drag in the document(s)** (`smoke_call.txt`, or 2–3 of the mock files from `demo/mock_inputs/`).
3. **Press _Upload & Run_.** Narrate what's happening as the button text changes:
   > "It just asked the API for a presigned URL... now it's PUTting the file **directly to S3**... now it's calling
   > POST /runs, which fired the Lambda, which invoked the runtime — and we got a `run_id` back in under a second."
4. The **right panel** shows the run: status badge, the **7-step timeline**, and a **progress bar** that advances as
   `status.json` updates every few seconds.
   > "These seven steps are the real pipeline: load documents, extract explicit needs with **Sonnet**, find hidden
   > signals with **Opus**, merge-and-rank in plain code, write the narrative with **Haiku**, render, publish."

Leave it running and switch to the logs (Act 4) while it works — then come back for the green "Completed" + download links.

---

## Act 4 — Prove the wiring with logs (3–4 min) — the credibility moment

While the run is in flight, show the two hops.

**(a) The Lambda hop** — Terminal A:
```bash
aws logs tail /aws/lambda/ingram_discovery_agent --since 5m --format short
```
> "Here's the API Lambda. One line per request: it logs the **route**, the **engagement**, and the **run_id** — and
> deliberately **never logs document content**. See `run_started` — that's the InvokeAgentRuntime call."

**(b) The runtime hop** — Terminal B (start this live):
```bash
aws logs tail /aws/bedrock-agentcore/runtimes/ingram_discovery_agent-0ayW0REyxv-DEFAULT --since 5m --follow
```
> "This is the agent itself inside AgentCore. You can watch it move through the steps and make the Bedrock calls in
> real time."

**(c) S3 as the backbone** — Terminal A or the Console:
```bash
aws s3 ls s3://ingram-discovery-agent/input/DemoLive/     # the file the browser uploaded
aws s3 ls s3://ingram-discovery-agent/output/DemoLive/ --recursive   # status.json + outputs appear here
```
> "Everything flows through this one bucket: `input/` is what the browser uploaded, `work/` holds checkpoints, and
> `output/` is where `status.json` and the final report land. The bucket is SSE-KMS encrypted and completely private —
> the only way in or out is a short-lived signed URL."

---

## Act 5 — The result (1–2 min)

Back on the UI, the badge turns green — **Completed, 7/7 steps** — and **download links** appear.

1. Click **README.md** (or `discovery_report.docx`) → open it.
   > "Here's the discovery report. Executive summary, a **ranked table of use cases** with confidence levels, the
   > **hidden opportunities** flagged as hypotheses, and open questions — every item tied to a verbatim quote."
2. Optionally open `discovery.json`:
   > "And the same thing as structured JSON, so a downstream agent can consume it."

> "That's the full loop: a browser upload, a one-second API call, a background agent orchestrating three Claude models,
> and a reviewed-ready report — all inside one private, encrypted AWS account."

---

## Act 6 — Close (30 sec)

> "To recap the AWS wiring: **CloudFront/S3** could host the UI, **API Gateway** is the front door, a **thin Lambda**
> signs URLs and starts runs, **Bedrock AgentCore Runtime** runs the orchestrator and its seven tools, **Bedrock**
> provides the models, **S3 + KMS** store everything encrypted, and **CloudWatch** gives us the logs we just watched.
> It's request-driven, least-privilege, and every finding is evidence-backed."

Mention honestly if asked:
- **No auth yet** on the API (demo stage) — a Cognito JWT authorizer is the next step.
- **Model access is per-account** — here extraction runs on Sonnet 4.5 because Sonnet 5.5 isn't enabled in this account.

---

## Cheat sheet (keep visible)

| Thing | Value |
|---|---|
| UI | `http://localhost:8000` |
| API base | `https://eaxi52af2c.execute-api.us-east-1.amazonaws.com` |
| Runtime log group | `/aws/bedrock-agentcore/runtimes/ingram_discovery_agent-0ayW0REyxv-DEFAULT` |
| Lambda log group | `/aws/lambda/ingram_discovery_agent` |
| Bucket | `ingram-discovery-agent` (`input/ work/ output/ code/`) |
| Account / region | `455162169375` / `us-east-1` |

### If the UI misbehaves, fall back to curl (Terminal A)
```bash
API=https://eaxi52af2c.execute-api.us-east-1.amazonaws.com
# 1) presigned URL
RESP=$(curl -s -X POST "$API/engagements/DemoLive/upload-urls" -H 'content-type: application/json' -d '{"files":["01.txt"]}')
URL=$(python3 -c "import sys,json;print(json.load(sys.stdin)['urls']['01.txt'])" <<<"$RESP")
# 2) upload the file
curl -s -o /dev/null -w "%{http_code}\n" -X PUT "$URL" \
  -H "x-amz-server-side-encryption: aws:kms" \
  -H "x-amz-server-side-encryption-aws-kms-key-id: acf6a128-5cbe-43c8-904f-313a566d91bd" \
  --data-binary @docs/smoke/smoke_call.txt
# 3) start the run
curl -s -X POST "$API/runs" -H 'content-type: application/json' -d '{"engagement":"DemoLive"}' | python3 -m json.tool
# 4) poll (replace RUN_ID)
curl -s "$API/runs/RUN_ID?engagement=DemoLive" | python3 -m json.tool
```

### Timing note
A real run takes ~1–3 minutes (the Opus hidden-signals pass is the slow part, ~1 min). Fill that time with Act 4
(logs) so there's never dead air.
