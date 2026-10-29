# Architecture Decision Record: Billing Dispute Assistant

| Field | Value |
|---|---|
| Customer | Northbridge Cloud Distribution |
| Status | Proposed |
| Date | 2026-10-12 |
| Region | us-east-1 |
| Availability Zone | us-east-1a |
| Source document | 1-input-srs.md |
| Components | 28 |

## 1. Context

Northbridge Cloud Distribution resells AWS to partner resellers and end customers. Today, billing dispute resolution requires agents to manually compare AWS cost and usage report files, Northbridge invoices from the LedgerOne ERP, and partner agreements, resulting in a mean time to resolution of about 20 days. The Billing Dispute Assistant is a new AI-assisted web application that automates ingestion of monthly AWS billing files and nightly LedgerOne invoice exports, supports dispute intake from both a web UI and the HelpTrack ticketing webhook, generates AI-drafted explanations and recommended resolutions with citations, and manages agent review and approval before posting responses back to HelpTrack. The system runs exclusively in AWS us-east-1, single-AZ us-east-1a, handles Confidential-Financial data, enforces Northbridge SSO with RBAC, and must operate within a USD 6,000/month pilot budget. Multi-AZ and multi-Region deployment are explicitly out of scope for the pilot.

## 2. Requirements addressed

| ID | Requirement | Type |
|---|---|---|
| REQ-01 | The system shall ingest monthly AWS billing files (CUR ~30 GB and discount breakdown ~8 GB per month, compressed parquet or CSV) delivered to a landing area, validate them and make them queryable within 4 hours of arrival. | functional |
| REQ-02 | The system shall import the nightly LedgerOne invoice export (CSV, ~200 MB) via SFTP push, reject or quarantine malformed files, and complete the import within 30 minutes. | functional |
| REQ-03 | The system shall let a billing agent create a dispute in the web UI capturing partner, billing period, invoice reference and disputed amount. | functional |
| REQ-04 | The system shall receive disputes from the HelpTrack outbound webhook and create or update the matching dispute, ignoring duplicates. | functional |
| REQ-05 | The system shall retrieve billing line items, invoice lines and partner agreement terms relevant to a dispute and present a side-by-side comparison highlighting differences. | functional |
| REQ-06 | The system shall generate an AI explanation and recommended resolution for a dispute using Amazon Bedrock, with each statement citing supporting line items or agreement clauses. | functional |
| REQ-07 | The system shall let the agent review, edit, approve or reject a draft and request regeneration; nothing is posted without explicit agent approval. | functional |
| REQ-08 | The system shall post the approved response to the HelpTrack ticket via its REST API and record the outcome, retrying on failure. | functional |
| REQ-09 | The system shall record an immutable audit event for every AI draft (inputs, model, prompt version, output) and every agent action. | functional |
| REQ-10 | The system shall provide search over past disputes by partner, period, amount, status and free text. | functional |
| REQ-11 | The system shall provide a dashboard of dispute status, ageing and MTTR, filterable by period and partner. | functional |
| REQ-12 | The system shall enforce role-based access (agent, Billing Operations Lead, finance auditor) and hide margin fields from read-only roles. | functional |
| REQ-13 | The system shall send an email notification to the assigned agent when a draft is ready. | functional |
| REQ-14 | The system shall extract text from PDF invoices attached to disputes and make it available to comparison and drafting. | functional |
| REQ-15 | About 200 disputes per week peaking at 60 per day at month-end; about 1,500 AI drafts per month including regenerations. | non_functional |
| REQ-16 | UI p95 response time under 2 seconds for page loads and searches. | non_functional |
| REQ-17 | AI draft generation p95 under 45 seconds from request to draft displayed. | non_functional |
| REQ-18 | Monthly billing files ingested and queryable within 4 hours of arrival; nightly invoice import completed within 30 minutes. | non_functional |
| REQ-19 | 99.5% availability during business hours Monday–Friday 07:00–19:00 US Eastern; single-AZ deployment in us-east-1a accepted for the pilot. | non_functional |
| REQ-20 | RPO 24 hours and RTO 8 hours. | non_functional |
| REQ-21 | Audit trail and billing data retained 7 years; raw billing files move to lower-cost storage after 90 days and remain retrievable. | non_functional |
| REQ-22 | Northbridge SSO (SAML 2.0) with MFA enforced by the identity provider; least-privilege access; encryption at rest and in transit; no publicly accessible data stores; security-relevant events logged. | non_functional |
| REQ-23 | Support 3x growth in disputes, users and file volumes within 2 years without redesign. | non_functional |
| REQ-24 | Central logs, metrics and alarms for ingestion, integrations, AI latency and errors; alarms routed to the operations team. | non_functional |
| REQ-25 | Operate within the pilot budget ceiling of USD 6,000 per month; prefer serverless and managed services and Graviton-based compute. | non_functional |
| REQ-26 | All data including backups and logs shall remain in us-east-1; AWS-native managed services only; generative AI only through Amazon Bedrock. | constraint |
| REQ-27 | All zonal resources shall be deployed in us-east-1a only; no resources run in any other Availability Zone. | constraint |
| REQ-28 | Data stores shall use private networking only, encryption at rest and in transit, and authentication through Northbridge SSO. | constraint |
| REQ-29 | Infrastructure defined as code; deployments repeatable across development, test and pilot environments. | constraint |

