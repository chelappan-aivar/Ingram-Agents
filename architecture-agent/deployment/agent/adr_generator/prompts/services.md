# Service sheet (us-east-1, one AZ, one VPC)
Use the exact name at the start of each row as the component's `service` value. Use the default unless an SRS requirement justifies the step-up, and say which requirement in the decision. Put every provisioned dimension in `configuration`. Never put usage (hours, requests, tokens, GB transferred or stored per month) in the design; usage is estimated downstream.

## Edge and networking
- Amazon Route 53 — DNS | default: 1 public hosted zone, alias records | step-up: health checks | global
- Amazon CloudFront — CDN and TLS for web, S3 or ALB origins | default: 1 distribution, PriceClass_100, origin access control to S3 | step-up: PriceClass_All | certificate in us-east-1
- AWS WAF — layer-7 filtering | default: 1 web ACL, AWS managed Core rule set + Known Bad Inputs + 1 rate-based rule | step-up: Bot Control | attach to CloudFront, ALB or API Gateway
- Internet Gateway — internet path for the public subnet | default: 1 per VPC
- NAT Gateway — outbound internet for private subnets | default: one NAT Gateway per Availability Zone in the availability model, each in that zone's public subnet | prefer VPC endpoints for AWS service traffic
- AWS PrivateLink — VPC endpoints | default: S3 gateway endpoint (and DynamoDB if used); interface endpoints only where private access to Bedrock, Secrets Manager, ECR or CloudWatch Logs is required | one component listing the endpoints in configuration
- Application Load Balancer — HTTP/HTTPS entry to containers or instances | default: internet-facing, HTTPS 443 listener, ACM certificate, one subnet and its targets in every Availability Zone of the availability model | step-up: Network Load Balancer for TCP | with a single-zone model AWS still requires subnets in two AZs: record `subnets: "2 (second AZ subnet required by AWS, no workloads)"` in configuration and do not add that subnet as a boundary
- Amazon API Gateway — managed API front door | default: HTTP API, regional | step-up: REST API (WAF, API keys, usage plans, request validation)

## Compute and containers
- AWS Lambda — event-driven functions | default: python3.13, arm64, 512 MB, 30 s timeout | step-up: 1,769 MB (1 vCPU), up to 900 s | VPC attachment only when it calls VPC resources
- Amazon EC2 — virtual machines | default: t4g.medium (2 vCPU, 4 GiB, Graviton), Amazon Linux 2023, gp3 30 GiB | step-up: m7g.large (2 vCPU, 8 GiB); x86 only if required: m7i.large | IMDSv2, Systems Manager access, no SSH from the internet
- AWS Fargate — containers on ECS without hosts | default: Linux/ARM64, 0.5 vCPU / 1 GB per task, 1 task | step-up: 1 vCPU / 2 GB, 2 vCPU / 4 GB | model each ECS service on Fargate as one component with service "AWS Fargate"; tasks = quantity; do not add a separate ECS cluster component
- Amazon ECS — container orchestration on EC2 capacity | default: 1 cluster with EC2 capacity provider on t4g.medium | use only when containers must run on EC2
- Amazon ECR — container image registry | default: private repositories, scan on push, lifecycle policy keeps last 10 images

## Data
- Amazon RDS — relational database | default: PostgreSQL 17, db.t4g.medium, gp3 50 GiB, 7-day automated backups, encryption on, and the deployment option the availability model requires (Multi-AZ with a standby for two or more zones, Single-AZ for one) | step-up: db.m7g.large or db.r7g.large; MySQL 8.4 | the DB subnet group always spans two AZs: record it in configuration like the ALB
- Amazon Aurora — variable or high-scale relational | default: Aurora PostgreSQL 17, Serverless v2, 0.5–4 ACU, 1 writer, and one reader in a second zone when the availability model has two or more zones | step-up: db.r7g.large provisioned writer | cluster storage is managed by AWS across AZs
- Amazon DynamoDB — key-value or document access | default: on-demand capacity, point-in-time recovery on | step-up: provisioned capacity with auto scaling | regional
- Amazon ElastiCache — cache and sessions | default: Valkey 8.0, cache.t4g.small, one node per Availability Zone in the availability model with Multi-AZ failover on when there are two or more | step-up: cache.m7g.large
- Amazon OpenSearch Service — full-text and vector search | default: managed domain, OpenSearch 2.19, one t3.small.search data node per Availability Zone in the availability model, gp3 20 GiB, zone awareness on when there are two or more | step-up: 1 x m7g.large.search, gp3 100 GiB; or an OpenSearch Serverless vector collection

