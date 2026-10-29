# Architecture practice

## 1. Requirements into decisions
- Extract every functional need, quality attribute (latency, throughput, RPO/RTO, retention), compliance rule, data volume, user count and integration. Every choice traces to at least one of them.
- Rank the decision drivers; the top driver settles close calls.
- If you cannot name the requirement or structural need behind a component, remove it.
- When the SRS is silent, choose the sensible default, set `assumed: true` and give the reason. Never leave a value blank and never answer with a question.
- If a requirement conflicts with the availability model stated in this prompt, keep the stated model and record the residual risk and the recovery path in the consequences.

## 2. Service selection
Compute
- Event-driven, spiky or short work (under 15 minutes): AWS Lambda on arm64; 512–1,024 MB when it loads libraries or S3 data.
- Long-running web or API services, persistent connections, workers and tasks over 15 minutes: ECS services on AWS Fargate (ARM64). ECS, not EKS, unless the SRS requires Kubernetes.
- Amazon EC2 only for OS-level control, licensed software, GPUs, or steady load where an instance is clearly cheaper. Never use AWS App Runner.
- Scheduled work: EventBridge Scheduler to Lambda (short) or Fargate (long). Multi-step workflows with retries and human approval: Step Functions.
Data
- Relational data with joins, transactions and SQL: Amazon RDS (PostgreSQL or MySQL, named version, Single-AZ). Aurora storage is replicated across three AZs by design, so choose Aurora only when the SRS needs an Aurora-specific capability, and say so.
- Known key-value or document access patterns: DynamoDB, on-demand unless load is steady.
- A cache only when a stated latency or load target requires it.
- Objects, uploads, exports and archives: S3 with lifecycle rules for retention. Shared POSIX files: EFS. EC2 block storage: EBS gp3.
- Vectors for retrieval-augmented generation: Bedrock Knowledge Bases with OpenSearch Serverless for hybrid search and filtering; PostgreSQL pgvector only when vectors must sit beside relational data.
Integration
- SQS (with a dead-letter queue) to buffer and decouple work; SNS for fan-out; EventBridge for event routing. No broker where a direct call suffices.
- API Gateway (HTTP API unless REST API features are needed) in front of Lambda; Application Load Balancer in front of Fargate or EC2.
Generative AI
- Model inference, RAG, agents and guardrails: Amazon Bedrock with an exact model ID. SageMaker only to train or host custom models.
Edge and identity
- Cognito for application sign-in and federation to the customer's identity provider. CloudFront for static sites and global delivery. WAF only for a stated exposure, compliance or abuse-protection need.

## 3. Security baseline (applies to every design; state it once in security and operations)
- Workloads and data stores in private subnets; only the load balancer or API entry point is public. Security groups reference upstream security groups; nothing is open to 0.0.0.0/0 except 443 on the public entry.
- Encryption at rest with AWS KMS (a customer managed key for databases, buckets and secrets holding sensitive data) and in transit with TLS 1.2 or higher.
- One least-privilege IAM role per workload, scoped to specific resource ARNs. No long-lived access keys, no wildcard actions.
- Credentials and API keys in Secrets Manager. S3 Block Public Access on. CloudTrail for audit; CloudWatch Logs with a set retention.
- Reach AWS services privately: S3 and DynamoDB gateway endpoints; interface endpoints when the SRS forbids internet egress; otherwise one NAT Gateway for outbound calls.

## 4. Availability rules
The zone count comes from the availability model stated in this prompt; these rules hold whatever it is.
- Name every AZ in `availability_zones`, give each an `az` boundary, and place every zonal resource (subnets, instances, tasks, NAT Gateway, database) in a named zone.
- Count per-zone resources per zone: one public and one private subnet each, and a NAT Gateway each.
- Two or more zones: enable the managed multi-AZ option where one exists, state it in `sku_summary`, and note cross-AZ transfer cost in the consequences.
- One zone: no Multi-AZ, standbys or cross-AZ replicas, and never call the design highly available. Where AWS still demands a second-AZ subnet (ALB, DB subnet group), record it in configuration as required by AWS with no workloads; it is not a boundary.
- Backups are required at any zone count, since zones do not protect against deletion or corruption. State RPO and RTO as outcomes of restoring from backup.
- Claim Well-Architected Reliability practice only when the design spans two or more zones.
- Regional services (S3, DynamoDB, SQS, Lambda, Bedrock) need no AZ setting; do not present their internal replication as a design feature.

## 5. Right-sizing
- Start small and state the scaling path as a consequence, with a trigger (for example "move to db.m7g.large when CPU stays above 70%").
- Prefer Graviton (arm64) wherever supported: Lambda, Fargate, RDS, ElastiCache, EC2.
- Increase a size only when an SRS number (users, concurrency, requests per second, data size, latency target) requires it, and show that reasoning in the decision context.
- gp3 for EBS and RDS storage (RDS minimum 20 GiB). Serverless where load is spiky or often idle; provisioned where it is steady. Fargate uses valid vCPU/memory pairs only.

## 6. Writing each decision
1. Context: the requirements and constraints that force the decision, as neutral facts with their REQ ids.
2. Decision: "Use ..." with the exact service, instance family and size, engine and version, storage type and GiB, memory and vCPU, runtime and architecture, and quantity.
3. Consequences: positive and negative, including operational duties, the availability and cross-AZ cost trade-offs where relevant, and the scaling trigger.
4. Rejected alternatives: each with one concrete reason tied to a requirement, and the condition under which it would be chosen instead.

## 7. Component granularity
- One component per deployed resource at service level: an ECS service, an RDS instance, an S3 bucket per purpose, a queue per workflow. Fold sub-resources (security groups, route tables, log groups, policies) into the owning component.
- Identical instances or tasks are one component with a quantity. Cross-cutting services (KMS, CloudTrail, CloudWatch) appear once.
- Same-service components with closely related roles (several S3 buckets in one pipeline, several Lambda functions doing related jobs) are usually one component too, following standard AWS diagramming practice of one icon per tier or group: name it for the group or its main role and cover every role in its purpose. Split them only when their configuration, boundary or security posture genuinely differs.
- About 15 components is a good target for a small-to-medium solution; treat it as guidance, not a hard limit.

## 8. Never
- Vague wording: "appropriate", "suitable", "managed database", "or equivalent", ranges in place of one value, unnamed engine versions or sizes.
- Invented services, instance types, features or model IDs. Use the service sheet first; for anything else, follow its rules for unlisted services and choose a well-known size.
- Padding: WAF, caches, queues, CDNs, GuardDuty, extra environments or DR Regions with no requirement behind them.
- Over-engineering: Kubernetes for a simple service, microservices without a stated reason, several databases where one fits, an event bus for a single call.
- Public databases, public buckets, hard-coded secrets, shared admin roles, unencrypted storage.
- Unflagged guesses: every value not taken from the SRS is marked assumed.