## 3. Architecture decisions

### ADR-001: Use AWS Fargate (ARM64) for the Dispute API and web application backend

**Status:** Accepted

**Context:** The system serves ~40 named users with ~25 concurrent at peak (REQ-15, REQ-16) and must handle sustained HTTP traffic for dispute management, AI draft orchestration and HelpTrack webhook receipt (REQ-03, REQ-04, REQ-07, REQ-08). Sessions are long-lived and the API must maintain connections to RDS and Secrets Manager. The pilot budget favours right-sized containers over EC2 instances (REQ-25).

**Decision:** Use AWS Fargate on Linux/ARM64 with 1 vCPU / 2 GB per task and 2 desired tasks for the Dispute API ECS service. Graviton ARM64 reduces cost and meets the budget constraint. Two tasks provide redundancy within the single AZ and headroom for month-end peak load.

**Consequences:**
- Stateless containers scale horizontally; add tasks when CPU exceeds 70% to handle 3x growth (REQ-23).
- No OS patching burden; AWS manages the Fargate data plane.
- Single-AZ: if us-east-1a has an outage, both tasks are unavailable until the AZ recovers; RTO is bounded by ECS task restart time (~2 min), not backup restore.
- ARM64 images must be built for linux/arm64; CI pipeline must target this platform.

**Requirements:** REQ-03, REQ-04, REQ-05, REQ-07, REQ-08, REQ-15, REQ-16, REQ-25

