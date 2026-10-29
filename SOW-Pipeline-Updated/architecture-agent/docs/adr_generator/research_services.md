# Service research for the ADR Generator service sheet

Research date: 2026-10-09. Sources: AWS documentation search (docs.aws.amazon.com, aws.amazon.com, repost.aws). Catalog read from `diagram_Generator/backend/app/catalog/components.yaml` (read-only).

## 1. Canonical catalog names (exact `name` values used in the sheet)

| Service in sheet | Catalog `name` | Catalog notes |
|---|---|---|
| Route 53 | Amazon Route 53 | |
| CloudFront | Amazon CloudFront | |
| WAF | AWS WAF | |
| VPC | Amazon VPC | |
| Internet Gateway | Internet Gateway | |
| NAT Gateway | NAT Gateway | |
| VPC endpoints | AWS PrivateLink | synonyms include "vpc endpoint(s)", "interface endpoint", "gateway endpoint" |
| ALB | Application Load Balancer | generic parent is "Elastic Load Balancing"; NLB is "Network Load Balancer" |
| API Gateway | Amazon API Gateway | |
| Lambda | AWS Lambda | |
| EC2 | Amazon EC2 | EBS is separate: "Amazon EBS" |
| ECS | Amazon ECS | |
| Fargate | AWS Fargate | synonym "ecs on fargate" maps here, not to ECS |
| ECR | Amazon ECR | |
| RDS | Amazon RDS | |
| Aurora | Amazon Aurora | synonym "aurora serverless" |
| DynamoDB | Amazon DynamoDB | |
| ElastiCache | Amazon ElastiCache | synonyms "valkey", "redis" |
| OpenSearch | Amazon OpenSearch Service | synonym "opensearch serverless" |
| S3 | Amazon S3 | |
| EFS | Amazon EFS | |
| SQS / SNS | Amazon SQS / Amazon SNS | |
| EventBridge | Amazon EventBridge | also "Amazon EventBridge Scheduler", "Amazon EventBridge Pipes" |
| Step Functions | AWS Step Functions | |
| Kinesis Data Streams | Amazon Kinesis Data Streams | |
| Glue | AWS Glue | |
| Athena | Amazon Athena | |
| Bedrock (and Knowledge Bases) | Amazon Bedrock | synonym "bedrock knowledge base(s)" |
| Textract | Amazon Textract | |
| Cognito | Amazon Cognito | |
| Secrets Manager | AWS Secrets Manager | |
| KMS | AWS KMS | |
| IAM | AWS IAM | |
| CloudWatch | Amazon CloudWatch | |
| CloudTrail | AWS CloudTrail | |

### Catalog gaps and mapping notes
- No dedicated entry for: OpenSearch Serverless (maps to Amazon OpenSearch Service by synonym), Bedrock Knowledge Bases (maps to Amazon Bedrock), Amazon EBS exists but EBS volumes are specified inside EC2 rows, Aurora Serverless v2 (maps to Amazon Aurora), Valkey (maps to Amazon ElastiCache), ECS on Fargate (two icons: Amazon ECS + AWS Fargate), Application Auto Scaling (not present; "Amazon EC2 Auto Scaling" exists for EC2 only), Amazon Route 53 Resolver, ACM exists as "AWS Certificate Manager" (not in sheet for space), CloudWatch Logs (maps to Amazon CloudWatch), Lambda layers.
- Gateway vs interface VPC endpoints are both "AWS PrivateLink" in the catalog.
- All 35 requested services have a catalog entry or a synonym route. No service is fully absent.

## 2. Findings per service

Facts marked (U) are unverified and listed in section 4.

