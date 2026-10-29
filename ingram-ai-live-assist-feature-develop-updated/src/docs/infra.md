# Infrastructure

Every resource name starts with the environment prefix (for example `ila-dev`).
`deploy/deploy.sh <env> up` creates all of them.

| Resource | Name | Used for |
|---|---|---|
| API Gateway (HTTP API) | `<prefix>-api` | Public HTTPS endpoint for the UI |
| Lambda | `<prefix>-api` | Handles API requests: checks the access key, sends events to the runtime, reads call views |
| Lambda | `<prefix>-replayer` | Feeds uploaded recordings or audio into a call (audio goes through Transcribe) |
| AgentCore Runtime | `<prefix>_runtime` | Entry point for call events, and the background worker that runs the agent |
| AgentCore Memory | `<prefix>_memory` | The agent's conversation history |
| AgentCore Memory | `<prefix>_ltm` | Facts per account and opportunity across calls (semantic strategy, records expire after 180 days) |
| Bedrock | Claude Haiku 4.5 | The model behind the agent |
| Amazon Transcribe | streaming | Turns uploaded audio into text |
| DynamoDB | `<prefix>-timeline` | Call details, turns, runs and minutes (180-day expiry); accounts and opportunities (no expiry) |
| DynamoDB | `<prefix>-sessions` | Checklist state, problem summary and suggestions per call |
| S3 | `<prefix>-uploads-<account>-<region>` | Uploaded recordings and audio (180-day expiry) |
| ECR | `<prefix>/agent` | Container image for the runtime |
| Secrets Manager | `<prefix>/access-key` | API access key (`X-Access-Key` header) |
| IAM roles | `<prefix>-BedrockAgentCoreRuntime`, `<prefix>-api`, `<prefix>-replayer` | Permissions, each limited to this environment |
| CloudWatch Logs | per Lambda and runtime | Logs (metadata only, no call text) |

## Flow

1. The UI calls the API to start a call, add lines, or upload a recording or audio.
2. The API Lambda sends events to the AgentCore Runtime. For uploads, the replayer
   Lambda sends the turns instead.
3. The runtime stores each turn in the timeline table, then runs the agent in the
   background. The agent writes captured fields and suggestions to DynamoDB.
4. The UI polls the API, which reads the call's view from DynamoDB.