**Alternatives considered (rejected, not part of this design):** AWS Lambda for the API (Dispute comparison and AI orchestration can exceed 15 minutes for large CUR files (REQ-05, REQ-17); Lambda's 15-minute timeout is a hard ceiling. Lambda would be chosen if all operations were short and event-driven.); Amazon EC2 (t4g.medium) (No OS-level control or licensed software is required (C-01); Fargate removes patching overhead and is cheaper at this task count.)

### ADR-002: Use Amazon RDS for PostgreSQL as the operational data store

**Status:** Accepted

**Context:** Disputes, invoices, partners, AI drafts and audit events are relational entities with joins across them (REQ-05, REQ-10, REQ-11). The audit trail must be immutable and tamper-evident (REQ-09). Volume is modest: ~870 disputes/month, ~1,500 drafts/month, ~25,000 audit events/month (REQ-15). The system must support 3x growth (REQ-23) and stay within the pilot budget (REQ-25).

**Decision:** Use one Amazon RDS instance running PostgreSQL 17, db.t4g.medium, Single-AZ in us-east-1a, gp3 100 GiB storage, 7-day automated backups with point-in-time recovery, encrypted with the customer managed KMS key. The DB subnet group references a second AZ subnet as required by AWS but no workloads run there.

**Consequences:**
- SQL joins support comparison views, search and dashboard queries natively (REQ-05, REQ-10, REQ-11).
- 7-day PITR satisfies RPO 24 h (REQ-20); RTO 8 h is achievable by restoring the latest snapshot to a new instance.
- Single-AZ: an AZ failure means the database is unavailable until AWS recovers the AZ or a snapshot is restored; this risk is accepted for the pilot (REQ-27).
- Move to db.m7g.large when CPU stays above 70% or storage exceeds 80 GiB to support 3x growth.

**Requirements:** REQ-05, REQ-09, REQ-10, REQ-11, REQ-20, REQ-21, REQ-23, REQ-25

**Alternatives considered (rejected, not part of this design):** Amazon DynamoDB (Dashboard aggregations, dispute search and audit joins require flexible SQL; DynamoDB would be chosen only if access patterns were purely key-value.); Amazon Aurora PostgreSQL Serverless v2 (Aurora storage replicates across three AZs by design, which conflicts with the single-AZ data residency intent; Aurora would be chosen post-pilot if higher availability is required.)

### ADR-003: Use Amazon S3 with Athena for billing file storage and querying

**Status:** Accepted

**Context:** Monthly CUR files are ~30 GB and discount breakdowns ~8 GB, growing 3x within 2 years (REQ-01, REQ-23). Files must be queryable within 4 hours of arrival (REQ-18) and retained 7 years with raw files moved to lower-cost storage after 90 days (REQ-21). Loading 38 GB into RDS each month would be expensive and slow.

**Decision:** Use Amazon S3 (Standard tier, SSE-KMS, versioning on, Block Public Access on) as the billing file store with an S3 lifecycle rule transitioning objects to S3 Glacier Flexible Retrieval after 90 days. Use Amazon Athena (engine version 3, Parquet with partitions, one workgroup) to query CUR and discount data in place. An AWS Glue Data Catalog database holds the table definitions.

**Consequences:**
- Athena queries CUR data in S3 without loading it into RDS, meeting the 4-hour ingestion target (REQ-18).
- S3 lifecycle to Glacier after 90 days satisfies the retention and cost requirements (REQ-21, REQ-25).
- Athena query latency depends on partition design; partitioning by billing period and partner keeps p95 under 2 s for typical dispute queries (REQ-16).
- Glue crawler must be run after each monthly ingestion to update partitions; this is automated via EventBridge Scheduler.

**Requirements:** REQ-01, REQ-18, REQ-21, REQ-23, REQ-25

**Alternatives considered (rejected, not part of this design):** Load CUR into RDS PostgreSQL (38 GB/month growing 3x would exceed db.t4g.medium storage and query performance limits within the pilot window; Athena is purpose-built for this scale.); Amazon OpenSearch Service for billing search (Full-text search over billing line items is not required; Athena SQL over partitioned Parquet is sufficient and cheaper (REQ-25).)

### ADR-004: Use Amazon Bedrock with Claude Sonnet 4.6 for AI draft generation

**Status:** Accepted

**Context:** The system must generate explanations and recommended resolutions with citations (REQ-06) at p95 under 45 seconds (REQ-17), producing ~1,500 drafts per month (REQ-15). Generative AI must be provided only through Amazon Bedrock (C-02, REQ-26). The model must handle long context windows to reason over CUR line items and invoice data.

**Decision:** Use Amazon Bedrock with Anthropic Claude Sonnet 4.6 (model ID us.anthropic.claude-sonnet-4-6) on on-demand pricing for draft generation and explanation. Use Amazon Textract (asynchronous DetectDocumentText jobs from S3) for PDF invoice text extraction (REQ-14). Both are invoked by the Dispute API via the Bedrock and Textract API endpoints.

**Consequences:**
- Claude Sonnet 4.6 has a large context window suitable for multi-page CUR comparisons and citation generation (REQ-06).
- On-demand pricing means no commitment; cost scales with actual draft volume, staying within the pilot budget at 1,500 drafts/month (REQ-25).
- p95 45 s target is achievable for typical dispute payloads; very large CUR extracts may need prompt truncation strategies.
- Model access must be approved in Northbridge's account before go-live (OI-01).

**Requirements:** REQ-06, REQ-14, REQ-15, REQ-17, REQ-25, REQ-26

**Alternatives considered (rejected, not part of this design):** Amazon Bedrock with Claude Haiku 4.5 (Haiku is suited to light classification; generating cited, multi-paragraph dispute explanations from large billing contexts requires Sonnet's reasoning capability (REQ-06, AC-02, AC-03).); Amazon SageMaker with a custom model (No custom model training is required; Bedrock managed inference removes operational overhead and satisfies C-02.)

### ADR-005: Use AWS Transfer Family (SFTP) for LedgerOne nightly invoice ingestion

**Status:** Accepted

**Context:** LedgerOne pushes a nightly CSV export (~200 MB) via SFTP authenticated by key to a Northbridge-controlled endpoint (Section 5). The system must import this file within 30 minutes and quarantine malformed files with an alert (REQ-02). No managed AWS SFTP service is listed on the service sheet; the closest managed option is AWS Transfer Family.

**Decision:** Use AWS Transfer Family with an SFTP endpoint backed by S3 (the Billing Data S3 bucket landing prefix) in us-east-1a. LedgerOne authenticates with an SSH key stored in Secrets Manager. An S3 event notification triggers the ingestion Lambda when a new invoice file lands.

**Consequences:**
- Fully managed SFTP; no EC2 bastion or self-managed SSH server required (C-01).
- Files land directly in S3, triggering the existing Lambda ingestion pipeline (REQ-02, REQ-18).
- AWS Transfer Family has a per-protocol-endpoint hourly charge; this is the only viable managed SFTP option on AWS.
- SFTP endpoint is internet-facing by default; restrict source IPs to LedgerOne's known egress IPs via the Transfer Family endpoint policy.

**Requirements:** REQ-02, REQ-18, REQ-26

**Alternatives considered (rejected, not part of this design):** EC2-hosted OpenSSH server (Violates C-01 (no self-managed software on VMs unless no managed option exists); AWS Transfer Family is the managed equivalent.); LedgerOne pushing directly to S3 via HTTPS (LedgerOne's nightly export specification mandates SFTP push (Section 5); changing the export mechanism is out of scope.)

### ADR-006: Use Amazon Cognito with SAML federation for user authentication and RBAC

**Status:** Accepted

**Context:** All users must authenticate through Northbridge SSO (SAML 2.0) with MFA enforced by the identity provider (REQ-22, C-05). The system must enforce three roles (agent, Billing Operations Lead, finance auditor) and hide margin fields from read-only roles (REQ-12). ~49 named users total.

**Decision:** Use Amazon Cognito (1 user pool, Essentials tier) federated to Northbridge SSO via SAML 2.0. Role and name are mapped from SAML assertion attributes to Cognito groups (agent, lead, auditor). The Dispute API validates the Cognito JWT on every request and enforces field-level access based on the group claim.

**Consequences:**
- MFA is enforced by Northbridge SSO before the SAML assertion is issued; Cognito does not need to manage MFA (REQ-22).
- Cognito Essentials tier supports SAML federation and group claims at lower cost than Plus tier (REQ-25).
- Role changes require updating the SAML attribute in Northbridge SSO and re-federating; no direct Cognito user management needed.
- If Northbridge SSO is unavailable, users cannot log in; this is accepted as SSO is a corporate dependency.

**Requirements:** REQ-12, REQ-22, REQ-26

**Alternatives considered (rejected, not part of this design):** AWS IAM Identity Center (IAM Identity Center is designed for AWS console and CLI access, not for application-level RBAC with custom field-hiding logic; Cognito is the correct service for application sign-in with JWT issuance.); Custom JWT service on Fargate (Reinvents managed identity; violates the preference for AWS-native managed services (C-01).)

### ADR-007: Use AWS Lambda for event-driven ingestion, webhook handling and notifications

**Status:** Accepted

**Context:** Billing file ingestion (monthly, triggered by S3 event), LedgerOne invoice import (nightly, triggered by S3 event), HelpTrack webhook receipt (REQ-04), HelpTrack response posting with retry (REQ-08), PDF text extraction orchestration (REQ-14) and agent email notifications (REQ-13) are all short-duration, event-driven tasks that run infrequently and complete well within 15 minutes. Running these on always-on Fargate tasks would waste cost (REQ-25).

**Decision:** Use AWS Lambda (Python 3.13, arm64, 1,024 MB, 300 s timeout) for all event-driven functions: billing file validation and Glue crawler trigger, LedgerOne invoice import and quarantine, HelpTrack webhook receiver, HelpTrack response poster with SQS-backed retry, PDF Textract orchestration, and agent email dispatch via SES. EventBridge Scheduler triggers the nightly invoice import check. An SQS queue with a dead-letter queue buffers HelpTrack outbound posts.

**Consequences:**
- Pay-per-invocation cost is negligible at these volumes; well within the pilot budget (REQ-25).
- 1,024 MB / arm64 provides sufficient memory for parquet validation and JSON processing without a full vCPU.
- 300 s timeout covers the LedgerOne 200 MB CSV import and Glue crawler trigger within the 30-minute window (REQ-18).
- Lambda functions in the private subnet require a NAT Gateway to reach Bedrock, SES, HelpTrack and other external endpoints.
- Scale to 3x volume is automatic with Lambda concurrency (REQ-23).

**Requirements:** REQ-01, REQ-02, REQ-04, REQ-08, REQ-13, REQ-14, REQ-18, REQ-25

**Alternatives considered (rejected, not part of this design):** Fargate tasks for ingestion (Ingestion runs once per month (billing) or once per night (invoices); always-on tasks would waste cost and add operational complexity (REQ-25).); AWS Glue for LedgerOne CSV import (200 MB CSV import with quarantine logic is simple Python; Glue adds cost and complexity for a single-file nightly job.)

### ADR-008: Use Application Load Balancer and CloudFront for web and API entry

**Status:** Accepted

**Context:** The web application is served to ~25 concurrent users on corporate networks and VPN (REQ-16, REQ-19). The entry point must terminate TLS, route to the Fargate API, and serve the static React front-end. The system is not publicly exposed to the internet beyond the corporate perimeter, but CloudFront provides TLS termination, caching of static assets and a single domain entry point.

**Decision:** Use Amazon CloudFront (1 distribution, PriceClass_100) with two origins: S3 (origin access control) for the static web application assets, and the Application Load Balancer for API requests. The ALB is internet-facing with an HTTPS 443 listener and an ACM certificate, targets in us-east-1a. The ALB subnet group references a second AZ subnet as required by AWS with no workloads there. Static assets are stored in the Web Assets S3 bucket.

**Consequences:**
- CloudFront caches static assets at edge, reducing ALB and Fargate load and improving p95 page load times (REQ-16).
- Single CloudFront domain simplifies CORS and Cognito callback URL configuration.
- PriceClass_100 covers North America and Europe, sufficient for corporate VPN users (REQ-25).
- ALB is internet-facing; security group restricts inbound 443 to CloudFront managed prefix list only, so no direct public access to the ALB.

**Requirements:** REQ-16, REQ-19, REQ-22, REQ-25

**Alternatives considered (rejected, not part of this design):** Amazon API Gateway HTTP API instead of ALB (The Fargate API has persistent database connections and streaming responses for AI drafts; ALB is the correct front-end for Fargate. API Gateway would be chosen if the backend were Lambda.); Serve static assets from Fargate (Mixing static and dynamic serving on Fargate wastes compute and complicates deployments; S3 + CloudFront is the standard pattern.)

## 4. Network layout

The solution runs in AWS Region us-east-1. It uses one VPC, Billing Dispute VPC (10.0.0.0/16), in a single Availability Zone, us-east-1a. The Public subnet (us-east-1a), in Billing Dispute VPC (10.0.0.0/16), contains the Application Load Balancer and the NAT Gateway. The Private subnet (us-east-1a), in Billing Dispute VPC (10.0.0.0/16), contains the Ingestion Lambda Functions, the Dispute API, the Integration Lambda Functions, the Dispute Database and the VPC Endpoints. Region-level services used: CloudFront Distribution, SFTP Transfer Endpoint, Billing Data S3 Bucket, Glue Data Catalog, Athena Workgroup, Bedrock AI Model, Textract Service, Cognito User Pool, SQS Retry Queue, API Gateway Webhook Endpoint, Web Assets S3 Bucket, Secrets Manager, KMS Customer Managed Key, CloudWatch Observability, CloudTrail Audit Trail, SES Email Service and ECR Repository. Outside AWS, the solution involves: Billing Agent / Lead / Auditor, HelpTrack, LedgerOne ERP and Northbridge SSO.

## 5. Components

| ID | Component | AWS service | SKU / configuration | Qty | Placement | Purpose |
|---|---|---|---|---|---|---|
| C-01 | Billing Agent / Lead / Auditor | User | Human actor — no AWS resource | 1 | External | Represents the three user classes (billing agent, Billing Operations Lead, finance auditor) who access the system via the web UI. |
| C-02 | CloudFront Distribution | Amazon CloudFront | CloudFront, PriceClass_100, 2 origins (S3 OAC + ALB) | 1 | Region | Terminates TLS, caches static web application assets from S3, and forwards API requests to the Application Load Balancer. |
| C-03 | Application Load Balancer | Application Load Balancer | ALB, internet-facing, HTTPS 443, ACM certificate, single-AZ targets | 1 | Public subnet (us-east-1a) | Terminates HTTPS from CloudFront and load-balances requests across Dispute API Fargate tasks in us-east-1a. |
| C-04 | NAT Gateway | NAT Gateway | NAT Gateway, public, us-east-1a, 1 Elastic IP | 1 | Public subnet (us-east-1a) | Provides outbound internet connectivity for Lambda functions and Fargate tasks in the private subnet to reach HelpTrack REST API, Bedrock, SES and other external endpoints. |
| C-05 | SFTP Transfer Endpoint | AWS Transfer Family | AWS Transfer Family, SFTP, public endpoint, S3-backed | 1 | Region | Provides the Northbridge-controlled SFTP endpoint to which LedgerOne pushes the nightly invoice CSV export, landing files directly into the Billing Data S3 bucket. |
| C-06 | Ingestion Lambda Functions | AWS Lambda | Lambda, python3.13, arm64, 1,024 MB, 300 s timeout, VPC-attached | 1 | Private subnet (us-east-1a) | Validates and catalogues monthly CUR and discount billing files, triggers Glue crawler to update Athena partitions, imports and quarantines LedgerOne nightly invoice CSV, and orchestrates Textract PDF extraction jobs. |
| C-07 | Dispute API | AWS Fargate | Fargate Linux/ARM64, 1 vCPU / 2 GB, 2 tasks | 2 | Private subnet (us-east-1a) | Serves the REST API for dispute creation, comparison, AI draft orchestration, agent review and approval, search and dashboard; validates Cognito JWTs and enforces RBAC including margin field hiding. |
| C-08 | Integration Lambda Functions | AWS Lambda | Lambda, python3.13, arm64, 1,024 MB, 300 s timeout, VPC-attached | 1 | Private subnet (us-east-1a) | Receives and deduplicates HelpTrack outbound webhook payloads, posts approved responses to HelpTrack REST API with SQS-backed retry, and dispatches agent email notifications via SES when a draft is ready. |
| C-09 | Dispute Database | Amazon RDS | db.t4g.medium, PostgreSQL 17, gp3 100 GiB, Single-AZ, 7-day backups | 1 | Private subnet (us-east-1a) | Stores disputes, partners, invoices (normalised), AI drafts, audit events and agent actions; supports search, dashboard queries and immutable audit trail. |
| C-10 | Billing Data S3 Bucket | Amazon S3 | S3 Standard, SSE-KMS, versioning on, Glacier Flexible Retrieval after 90 days | 1 | Region | Receives monthly CUR and discount breakdown files from Northbridge staff/automation and nightly LedgerOne invoice CSVs via Transfer Family; serves as the Athena query source and long-term archive. |
| C-11 | Glue Data Catalog | AWS Glue | Glue 5.0, G.1X x 2 workers, Data Catalog database, crawler on-demand | 1 | Region | Maintains the Glue Data Catalog table definitions and partition metadata for CUR and discount Parquet files in S3 so that Athena can query them immediately after ingestion. |
| C-12 | Athena Workgroup | Amazon Athena | Athena engine v3, billing-dispute-wg workgroup, Parquet partitioned | 1 | Region | Executes SQL queries over CUR and discount breakdown Parquet files in S3 to retrieve billing line items relevant to a dispute for comparison and AI context. |
| C-13 | Bedrock AI Model | Amazon Bedrock | Bedrock, Anthropic Claude Sonnet 4.6 (us.anthropic.claude-sonnet-4-6), on-demand | 1 | Region | Generates AI-drafted dispute explanations and recommended resolutions with citations to billing line items and agreement clauses, invoked by the Dispute API for each draft request. |
| C-14 | Textract Service | Amazon Textract | Textract, DetectDocumentText, asynchronous, S3 input | 1 | Region | Extracts plain text from PDF invoices attached to disputes, making the content available to the comparison view and AI draft generation. |
| C-15 | Cognito User Pool | Amazon Cognito | Cognito, Essentials tier, 1 user pool, SAML 2.0 federation | 1 | Region | Federates Northbridge SSO SAML assertions to issue JWTs with role group claims; the Dispute API validates these tokens to enforce RBAC and margin field hiding. |
| C-16 | SQS Retry Queue | Amazon SQS | SQS Standard queue + DLQ, 4-day retention, 5 max receives | 1 | Region | Decouples and buffers approved HelpTrack response post requests from the Dispute API, enabling the Integration Lambda to retry on transient HelpTrack API failures. |
| C-17 | API Gateway Webhook Endpoint | Amazon API Gateway | API Gateway HTTP API, regional, Lambda integration | 1 | Region | Exposes a public HTTPS endpoint for the HelpTrack outbound webhook, authenticates payloads via HMAC signature, and invokes the Integration Lambda to create or update disputes. |
| C-18 | Web Assets S3 Bucket (assumed) | Amazon S3 | S3 Standard, SSE-KMS, versioning on, OAC-only access | 1 | Region | Stores the compiled static React web application assets (HTML, JS, CSS) served to users via CloudFront with origin access control. |
| C-19 | Secrets Manager | AWS Secrets Manager | Secrets Manager, 4 secrets, RDS rotation enabled, KMS encrypted | 1 | Region | Centrally stores and rotates all credentials and API keys used by Fargate tasks and Lambda functions, eliminating hard-coded secrets. |
| C-20 | KMS Customer Managed Key | AWS KMS | KMS, 1 customer managed symmetric key, annual rotation | 1 | Region | Provides a single customer managed encryption key used by RDS, S3 buckets, Secrets Manager and CloudWatch Logs to satisfy the Confidential-Financial data classification requirement. |
| C-21 | CloudWatch Observability | Amazon CloudWatch | CloudWatch, 30-day log retention, alarms on errors and saturation, 1 dashboard | 1 | Region | Provides centralised logging, metrics and alarms for ingestion pipelines, AI draft latency, integration errors and infrastructure saturation; routes alarms to the operations team. |
| C-22 | CloudTrail Audit Trail | AWS CloudTrail | CloudTrail, multi-Region trail, S3 delivery, log file validation on | 1 | Region | Records all AWS API calls for security audit and compliance, with log file validation ensuring tamper-evidence for the 7-year retention requirement. |
| C-23 | SES Email Service | Amazon SES | SES, 1 verified domain identity, DKIM on, shared IP | 1 | Region | Sends email notifications to assigned billing agents when an AI draft is ready for review, using a Northbridge-domain sender address. |
| C-24 | VPC Endpoints (assumed) | AWS PrivateLink | PrivateLink: S3 and DynamoDB gateway endpoints; Secrets Manager, ECR API, ECR DKR, CloudWatch Logs interface endpoints | 1 | Private subnet (us-east-1a) | Provides private network paths from the private subnet to S3, Secrets Manager, ECR and CloudWatch Logs, reducing NAT Gateway data transfer costs and keeping traffic off the public internet. |
| C-25 | HelpTrack | HelpTrack | External system — HelpTrack ticketing platform | 1 | External Systems | Northbridge's ticketing system; sends dispute webhooks to the system and receives approved responses via REST API. |
| C-26 | LedgerOne ERP | LedgerOne | External system — LedgerOne ERP | 1 | External Systems | Northbridge's ERP; pushes nightly invoice CSV exports to the SFTP Transfer Family endpoint. |
| C-27 | Northbridge SSO | Northbridge SSO | External system — Northbridge corporate SAML 2.0 IdP | 1 | External Systems | Issues SAML assertions with role and name attributes to Cognito for user authentication and RBAC; enforces MFA. |
| C-28 | ECR Repository (assumed) | Amazon ECR | ECR private repository, scan on push, last 10 images lifecycle policy | 1 | Region | Stores and serves the Dispute API container images to ECS Fargate tasks; scan on push detects vulnerabilities before deployment. |

## 6. Request and data flow

1. The Billing Agent / Lead / Auditor accesses the web application through the CloudFront Distribution over HTTPS. (F-01; HTTPS 443)
2. Northbridge SSO issues a SAML 2.0 assertion to the Cognito User Pool to authenticate users and supply role attributes. (F-02; SAML 2.0 443)
3. The CloudFront Distribution fetches static web application assets from the Web Assets S3 Bucket via origin access control. (F-03; HTTPS 443)
4. The CloudFront Distribution forwards API requests to the Application Load Balancer over HTTPS. (F-04; HTTPS 443)
5. The Application Load Balancer routes HTTPS requests to the Dispute API Fargate tasks in us-east-1a. (F-05; HTTP 8080)
6. The Dispute API validates Cognito JWTs against the Cognito User Pool JWKS endpoint on every authenticated request. (F-06; HTTPS 443)
7. The Dispute API reads and writes disputes, partners, AI drafts and audit events in the Dispute Database. (F-07; PostgreSQL 5432)
8. The Dispute API queries the Athena Workgroup to retrieve CUR and discount line items relevant to a dispute. (F-08; HTTPS (Athena API) 443)
9. The Athena Workgroup scans partitioned Parquet files in the Billing Data S3 Bucket to execute billing line item queries. (F-09; S3 API 443)
10. The Dispute API invokes the Bedrock AI Model with dispute context and retrieved line items to generate an explanation and recommended resolution. (F-10; HTTPS (Bedrock API) 443)
11. The Dispute API enqueues an approved response message to the SQS Retry Queue for reliable delivery to HelpTrack. (F-11; HTTPS (SQS API) 443)
12. The SQS Retry Queue triggers the Integration Lambda Functions to post the approved response to HelpTrack. (F-12; SQS event 443)
13. The Integration Lambda Functions post the approved dispute response to the HelpTrack REST API and record the outcome. (F-13; HTTPS (REST) 443)
14. HelpTrack sends outbound webhook payloads for new and updated disputes to the API Gateway Webhook Endpoint. (F-14; HTTPS 443)
15. The API Gateway Webhook Endpoint invokes the Integration Lambda Functions to process and deduplicate incoming HelpTrack webhook payloads. (F-15; Lambda invoke 443)
16. The Integration Lambda Functions create or update the matching dispute record in the Dispute Database after processing a HelpTrack webhook. (F-16; PostgreSQL 5432)
17. LedgerOne ERP pushes the nightly invoice CSV export to the SFTP Transfer Endpoint using SSH key authentication. (F-17; SFTP 22)
18. The SFTP Transfer Endpoint writes the received invoice CSV file to the Billing Data S3 Bucket under the /invoices/ prefix. (F-18; S3 API 443)
19. The Billing Data S3 Bucket emits an S3 event notification to the Ingestion Lambda Functions when a new billing or invoice file arrives. (F-19; S3 event notification 443)
20. The Ingestion Lambda Functions trigger the Glue Data Catalog crawler after validating a new monthly billing file to update Athena partition metadata. (F-20; HTTPS (Glue API) 443)
21. The Ingestion Lambda Functions write normalised invoice header and line records to the Dispute Database after a successful LedgerOne import. (F-21; PostgreSQL 5432)
22. The Ingestion Lambda Functions submit asynchronous Textract DetectDocumentText jobs for PDF invoice attachments stored in the Billing Data S3 Bucket. (F-22; HTTPS (Textract API) 443)
23. The Textract Service reads PDF invoice attachment files from the Billing Data S3 Bucket to perform text extraction. (F-23; S3 API 443)
24. The Integration Lambda Functions invoke Amazon SES to send a draft-ready email notification to the assigned billing agent. (F-24; HTTPS (SES API) 443)
25. The Dispute API retrieves database credentials and HelpTrack OAuth tokens from Secrets Manager at startup and on rotation. (F-25; HTTPS (Secrets Manager API) 443)
26. The Glue Data Catalog crawler scans the Billing Data S3 Bucket to infer and register partition schemas for Athena queries. (F-26; S3 API 443)

## 7. Security and operations

- All workloads (Dispute API Fargate tasks, Ingestion Lambda Functions, Integration Lambda Functions) run in the private subnet; only the Application Load Balancer and API Gateway Webhook Endpoint are internet-reachable. The ALB security group accepts inbound HTTPS 443 exclusively from the CloudFront managed prefix list; no rule permits 0.0.0.0/0 on any other port.
- The Dispute Database (RDS), Billing Data S3 Bucket, Web Assets S3 Bucket and Secrets Manager are accessible only from within the VPC via security group rules referencing the Fargate and Lambda security groups, or via VPC endpoints. S3 Block Public Access is on for both buckets.
- All data at rest is encrypted with the KMS Customer Managed Key: RDS storage, both S3 buckets, Secrets Manager secrets, CloudWatch Logs log groups and CloudTrail logs. All data in transit uses TLS 1.2 or higher.
- Each workload has a dedicated least-privilege IAM execution role scoped to specific resource ARNs: the Dispute API role permits RDS connect, Athena query, Bedrock InvokeModel on the Claude Sonnet 4.6 model ARN, Secrets Manager GetSecretValue on its named secrets, and S3 GetObject on the billing bucket; the Ingestion Lambda role permits S3 GetObject/PutObject on the billing bucket, Glue StartCrawler, Textract StartDocumentTextDetection, and RDS connect; the Integration Lambda role permits SQS ReceiveMessage/DeleteMessage on the retry queue, SES SendEmail, and RDS connect. No wildcard actions and no long-lived access keys are used.
- Cognito User Pool enforces SAML 2.0 federation to Northbridge SSO; MFA is enforced by the Northbridge SSO identity provider before the SAML assertion is issued. The Dispute API validates the Cognito JWT on every request and enforces role-based field-level access: the finance auditor group claim causes the API to redact margin fields from all responses (REQ-12).
- HelpTrack webhook payloads are authenticated by HMAC signature validation in the API Gateway Lambda authorizer before the Integration Lambda is invoked. HelpTrack OAuth client credentials are stored in Secrets Manager and never hard-coded.
- The SFTP Transfer Family endpoint restricts accepted connections to the known LedgerOne egress IP addresses via a resource policy. The LedgerOne SSH key is stored in Secrets Manager.
- CloudTrail records all AWS management and data-plane API calls to the Billing Data S3 Bucket with log file validation enabled, providing a tamper-evident audit trail for the 7-year retention requirement. CloudWatch Logs retains application logs for 30 days; long-term audit events are stored in the Dispute Database (RDS) with append-only row-level security enforced at the application layer.
- CloudWatch alarms notify the operations team via SNS on: Ingestion Lambda errors, Integration Lambda DLQ depth > 0, Dispute API 5xx error rate > 1%, AI draft p95 latency > 40 s, and RDS CPU > 80%. The single operational dashboard (CloudWatch Observability) surfaces all key metrics.
- RDS automated backups are retained for 7 days with point-in-time recovery enabled, satisfying RPO 24 h. A restore to a new db.t4g.medium instance in us-east-1a achieves RTO 8 h. S3 versioning on the Billing Data S3 Bucket protects against accidental deletion of billing files. CloudTrail logs are protected by S3 Object Lock (governance mode) to prevent deletion within the 7-year retention window.
- Infrastructure is defined as code (AWS CDK or CloudFormation) and deployed through a CI/CD pipeline stored in a version-controlled repository, enabling repeatable deployments across development, test and pilot environments (REQ-29).

## 8. Assumptions

| ID | Assumption | Reason |
|---|---|---|
| ASM-01 | The Dispute API Fargate tasks are sized at 1 vCPU / 2 GB with 2 desired tasks. | SRS states ~25 concurrent users at peak (Section 2.2) but does not specify throughput or task count. Two tasks at 1 vCPU / 2 GB provide headroom for month-end peak (60 disputes/day, REQ-15) and allow one task to absorb load during a rolling deployment. |
| ASM-02 | The RDS gp3 storage is provisioned at 100 GiB. | SRS gives ~870 disputes/month, ~1,500 drafts/month and ~25,000 audit events/month (Section 6) but does not specify database storage. 100 GiB covers 2 years of growth at 3x (REQ-23) with room for indexes and WAL, staying above the 20 GiB minimum. |
| ASM-03 | The Web Assets S3 Bucket uses SSE-KMS with the customer managed key. | SRS classifies all data as Confidential-Financial (Section 6) and requires encryption at rest (REQ-22, C-05); the KMS CMK is applied consistently to all buckets. |
| ASM-04 | The Cognito access token validity is set to 1 hour and the refresh token to 8 hours. | SRS does not specify token lifetimes. 1-hour access tokens limit the blast radius of a stolen token; 8-hour refresh tokens cover a full business-hours session (07:00–19:00, REQ-19) without requiring re-authentication. |
| ASM-05 | The Glue crawler is triggered on-demand by the Ingestion Lambda after each monthly billing file arrival rather than on a fixed schedule. | SRS requires files to be queryable within 4 hours of arrival (REQ-18); a fixed daily schedule could delay partition registration. On-demand trigger ensures Athena partitions are updated immediately after validation. |
| ASM-06 | Partner agreement terms are stored as structured text in the Dispute Database (RDS) and/or as extracted text from PDFs via Textract. | OI-04 notes that it is unconfirmed whether partner agreements are structured data or PDF only. The design accommodates both: structured data in RDS and PDF text via Textract (REQ-14), so no redesign is needed when OI-04 is resolved. |
| ASM-07 | The API Gateway Webhook Endpoint uses an HTTP API (not REST API) with a Lambda authorizer for HMAC validation. | SRS requires webhook payload authentication (Section 5) but does not specify API Gateway tier. HTTP API is sufficient for webhook ingestion and is cheaper than REST API (REQ-25); WAF is not added as the webhook is authenticated by HMAC and not a stated compliance requirement. |
| ASM-08 | CloudTrail logs are stored in the Billing Data S3 Bucket under a /cloudtrail/ prefix rather than a dedicated bucket. | SRS requires all data to remain in us-east-1 (C-04) and a 7-year audit trail (REQ-21). Consolidating CloudTrail logs into the existing bucket with a lifecycle policy reduces the number of S3 buckets and simplifies KMS key policy management. |
| C-18 | Web Assets S3 Bucket: S3 Standard, SSE-KMS, versioning on, OAC-only access | SRS requires a web UI (FR-03, FR-07) but does not specify the static asset hosting mechanism; S3 + CloudFront OAC is the standard AWS pattern. |
| C-24 | VPC Endpoints: PrivateLink: S3 and DynamoDB gateway endpoints; Secrets Manager, ECR API, ECR DKR, CloudWatch Logs interface endpoints | Private subnet workloads access S3 and Secrets Manager frequently; gateway and interface endpoints reduce NAT costs and align with the private-networking constraint (C-05, REQ-28). |
| C-28 | ECR Repository: ECR private repository, scan on push, last 10 images lifecycle policy | Fargate tasks require a container registry; ECR is the standard AWS-native registry (C-01, ADR-001). |

## 9. Open questions

- OI-01: Which Amazon Bedrock model (confirmed model ID and data handling terms) has been approved by Northbridge InfoSec for use with Confidential-Financial billing data? The design assumes Claude Sonnet 4.6 (us.anthropic.claude-sonnet-4-6); a different model ID would change the Bedrock component configuration and potentially the draft quality and latency.
- OI-02: What is the production availability target and multi-AZ requirement after the pilot? If 99.9% or higher is required, the design must be revised to add Multi-AZ RDS, cross-AZ Fargate tasks, and a second NAT Gateway, which changes the architecture and cost significantly.
- OI-03: What are the HelpTrack API rate limits and webhook retry behaviour? If the rate limit is below the peak posting rate (~60 disputes/day at month-end), the SQS visibility timeout and retry count on the SQS Retry Queue may need adjustment to avoid DLQ overflow.
- OI-04: Are partner agreements available as structured data (CSV/JSON) or only as PDF documents? If structured, they can be loaded directly into RDS; if PDF-only, Textract extraction is the only path and the Ingestion Lambda must handle agreement PDFs in addition to invoice PDFs.