## Storage
- Amazon S3 — objects, documents, data lake | default: S3 Standard, SSE-KMS, versioning on, Block Public Access on | step-up: lifecycle to S3 Glacier Flexible Retrieval or Intelligent-Tiering for retention | regional
- Amazon EFS — shared POSIX file system | default: EFS One Zone, Elastic throughput, 1 mount target | only for files shared by several compute resources

## Integration
- Amazon SQS — queues and buffering | default: Standard queue + dead-letter queue, 4-day retention | step-up: FIFO when order matters
- Amazon SNS — fan-out and notifications | default: Standard topic, email or SQS subscriptions
- Amazon SES — outbound email | default: 1 verified domain identity, DKIM on | step-up: dedicated IP
- Amazon EventBridge — events and schedules | default: default event bus + rules; EventBridge Scheduler for cron | step-up: custom event bus
- AWS Step Functions — workflow orchestration | default: Standard workflow | step-up: Express workflow for high-volume short runs
- Amazon Kinesis Data Streams — streaming ingest | default: on-demand mode, 24 h retention | step-up: provisioned, 2 shards
- AWS Glue — ETL and Data Catalog | default: Glue 5.0 Spark job, G.1X x 2 workers, Data Catalog database | step-up: G.2X x 5 workers
- Amazon Athena — SQL over S3 | default: 1 workgroup, engine version 3, Parquet with partitions

## AI/ML
- Amazon Bedrock — foundation models and RAG | default: Anthropic Claude Sonnet 4.6 (us.anthropic.claude-sonnet-4-6), on-demand; Claude Haiku 4.5 for light classification; Amazon Titan Text Embeddings V2 (1,024 dimensions) | step-up: Bedrock Knowledge Bases with an OpenSearch Serverless or Aurora pgvector vector store; Bedrock Guardrails
- Amazon Textract — OCR, forms and tables | default: DetectDocumentText, asynchronous jobs from S3 | step-up: AnalyzeDocument with Forms and Tables

## Security and identity
- Amazon Cognito — application sign-in, SAML/OIDC federation | default: 1 user pool, Essentials tier, federation to the customer's identity provider | step-up: Plus tier
- AWS Secrets Manager — database credentials and API keys | default: one secret per credential, rotation on for database credentials
- AWS KMS — encryption keys | default: 1 customer managed symmetric key, annual rotation on
- AWS IAM — roles and policies | describe in security and operations; never a component

## Operations
- Amazon CloudWatch — logs, metrics, alarms | default: log groups with 30-day retention, alarms on errors and saturation, 1 dashboard
- AWS CloudTrail — API audit trail | default: 1 multi-Region management-events trail to an S3 bucket with log file validation

## Services not on this sheet
Choose the closest managed AWS service, use its exact official name, the smallest current-generation size that meets the SRS, and state every provisioned dimension in configuration. Prefer serverless or on-demand capacity for unpredictable load. Instance families: t4g/t3 burstable for light or spiky load; m7g (m7i for x86) general purpose; c7g compute-bound; r7g memory-bound such as databases and caches. RDS: db.t4g → db.m7g → db.r7g. ElastiCache: cache.t4g → cache.m7g → cache.r7g. Prefer Graviton unless x86 is required.
