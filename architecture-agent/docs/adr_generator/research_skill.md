# Research digest: ADR generation for single-AZ AWS designs

Every bullet ends with its source URL. Items I could not verify are listed in the final section.

## (a) ADR structure and writing practice

- An ADR records one architecturally significant decision with its context, the decision and its consequences. Minimum content is context, decision, consequences. https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- Significant decisions cover structure, non-functional requirements, dependencies, interfaces and construction techniques. Functional and non-functional requirements are the usual inputs. https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- An ADR focuses on the reason for the decision, not the implementation. This stops later readers from overruling a decision they do not understand. https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- ADR lifecycle states are Proposed, Accepted, Rejected, Superseded. An accepted ADR is immutable; a change needs a new ADR that supersedes it. A rejection records its reason. https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- Nygard format: Title (short noun phrase), Context (forces at play, value-neutral facts), Decision (full sentences, active voice, "We will ..."), Status, Consequences (positive, negative and neutral). https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- Nygard length rule: one or two pages, one significant decision per record, written to a future reader. https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
- MADR sections: Title, Context and Problem Statement (2-3 sentences, scope stated), Decision Drivers, Considered Options, Decision Outcome with justification (only option meeting a must-have, resolves a key force, or best overall), Consequences (good and bad), Confirmation, Pros and Cons per option, More Information (criteria for revisiting). https://github.com/adr/madr/blob/main/template/adr-template.md
- The wa-builder ADR mode (aws-samples) asks for at least two options, evidence-based effort, ordered decision drivers, one clear decision statement, and for every rejected option a "would choose this if" trigger. https://github.com/aws-samples/sample-well-architected-skills-and-steering/blob/main/skills/wa-builder/SKILL.md
- The same ADR mode says an ADR is a decision log, not a sales pitch: record real trade-offs, give specific rather than vague effort claims, set review triggers with concrete thresholds, and do not pad the Well-Architected pillar table with obvious impacts. https://github.com/aws-samples/sample-well-architected-skills-and-steering/blob/main/skills/wa-builder/SKILL.md
- The deploy skill (awslabs) states its rule to pick the most straightforward services, explain each choice briefly, and not ask questions that have an obvious answer. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/SKILL.md

## (b) Service-selection principles

- Lambda vs Fargate: Lambda for event-driven, unpredictable or wait-heavy work; Fargate for containerised apps, persistent processes and long-running services. Lambda caps at 15 minutes per invocation; Fargate has no hard limit. https://docs.aws.amazon.com/decision-guides/latest/decision-guides/fargate-or-lambda.html
- Fargate over Lambda when the process is monolithic and hard to split, runs on a predictable schedule, needs more than 15 minutes, or when refactoring effort must be minimal. https://repost.aws/knowledge-center/lambda-reduce-costs
- Lambda up to 10,240 MB memory and 6 vCPU; Fargate up to 32 vCPU and 244 GiB. Lambda packages: 250 MB unzipped or 10 GB container image. https://docs.aws.amazon.com/decision-guides/latest/decision-guides/fargate-or-lambda.html
- Step Functions for multi-step flows where each step is under 15 minutes; AWS Batch for jobs needing dynamic resource allocation or dependencies. https://repost.aws/knowledge-center/lambda-reduce-costs
- Container services: App Runner for the simplest internet deployment, ECS when more control over network and security is needed, EKS when Kubernetes control is needed and the team can run cluster upgrades (three Kubernetes releases a year). https://docs.aws.amazon.com/decision-guides/latest/containers-on-aws-how-to-choose/choosing-aws-container-service.html
- Default compute matrix: web and API services on Fargate plus ALB; serverless APIs on Lambda plus API Gateway; background workers on Fargate (Lambda if under 15 minutes); scheduled jobs on EventBridge plus Lambda (Fargate if long); static sites on Amplify Hosting or S3 plus CloudFront. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/defaults.md
- Do not recommend App Runner for new workloads (stated as maintenance mode); use ECS Express Mode. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/SKILL.md
- DynamoDB when access patterns are known up front, predictable millisecond latency is needed, or data is key-value or document. RDS when SQL with joins and aggregations is needed. https://aws.amazon.com/compare/dynamodb-and-rds/
- Database defaults: PostgreSQL/MySQL on Aurora Serverless v2 (RDS if "simple RDS" is wanted), DynamoDB for key-value, ElastiCache Serverless for caching, OpenSearch Serverless for full-text search, Secrets Manager for secrets, Parameter Store for configuration. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/defaults.md
- Database choice starts from data model, access pattern and operating model; use purpose-built engines rather than one database for everything. https://docs.aws.amazon.com/decision-guides/latest/decision-guides/databases-on-aws-how-to-choose.html
- Aurora storage is replicated six ways across three AZs by design, so Aurora cannot be made truly single-AZ at the storage layer. https://docs.aws.amazon.com/rds/latest/auroraextendedcontent/aurora-faq-availability-and-durability.html
- SQS: pull-based buffering, persistence up to 14 days, FIFO option. SNS: push fan-out to HTTP, email, SMS, SQS and Lambda. EventBridge: content-based routing, schedules, SaaS and cross-account events, no ordering guarantee. https://docs.aws.amazon.com/decision-guides/latest/decision-guides/sns-or-sqs-or-eventbridge.html
- Storage: S3 for objects, backups, data lakes and static hosting; EFS for shared file access across many clients; EBS for lowest-latency block access from a single EC2 instance. https://aws.amazon.com/efs/faq/
- GenAI: Bedrock for RAG, prompt engineering and foundation-model customisation; SageMaker when training a model from scratch. https://repost.aws/articles/ARg2nreO9URO2VyMnRKOt3ZA/building-a-decision-framework-to-select-the-right-aws-ml-service-for-your-workload
- Bedrock Knowledge Base vector stores: OpenSearch (managed or Serverless) for hybrid search, advanced queries and real-time indexing; Aurora PostgreSQL with pgvector when vectors sit beside relational data; S3 Vectors for cost-effective straightforward similarity search (up to 90 percent lower storage cost). https://aws.amazon.com/blogs/machine-learning/selecting-a-vector-store-for-amazon-bedrock-knowledge-bases/
- ALB spans at least two AZs (two subnets); NLB can use a single AZ. https://docs.aws.amazon.com/sdk-for-kotlin/api/latest/elasticloadbalancingv2/aws.sdk.kotlin.services.elasticloadbalancingv2.model/-set-subnets-request/subnet-mappings.html

