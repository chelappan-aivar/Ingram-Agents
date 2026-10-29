# `docs/` — architecture and deployment documentation

Design docs, earlier deployment guides, the AWS-icon diagram, and the smoke-test document. How to test and deploy the
agent today: `../deployment/README.md`.

## Files

| File | Purpose |
|---|---|
| `ARCHITECTURE.md` | How the agent works internally: the seven tools, the model passes, assembly and ranking. |
| `TARGET_ARCHITECTURE.md` | The original request-driven cloud design (UI → API → Lambda → AgentCore Runtime → S3/Bedrock), S3 layout, permissions. Built since; see `../deployment/README.md`. |
| `AGENT_FLOW.md` | Step-by-step walkthrough of one run from invocation to published outputs. |
| `BLUEPRINT_ORCHESTRATOR.md` | Deeper design notes on the orchestrator and the AgentCore assumptions (verified against AWS docs). |
| `CLI_DEPLOYMENT.md` | Superseded by `deployment/scripts/deploy.sh`. Deploy the runtime with the AWS CLI only (ZIP route, no Docker/ECR) — KMS, S3, roles, runtime, smoke test, teardown. |
| `DEPLOYMENT.md` | Superseded by `deployment/scripts/deploy.sh`. Fuller manual deployment reference. |
| `MANUAL_DEPLOYMENT_RUNBOOK.md` | Superseded by `deployment/scripts/deploy.sh`. Console-based runbook for the same resources. |
| `DEMO_SCRIPT.md` | Live demo walkthrough (resource ids from the earlier manual deployment). |
| `target_architecture_aws.drawio` | High-level AWS-icon architecture diagram (open with [draw.io](https://app.diagrams.net)). |

## Subfolders

| Folder | What it holds |
|---|---|
| `smoke/` | `smoke_call.txt` — a short fictional call transcript used to smoke-test a deployed runtime cheaply. |