### Amazon Route 53
Public hosted zone with alias records. Global service. Default: 1 hosted zone. CloudFront flat-rate plans bundle Route 53 DNS (https://aws.amazon.com/blogs/networking-and-content-delivery/cloudfront-premium-flat-rate-plan-supports-configurable-usage-allowances/).

### Amazon CloudFront
Flat-rate plans exist: Free $0, Pro $15, Business $200, Premium $1,000 per month, bundling WAF, Route 53, logs; a WAF web ACL must stay attached under any plan (https://docs.aws.amazon.com/waf/latest/developerguide/cloudfront-features.html). Sheet default stays pay-as-you-go style (distribution, PriceClass_100, OAC) so the calculator can price it; PriceClass behaviour is from prior knowledge (U). ACM certificates for CloudFront must be in us-east-1 (prior knowledge, U).

### AWS WAF
Web ACL with AWS managed rule groups; attach to CloudFront or ALB. Rule group names from prior knowledge (U for exact names: AWSManagedRulesCommonRuleSet, KnownBadInputsRuleSet).

### Amazon VPC / Internet Gateway / NAT Gateway / AWS PrivateLink
- NAT Gateway is zonal; billed per NAT-gateway-hour plus per-GB processed. Example rate $0.045 per hour (us-east-2 example in page), https://aws.amazon.com/vpc/pricing/ ; strategies to cut cost with endpoints: https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html
- Gateway endpoints (S3, DynamoDB) have no hourly or data charge; interface endpoints are charged per AZ-hour (about $0.01) plus per-GB: https://aws.amazon.com/blogs/architecture/choosing-your-vpc-endpoint-strategy-for-amazon-s3/ , https://docs.aws.amazon.com/eks/latest/best-practices/cost-opt-networking.html
- Single-AZ gotcha: a DB subnet group requires subnets in at least two AZs even for Single-AZ instances: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html , https://repost.aws/knowledge-center/rds-db-subnet-group

### Application Load Balancer
ALB requires at least two subnets in different AZs; each subnet at least /27 with 8 free IPs: https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html . Targets can live in one AZ. NLB can use a single AZ (https://aws.amazon.com/blogs/networking-and-content-delivery/using-load-balancer-target-group-health-thresholds-to-improve-availability/).

### Amazon API Gateway
HTTP API default (cheaper), REST API step-up for API keys, usage plans, WAF. (Prior knowledge; U for feature split.)

### AWS Lambda
- Memory 128 to 10,240 MB: https://repost.aws/knowledge-center/lambda-memory-compute-cost
- Architectures arm64 (Graviton2) and x86_64: https://docs.aws.amazon.com/lambda/latest/dg/foundation-arch.html
- Runtimes: Node.js 26, 24, 22 supported (22 deprecates Apr 30, 2027; 24 Apr 30, 2028); Node.js 20 and Python 3.9 deprecated: https://docs.aws.amazon.com/lambda/latest/dg/lambda-nodejs.html , https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html
- Python 3.13 is stated as supported from prior knowledge, not seen in the fetched excerpt (U). 1,769 MB equals 1 vCPU (prior knowledge, U).
- Sheet default: arm64, 512 MB, python3.13 or nodejs22.x.

### Amazon EC2
- Release history shows T8i (Sep 15, 2026), M9g/M9gd (Jun 2026), C9g (Jun 2026), R9g (Aug 2026), plus M8i-family variants (M8in, M8idb etc.): https://docs.aws.amazon.com/ec2/latest/instancetypes/doc-history.html
- T8i burstable GA, up to 30% better price-performance than T3: https://aws.amazon.com/blogs/aws/new-low-cost-burstable-amazon-ec2-t8i-instances-are-generally-available/
- Sheet keeps broadly available, long-established families (t4g, t3, m7g, m8g, m7i, c7g, r7g) so the cost calculator is likely to know them. m8g confirmed in RDS/ElastiCache pages; c7g/c7i/r7i/m7i sizes not directly re-verified this session (U). t4g.medium = 2 vCPU, 4 GiB (prior knowledge, U).
- EBS gp3: 3,000 IOPS and 125 MB/s baseline regardless of size; 1 GB to 64 TB: https://repost.aws/knowledge-center/ebs-volume-type-differences
- Amazon Linux 2023 AMI, IMDSv2 (prior knowledge, U).

### Amazon ECS / AWS Fargate / Amazon ECR
- Valid Fargate CPU/memory pairs: 0.25 vCPU (0.5/1/2 GB), 0.5 (1-4 GB), 1 (2-8 GB), 2 (4-16 GB), 4 (8-30 GB), 8 (16-60 GB), 16 (32-120 GB), 32 (60/120/244 GB, per the docs page): https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-tasks-services.html
- Fargate ARM64 (Graviton) and 20 GiB default ephemeral storage: prior knowledge (U).
- ECR scan-on-push and lifecycle policy: prior knowledge (U).

### Amazon RDS
- PostgreSQL majors on RDS: 18 (18.6 released Aug 25, 2026), 17 (17.11), 16, 15, 14, 13: https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-release-calendar.html
- MySQL: 8.4 (8.4.11 RDS Aug 21, 2026) and 8.0 (8.0 reaches end of standard support Jul 31, 2026, now Extended Support): https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/MySQL.Concepts.VersionMgmt.html . Sheet uses MySQL 8.4.
- Instance classes: db.t4g (Graviton2 burstable) supports PostgreSQL 17 and earlier, MySQL 8.4/8.0, MariaDB up to 12.3 in the table excerpt; PostgreSQL 18 on db.t4g not seen in the truncated excerpt (U): https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.DBInstanceClass.Support.html . Sheet default therefore PostgreSQL 17.
- db.m7g, db.m8g (Graviton4), db.r8gd exist: https://aws.amazon.com/blogs/database/leveling-up-amazon-rds-with-aws-graviton4-benchmarks/ , https://aws.amazon.com/rds/instance-types/ . Sheet step-up uses db.m7g / db.r7g (established); db.m8g / db.r8g are valid newer alternatives.
- Single-AZ deployment is a selectable option; the DB subnet group still needs two AZs (links above).
- Storage gp3, min 20 GiB (prior knowledge, U).

### Amazon Aurora
- Aurora Serverless v2 capacity: 0.5 to 256 ACU, half-step increments; 1 ACU is about 2 GiB; minimum 0 ACU with auto-pause on supporting versions; Performance Insights needs min 2 ACU. Renamed "Aurora serverless" in April 2026: https://aws.amazon.com/blogs/database/understanding-how-acu-minimum-and-maximum-range-impacts-scaling-in-amazon-aurora-serverless-v2/ , https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_ServerlessV2ScalingConfigurationInfo.html
- Aurora PostgreSQL 18.x (18.6 Sep 29, 2026), 17.x (17.7 LTS), serverless supported on 17.4+ and 18.3+: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraPostgreSQLReleaseNotes/aurorapostgresql-release-calendar.html , https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.Aurora_Fea_Regions_DB-eng.Feature.ServerlessV2.html
- Aurora MySQL 3.x (MySQL 8.0 compatible) and 8.4.x versions: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraMySQLReleaseNotes/AuroraMySQL.release-calendars.html
- Aurora classes: db.serverless, db.t4g.medium/large, db.r7g, db.r8g, db.r6g, db.x2g: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.DBInstanceClass.SupportAurora.html
- Cluster storage spans AZs by design; a single writer instance lives in one AZ; the DB subnet group still needs 2 AZs. (Prior knowledge for 3-AZ storage statement, U.)

### Amazon DynamoDB
On-demand default, PITR on; regional service (prior knowledge, U). Gateway endpoint free.

### Amazon ElastiCache
- Current-generation Valkey node types include cache.m8g, m7g, m6g, r8g, r7g, burstable cache.t4g.micro/small/medium (0.5, 1.37, 3.09 GiB) and t3: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheNodes.SupportedTypes.html
- Valkey 8.1 and Valkey 9.0 (May 2026) available; CDK enum Valkey 8: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/engine-versions.html , https://aws.amazon.com/about-aws/whats-new/2026/05/valkey-amazon-elasticache/ . Sheet says "Valkey 8.x" as conservative; 9.0 is current.
- Single node, no replica keeps one AZ; replicas in another AZ are the multi-AZ option.

### Amazon OpenSearch Service
- OpenSearch versions supported: 3.5, 3.3, 3.1, 2.19, 2.17...: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/what-is.html
- t3.small.search and t3.medium.search have 10 GiB minimum EBS, 100/200 GiB gp3 max; T3 is suitable for dev/QA, not production: https://docs.aws.amazon.com/opensearch-service/latest/developerguide/limits.html , https://aws.amazon.com/blogs/big-data/best-practices-for-configuring-your-amazon-opensearch-service-domain/
- Newer data-node families: M8g/C8g/R8g/R8gd, OR1, OR2, OM2, OI2: https://aws.amazon.com/blogs/big-data/amazon-opensearch-service-101-t-shirt-size-your-domain-for-e-commerce-search/ , https://docs.aws.amazon.com/opensearch-service/latest/developerguide/multi-tier-storage.html . m7g.large.search is from prior knowledge (U; M6g and M8g confirmed).
- OpenSearch Serverless: OCU = 6 GiB memory; min/max OCU set per collection group, indexing and search separately; min can be 0; standby replicas can be disabled for dev/test, lowering the minimum from 4 to 2 OCU (older article): https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-scaling.html , https://aws.amazon.com/blogs/big-data/amazon-opensearch-h2-2023-in-review/
- Single-AZ: domains can run with 1 AZ and 0 replicas, no HA.

### Amazon S3 / Amazon EFS
- S3: standard class, versioning, Block Public Access (prior knowledge, U).
- EFS: Elastic throughput recommended default; modes Elastic, Provisioned, Bursting; One Zone class exists: https://docs.aws.amazon.com/efs/latest/ug/performance.html , https://aws.amazon.com/blogs/storage/performance-analysis-for-different-amazon-efs-throughput-modes-via-amazon-cloudwatch/ . One Zone is the single-AZ option.

### Amazon SQS / SNS / EventBridge / AWS Step Functions
- Step Functions Standard (up to 1 year, exactly-once, per transition) vs Express (up to 5 min, at-least-once, per execution and duration); type immutable: https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html
- SQS Standard + DLQ, 4-day default retention; SNS Standard; EventBridge default bus: prior knowledge (U).

### Amazon Kinesis Data Streams
On-demand mode: no capacity planning, 4 shards (4 MB/s write) initially, scales automatically; shard = 1 MB/s write, 2 MB/s read; pay per GB: https://docs.aws.amazon.com/streams/latest/dev/how-do-i-size-a-stream.html , https://aws.amazon.com/kinesis/data-streams/faqs/

### AWS Glue / Amazon Athena
Glue 5.1: Spark 3.5.6, Python 3.11 (Glue 5.0: Spark 3.5.4): https://aws.amazon.com/blogs/big-data/introducing-aws-glue-5-1-for-apache-spark/ . Worker types G.1X, G.2X, G.4X...: https://repost.aws/knowledge-center/glue-container-yarn-memory-limit . Glue minimum 2 workers (prior knowledge, U). Athena engine v3 and per-TB-scanned pricing (prior knowledge, U).

### Amazon Bedrock
- Model list includes Claude Sonnet 5.5, Haiku 5.5, Opus 5.5, Sonnet 5, Opus 4.8/4.7/4.6, Sonnet 4.6, Haiku 4.5, Sonnet 4.5; Titan Text Embeddings V2; Cohere Embed v4, Rerank 3.5; Nova family: https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html
- Claude Sonnet 4.6: model ID `anthropic.claude-sonnet-4-6`; geo profile `us.anthropic.claude-sonnet-4-6`; global `global.anthropic.claude-sonnet-4-6`: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html
- Claude Sonnet 5.5 (launched Sep 28, 2026): `anthropic.claude-sonnet-5-5`, `us.anthropic.claude-sonnet-5-5`, `global.anthropic.claude-sonnet-5-5`; 1M context; Knowledge Bases supported on bedrock-runtime: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5-5.html
- Claude Haiku 5.5 (Oct 7, 2026): `us.anthropic.claude-haiku-5-5`: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html
- Claude Haiku 4.5: requires a geo or global inference profile on bedrock-runtime; `us.anthropic.claude-haiku-4-5-20251001-v1:0`: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html
- Titan Text Embeddings V2: `amazon.titan-embed-text-v2:0`, 8K context, no inference profile: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html . Output dimension 1024 default (prior knowledge, U).
- Knowledge Base vector stores: OpenSearch Serverless/managed, Aurora PostgreSQL pgvector, S3 Vectors (plus Pinecone, Redis Enterprise Cloud, MongoDB Atlas on older docs): https://aws.amazon.com/blogs/machine-learning/selecting-a-vector-store-for-amazon-bedrock-knowledge-bases/
- Third-party Claude models are billed via AWS Marketplace (model card Pricing section).
- Decision recorded: the sheet keeps Claude Sonnet 4.6 as the named default (matches the generator's own model and the team's wording), lists Haiku 4.5 as the light model and Sonnet 5.5 as step-up. Reviewer may prefer Sonnet 5.5 / Haiku 5.5 as default now that they exist.

### Amazon Textract
PNG, JPEG, TIFF, PDF; sync and async APIs; DetectDocumentText about $0.0015 per page (first 1M pages, us-west-2): https://aws.amazon.com/textract/faqs/ , https://aws.amazon.com/textract/pricing/

### Amazon Cognito
Feature plans Lite, Essentials, Plus; Essentials is the default for new user pools; Plus adds adaptive auth and compromised-credential detection: https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html , https://aws.amazon.com/cognito/pricing/

### AWS Secrets Manager / AWS KMS / AWS IAM / Amazon CloudWatch / AWS CloudTrail
All from prior knowledge (U): per-secret monthly charge, customer managed KMS key monthly charge plus requests, AWS managed keys no key fee, IAM no charge, CloudWatch Logs retention setting and per-GB ingestion, first CloudTrail management-events trail free.

## 3. Multi-AZ and single-AZ gotchas (verified)
- ALB: at least two subnets in two AZs (verified).
- RDS: Single-AZ DB instance is selectable, but DB subnet group needs subnets in at least two AZs (verified).
- Aurora: failover targets are replicas; no replica means no HA; the same two-AZ subnet group rule applies (verified for subnet group; cluster storage statement U).
- NAT Gateway: zonal; one per AZ for AZ-independent egress; traffic crossing AZs adds data transfer charges (verified).
- VPC interface endpoints: one ENI per chosen subnet/AZ, billed per AZ-hour (verified).
- ElastiCache replica in another AZ, EFS Standard vs One Zone, OpenSearch zone awareness: single node / One Zone keep single-AZ.

## 4. Unverified items (flagged for review)
1. RDS PostgreSQL 18 on db.t4g (default set to 17 for safety; 18 exists on RDS).
2. Lambda python3.13 identifier and the 1,769 MB = 1 vCPU rule (python3.14 not checked).
3. EC2 sizes for c7g, c7i, r7i, m7i, m7g.large and t4g.medium specs; M8i/C8i/R8i availability not confirmed (T8i, M9g, C9g, R9g exist per release history but are too new for calculator coverage).
4. OpenSearch m7g.large.search (M6g and M8g confirmed); Serverless "2 OCU minimum without standby" comes from a 2023 article and the new collection-group model allows min 0.
5. Fargate ARM64 and 20 GiB ephemeral default; ECR lifecycle behaviour.
6. CloudFront PriceClass names, ACM us-east-1 requirement, WAF managed rule group names, API Gateway HTTP vs REST feature split.
7. Titan Embeddings V2 default dimension 1024.
8. Glue minimum workers, Athena engine v3 and pricing, SQS/SNS/EventBridge defaults, Secrets Manager/KMS/CloudTrail/CloudWatch pricing statements.
9. Valkey default written as "8.x" while 9.0 is current; the exact minor chosen by service.
10. The documentation index returned dates up to October 2026, consistent with the task date; model availability (Sonnet 5.5, Haiku 5.5) is new and Region availability for us-east-1 was not individually checked.
11. Aurora Serverless v2 renamed "Aurora serverless" (April 2026); the sheet still says "Serverless v2" because the catalog synonym is "aurora serverless" and the calculator vocabulary is unknown.