## (c) Well-Architected rules for single-AZ workloads

Security
- Encrypt all storage at rest; enforce TLS 1.2 or higher; HTTP redirects to HTTPS; S3 private unless serving static content (CloudFront via origin access control, not legacy OAI). https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Compute and databases in private subnets; the load balancer is the only public component; databases get no public IPs; Lambda joins a VPC only when it needs VPC resources. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Security groups deny by default: ALB 443 inbound, app tier accepts only from the upstream security group on the app port, database accepts only from the app security group. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Least-privilege IAM with specific ARNs and conditions, roles not access keys, no admin roles created by the design. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Secrets in Secrets Manager, non-secret config in Parameter Store; inject at runtime, never bake into images. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Gateway endpoints for S3 and DynamoDB carry no hourly or data-processing charge and avoid NAT data costs; interface endpoints cost extra. https://repost.aws/knowledge-center/vpc-reduce-nat-gateway-transfer-costs
- Audit and logging: CloudTrail at account level, VPC Flow Logs, ALB access logs, database audit logs where the requirement justifies them. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md

Reliability inside one AZ
- Reliability is expressed as backup and restore: define RTO and RPO, back up data, secure and encrypt backups, test restores. Lower RTO/RPO costs more. https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_backing_up_data_secured_backups_data.html
- RDS automated backups: retention 0-35 days; default 7 days from the console, 1 day via API or CLI, so set it explicitly. https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.BackupRetention.html
- Aurora automated backup retention is 1-35 days; snapshots extend retention. https://aws.amazon.com/blogs/storage/using-aws-backup-to-protect-amazon-aurora-databases/
- Multi-AZ for stateful services is a production-hardening step in the deploy skill; dev-sized designs use single-AZ. A single-AZ layout is explicitly acceptable for dev sizing, including a single-AZ ALB. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/defaults.md
- NAT gateways are zonal; keep resources in the same AZ as the NAT gateway to avoid cross-AZ data charges. https://repost.aws/knowledge-center/vpc-reduce-nat-gateway-transfer-costs

Cost optimisation
- Select right type and quantity, understand spend, avoid unnecessary cost. https://aws.amazon.com/architecture/well-architected/
- gp3 costs 20 percent less per GiB than gp2 and includes 3,000 IOPS and 125 MiB/s baseline. https://docs.aws.amazon.com/prescriptive-guidance/latest/optimize-costs-microsoft-workloads/ebs-migrate-gp2-gp3.html
- Lambda arm64 runs on Graviton2 and is available in most Regions. https://docs.aws.amazon.com/lambda/latest/dg/foundation-arch.html

Operational excellence, performance, sustainability
- Operational excellence: monitor, automate changes, define standards for daily operations (logs and metrics for every workload). https://aws.amazon.com/architecture/well-architected/
- Performance efficiency: choose resource types and sizes by workload requirement, monitor, and revisit as demand changes. https://aws.amazon.com/architecture/well-architected/
- Sustainability: maximise utilisation, use managed services, size to actual workload need rather than maximum uptime and durability. https://docs.aws.amazon.com/prescriptive-guidance/latest/neptune-analytics-well-architected-framework/sustainability-pillar.html

## (d) Sizing starting points (starting points only, never targets)

