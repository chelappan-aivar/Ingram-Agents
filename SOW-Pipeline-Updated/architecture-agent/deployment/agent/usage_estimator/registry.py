"""The cost-calculator schema (usage_estimator/expectedOutput.md) as code: one entry per service, in schema order.

Each service has its schema group, its exact `service` name and its fields in schema order. A field is
(name, type, hint): type is "integer", "number", "boolean", "string" or "list"; the hint goes into the tool schema so
the model knows what the figure means. tests/usage_estimator/test_registry.py keeps this file and expectedOutput.md
identical (groups, services, field names and order).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HOURS_PER_MONTH = 730          # AWS standard month for always-on resources

# Availability fields set by code from the design's availability model, whatever the model answers.
# The model never sees them (estimator.py skips them when building the tool schema) because they
# are a property of the design, not an estimate. See adr_generator/availability.py.
AZ_FIELDS = frozenset({"multi_az", "availability_zones"})
# Fields computed in code from other fields of the same entry.
DERIVED = {"memory_gb"}

GROUPS = ["networking_and_cdn", "serverless_compute", "containers", "compute", "database", "storage",
          "messaging_and_events", "analytics_and_etl", "ai_and_ml", "security_and_identity", "observability"]


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    hint: str


@dataclass(frozen=True)
class Service:
    group: str
    name: str
    fields: tuple[Field, ...]

    @property
    def key(self) -> str:
        """Tool-schema property name, e.g. 'amazon_route_53'."""
        return re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")


def _svc(group: str, name: str, *fields: tuple[str, str, str]) -> Service:
    return Service(group, name, tuple(Field(*f) for f in fields))


I, N, B, S, L = "integer", "number", "boolean", "string", "list"

SERVICES: list[Service] = [
    # --- networking_and_cdn
    _svc("networking_and_cdn", "Amazon Route 53",
         ("hosted_zones", I, "Hosted zones in the design."),
         ("queries_per_month", I, "DNS queries per month."),
         ("routing_policy", S, "Simple for a single-Region design, unless the design states another policy."),
         ("health_checks", I, "Route 53 health checks; 0 unless the design uses them.")),
    _svc("networking_and_cdn", "Amazon CloudFront",
         ("data_transfer_out_gb", N, "GB served to viewers per month."),
         ("requests_per_month", I, "HTTP/HTTPS requests per month."),
         ("origin_type", S, "Origins behind the distribution, e.g. 'S3', 'ALB', 'S3 + ALB', 'API Gateway'."),
         ("price_class", S, "Price class from the design, e.g. 'North America and Europe' (PriceClass_100) or "
                            "'All Edge Locations'.")),
    _svc("networking_and_cdn", "AWS WAF",
         ("web_acl_count", I, "Web ACLs."),
         ("rules_per_acl", I, "Rules per web ACL, including rate-based rules."),
         ("requests_inspected_per_month", I, "Requests reaching every resource the web ACL protects, per month."),
         ("managed_rule_groups", I, "AWS managed rule groups per web ACL.")),
    _svc("networking_and_cdn", "Internet Gateway",
         ("data_transfer_in_gb", N, "GB from the internet into the VPC per month."),
         ("data_transfer_out_gb", N, "GB from the VPC to the internet per month (not via CloudFront)."),
         ("attached_vpc_count", I, "VPCs attached.")),
    _svc("networking_and_cdn", "NAT Gateway",
         ("gateway_count", I, "NAT Gateways."),
         ("data_processed_gb", N, "GB through the NAT Gateway per month (outbound calls and their responses)."),
         ("availability_zones", I, "Set by code from the design's availability model.")),
    _svc("networking_and_cdn", "AWS PrivateLink",
         ("endpoint_count", I, "Interface endpoints (gateway endpoints for S3 and DynamoDB are free; do not count "
                               "them)."),
         ("data_processed_gb", N, "GB through the interface endpoints per month."),
         ("endpoint_type", S, "'Interface', or 'Gateway' when the design has only gateway endpoints.")),
    _svc("networking_and_cdn", "Application Load Balancer",
         ("load_balancer_count", I, "Load balancers."),
         ("lcu_per_hour", N, "Average Load Balancer Capacity Units per hour."),
         ("rules_count", I, "Listener rules."),
         ("availability_zones", I, "Set by code from the design's availability model.")),
    _svc("networking_and_cdn", "Amazon API Gateway",
         ("api_type", S, "'HTTP', 'REST' or 'WebSocket', from the design."),
         ("api_calls_per_month", I, "API calls per month."),
         ("cache_size_gb", N, "API cache size in GB; 0 when no cache is configured."),
         ("data_transfer_out_gb", N, "GB returned to callers per month.")),
    # --- serverless_compute
    _svc("serverless_compute", "AWS Lambda",
         ("invocations_per_month", I, "Invocations per month, all functions in this component together."),
         ("average_duration_seconds", N, "Average billed duration per invocation in seconds."),
         ("memory_mb", I, "Memory from the design, in MB."),
         ("memory_gb", N, "memory_mb / 1024 (computed in code)."),
         ("architecture", S, "'arm64' or 'x86_64', from the design.")),
    _svc("serverless_compute", "AWS Fargate",
         ("vcpu_hours_per_month", N, "vCPU per task x tasks x running hours per month (730 when always on)."),
         ("memory_gb_per_month", N, "Memory GB per task x tasks x running hours per month (GB-hours)."),
         ("operating_system", S, "'Linux' or 'Windows'."),
         ("architecture", S, "'arm64' or 'x86_64', from the design.")),
    # --- containers
    _svc("containers", "Amazon ECS",
         ("cluster_count", I, "ECS clusters."),
         ("task_count", I, "Tasks running on average."),
         ("launch_type", S, "'EC2' or 'Fargate'."),
         ("average_task_duration_hours", N, "Average running hours per task per month (730 when always on).")),
    _svc("containers", "Amazon ECR",
         ("storage_gb", N, "GB of images stored, after the lifecycle policy."),
         ("data_transfer_out_gb", N, "GB pulled to outside the Region per month; 0 for same-Region pulls."),
         ("repositories", I, "Repositories."),
         ("image_scans_per_month", I, "Image scans per month (scan on push = pushes per month).")),
    # --- compute
    _svc("compute", "Amazon EC2",
         ("instance_type", S, "Instance type from the design."),
         ("instance_count", I, "Instances."),
         ("operating_system", S, "'Linux' or 'Windows'."),
         ("pricing_model", S, "'On-Demand' unless the design states a commitment."),
         ("hours_per_month", N, "Running hours per instance per month (730 when always on)."),
         ("storage_gb", N, "EBS storage per instance in GB, from the design.")),
    # --- database
    _svc("database", "Amazon RDS",
         ("engine", S, "Engine from the design, e.g. 'PostgreSQL', 'MySQL'."),
         ("instance_class", S, "Instance class from the design."),
         ("instance_count", I, "DB instances."),
         ("storage_gb", N, "Allocated storage in GB from the design, raised only if the stored data outgrows it."),
         ("multi_az", B, "Set by code from the design's availability model."),
         ("backup_retention_days", I, "Automated backup retention in days, from the design.")),
    _svc("database", "Amazon Aurora",
         ("engine", S, "'Aurora PostgreSQL' or 'Aurora MySQL'."),
         ("instance_class", S, "Writer instance class, or 'db.serverless' for Serverless v2."),
         ("reader_instances", I, "Reader instances; 0 unless the design has readers."),
         ("storage_gb", N, "Average GB stored in the cluster volume."),
         ("serverless", B, "true for Serverless v2."),
         ("multi_az", B, "Set by code from the design's availability model.")),
    _svc("database", "Amazon DynamoDB",
         ("storage_gb", N, "Average GB stored."),
         ("capacity_mode", S, "'On-Demand' or 'Provisioned', from the design."),
         ("read_capacity_units", I, "Provisioned RCU, or for On-Demand the average read units per second."),
         ("write_capacity_units", I, "Provisioned WCU, or for On-Demand the average write units per second."),
         ("point_in_time_recovery", B, "From the design.")),
    _svc("database", "Amazon ElastiCache",
         ("engine", S, "'Valkey', 'Redis' or 'Memcached', from the design."),
         ("node_type", S, "Node type from the design."),
         ("node_count", I, "Nodes."),
         ("multi_az", B, "Set by code from the design's availability model."),
         ("snapshot_retention_days", I, "Snapshot retention in days; 0 when snapshots are off.")),
    _svc("database", "Amazon OpenSearch Service",
         ("instance_type", S, "Data node instance type from the design."),
         ("instance_count", I, "Data nodes."),
         ("storage_gb", N, "EBS storage per node in GB, from the design."),
         ("dedicated_master", B, "true only if the design has dedicated master nodes."),
         ("availability_zones", I, "Set by code from the design's availability model.")),
    # --- storage
    _svc("storage", "Amazon S3",
         ("storage_class", S, "Main storage class, e.g. 'Standard', 'Intelligent-Tiering'."),
         ("storage_gb", N, "Average GB stored in storage_class only, after growth and lifecycle rules. Data moved "
                           "to another class (e.g. Glacier) is not counted here; name its GB in a warning."),
         ("get_requests_per_month", I, "GET and SELECT requests per month."),
         ("put_requests_per_month", I, "PUT, COPY, POST and LIST requests per month."),
         ("data_transfer_out_gb", N, "GB to the internet per month (not to CloudFront or within the Region).")),
    _svc("storage", "Amazon EFS",
         ("storage_class", S, "'One Zone' or 'Standard', from the design."),
         ("storage_gb", N, "Average GB stored."),
         ("throughput_mode", S, "'Elastic', 'Bursting' or 'Provisioned', from the design."),
         ("data_transfer_gb", N, "GB read and written per month."),
         ("availability_zones", I, "Set by code from the design's availability model.")),
    # --- messaging_and_events
    _svc("messaging_and_events", "Amazon SQS",
         ("queue_type", S, "'Standard' or 'FIFO', from the design."),
         ("requests_per_month", I, "API requests per month: send + receive + delete, including empty receives; "
                                   "each 64 KB chunk counts as one request."),
         ("average_message_size_kb", N, "Average message size in KB."),
         ("data_transfer_out_gb", N, "GB transferred out of the Region per month; 0 when consumers are in the "
                                     "Region.")),
    _svc("messaging_and_events", "Amazon SNS",
         ("topic_count", I, "Topics."),
         ("publish_requests_per_month", I, "Publish requests per month."),
         ("notification_deliveries_per_month", I, "Deliveries per month (publishes x subscriptions)."),
         ("protocols", L, "Subscription protocols, e.g. ['SQS', 'Lambda', 'Email'].")),
    _svc("messaging_and_events", "Amazon SES",
         ("emails_per_month", I, "Emails sent per month."),
         ("attachments_gb", N, "GB of attachments sent per month."),
         ("dedicated_ip", B, "From the design."),
         ("region", S, "The design's Region.")),
    _svc("messaging_and_events", "Amazon EventBridge",
         ("event_bus_count", I, "Event buses (the default bus counts as 1)."),
         ("events_per_month", I, "Events published to the bus or invoked by Scheduler per month."),
         ("rules_count", I, "Rules and schedules."),
         ("targets_per_rule", I, "Average targets per rule.")),
    _svc("messaging_and_events", "AWS Step Functions",
         ("workflow_type", S, "'Standard' or 'Express', from the design."),
         ("state_transitions_per_month", I, "executions_per_month x average_states_per_execution."),
         ("executions_per_month", I, "Workflow executions per month."),
         ("average_states_per_execution", I, "Average state transitions per execution.")),
    _svc("messaging_and_events", "Amazon Kinesis Data Streams",
         ("shard_count", I, "Provisioned shards; for on-demand mode, the shard-equivalent of the peak write rate."),
         ("data_ingestion_gb_per_month", N, "GB written per month."),
         ("retention_hours", I, "Retention in hours, from the design."),
         ("consumer_count", I, "Consumers reading the stream.")),
    # --- analytics_and_etl
    _svc("analytics_and_etl", "AWS Glue",
         ("dpu_hours_per_month", N, "Workers x DPU per worker x job hours per run x runs per month."),
         ("job_type", S, "'Spark', 'Python shell' or 'Ray', from the design."),
         ("crawlers_per_month", I, "Crawler runs per month."),
         ("data_catalog_objects", I, "Data Catalog objects (databases, tables, partitions).")),
    _svc("analytics_and_etl", "Amazon Athena",
         ("queries_per_month", I, "Queries per month."),
         ("data_scanned_tb_per_month", N, "TB scanned per month, after partitioning and columnar compression."),
         ("data_format", S, "e.g. 'Parquet', 'JSON', 'CSV'."),
         ("workgroup_count", I, "Workgroups.")),
    # --- ai_and_ml
    _svc("ai_and_ml", "Amazon Bedrock",
         ("model_id", S, "The Bedrock model or inference profile id from the design, e.g. "
                         "'us.anthropic.claude-sonnet-4-6'. One entry per model."),
         ("input_tokens_per_month", I, "Input tokens per month for this model (prompt + context + retrieved text)."),
         ("output_tokens_per_month", I, "Output tokens per month for this model (0 for embedding models)."),
         ("invocations_per_month", I, "Model calls per month for this model."),
         ("inference_type", S, "'On-Demand', 'Batch' or 'Provisioned Throughput', from the design.")),
    _svc("ai_and_ml", "Amazon Textract",
         ("pages_processed_per_month", I, "Pages processed per month."),
         ("features", L, "AnalyzeDocument features used, from 'FORMS', 'TABLES', 'QUERIES', 'SIGNATURES', "
                         "'LAYOUT'; empty for DetectDocumentText only."),
         ("async_jobs_per_month", I, "Asynchronous jobs per month (documents sent with Start* APIs)."),
         ("document_type", S, "e.g. 'PDF', 'Image'.")),
    # --- security_and_identity
    _svc("security_and_identity", "Amazon Cognito",
         ("monthly_active_users", I, "Monthly active users."),
         ("user_pool_count", I, "User pools."),
         ("advanced_security", B, "true for the Plus tier or advanced security features, from the design."),
         ("mfa_enabled", B, "From the design.")),
    _svc("security_and_identity", "AWS Secrets Manager",
         ("secrets_count", I, "Secrets."),
         ("api_calls_per_month", I, "GetSecretValue and other API calls per month, after client-side caching."),
         ("rotation_enabled", B, "From the design."),
         ("rotation_frequency_days", I, "Rotation interval in days; 0 when rotation is off.")),
    _svc("security_and_identity", "AWS KMS",
         ("customer_managed_keys", I, "Customer managed keys."),
         ("api_calls_per_month", I, "Encrypt, Decrypt and GenerateDataKey calls per month, after S3 Bucket Keys "
                                    "and data key caching."),
         ("key_type", S, "'Symmetric' or 'Asymmetric'."),
         ("multi_region", B, "true only for multi-Region keys in the design.")),
    # --- observability
    _svc("observability", "Amazon CloudWatch",
         ("metrics_per_month", I, "Custom metrics (AWS service metrics are free)."),
         ("logs_ingested_gb_per_month", N, "GB of logs ingested per month."),
         ("log_storage_gb", N, "GB of logs stored at steady state, given the retention."),
         ("alarms_count", I, "Alarms."),
         ("dashboards_count", I, "Dashboards.")),
    _svc("observability", "AWS CloudTrail",
         ("trails_count", I, "Trails."),
         ("events_per_month", I, "Management events per month (the first copy is free; the cost calculator "
                                 "decides)."),
         ("s3_logging_enabled", B, "true when the trail delivers to S3."),
         ("insight_events", B, "true only if the design enables CloudTrail Insights."),
         ("multi_region", B, "true for a multi-Region trail, from the design.")),
]

BY_NAME = {s.name: s for s in SERVICES}
BY_KEY = {s.key: s for s in SERVICES}