- Fargate valid task sizes: 0.25 vCPU with 0.5-2 GB; 0.5 vCPU with 1-4 GB; 1 vCPU with 2-8 GB; 2 vCPU with 4-16 GB; 4 vCPU with 8-30 GB. https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html
- Deploy-skill dev defaults: Fargate 0.5 vCPU / 1 GB; Aurora Serverless v2 0.5-2 ACU; Elastic Beanstalk 1x t3.small. Production: Fargate 1+ vCPU / 2+ GB, Aurora 2-16+ ACU. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/defaults.md
- RDS allocated storage minimum: 20 GiB for gp2/gp3 on MySQL, MariaDB and PostgreSQL (maximum 64 TiB); 100 GiB for io1/io2. https://docs.aws.amazon.com/sdk-for-kotlin/api/latest/rds/aws.sdk.kotlin.services.rds.model/-create-db-instance-request/-builder/allocated-storage.html
- Lambda memory range 128-10,240 MB; 1,769 MB is about one vCPU. 128 MB suits only lightweight routing/transform functions; more memory when importing libraries, layers or S3/EFS data. https://docs.aws.amazon.com/help-panel/lambda/latest/console/configuration-memory.html and https://repost.aws/knowledge-center/lambda-memory-compute-cost
- EBS gp3 baseline 3,000 IOPS and 125 MiB/s at any size from 1 GiB. https://docs.aws.amazon.com/emr/latest/ManagementGuide/emr-plan-storage-compare-volume-types.html
- Fargate minimum task 0.25 vCPU / 512 MiB; startup typically 5-15 seconds. https://docs.aws.amazon.com/prescriptive-guidance/latest/semantic-layer-agentic-ai-ontology-reasoning-virtual-knowledge-graph/technology-tradeoffs-alternatives.html
- Starting instance families (db.t4g.medium, t4g.small, m7g) are my own choices, not drawn from a verified AWS sizing table. They are marked as unverified below. https://docs.aws.amazon.com/decision-guides/latest/decision-guides/choosing-aws-compute-service.html

## (e) Anti-patterns to forbid in generated designs

- Vague wording and unspecific effort or capacity claims; use specific, evidenced statements. https://github.com/aws-samples/sample-well-architected-skills-and-steering/blob/main/skills/wa-builder/SKILL.md
- Padding the Well-Architected impact table with obvious or non-neutral-only claims; include only real impacts. https://github.com/aws-samples/sample-well-architected-skills-and-steering/blob/main/skills/wa-builder/SKILL.md
- Over-engineering: choosing between Lambda and Fargate by asking when one fits, adding Kubernetes where ECS suffices, or adding services nobody needs. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/SKILL.md
- Good-practice padding unconnected to requirements: production hardening (WAF, extended logging, Multi-AZ) belongs to production-sized requests only. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/references/security.md
- Multi-AZ leakage: Multi-AZ DB instances or clusters, cross-AZ replicas, cross-AZ NAT traffic, and describing Aurora's three-AZ storage as a feature of a single-AZ design. https://docs.aws.amazon.com/rds/latest/auroraextendedcontent/aurora-faq-availability-and-durability.html
- Recommending App Runner for new workloads. https://github.com/awslabs/agent-plugins/blob/main/plugins/deploy-on-aws/skills/deploy/SKILL.md
- Backup anti-patterns: unencrypted backups, same access for backups and data, production and backups in one security domain, untested restores. https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_backing_up_data_secured_backups_data.html
- ADR anti-pattern: mutating an accepted ADR instead of superseding it, and writing the how instead of the why. https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/adr-process.html
- Inventing services or SKUs: no source states this; it is a requirement of this project and is enforced through the service sheet.

## Not verified or unavailable

- The awslabs deploy skill, defaults.md and security.md were read through a summarising fetch tool, not raw text. Rules above are paraphrased and could miss nuance.
- The wa-builder SKILL.md was also read through a summarising fetch. Its ADR template was confirmed there; the separate architecture-decision-record skill URL returned 404.
- The Prescriptive Guidance appendix and related pages were not read; only adr-process.html.
- The container, database and messaging decision guides were read in part (truncated). The compute and storage decision guides were located but not read in full; storage heuristics come from AWS FAQ and comparison pages.
- Not verified: current AWS statement that App Runner is in maintenance mode (taken from the deploy skill only).
- Not verified: current OpenSearch Serverless minimum OCU cost; the only result found was a preview-era blog, so the skill states no figure.
- The Well-Architected pillar content was sampled from the pillar overview pages and REL09-BP02, not read bestpractice by bestpractice; security, cost and performance rules are therefore largely drawn from the deploy skill.
- Instance-family starting points (t4g, m7g, db.t4g.medium) are not backed by a fetched AWS source. Check availability for the target Region and engine before use.
- Whether Aurora Serverless v2 is acceptable in a single-AZ design is a policy call for the team; its storage is multi-AZ regardless.
