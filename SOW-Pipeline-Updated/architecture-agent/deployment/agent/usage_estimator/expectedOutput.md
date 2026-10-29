# Expected output: cost-calculator usage schema

The JSON the cost calculator requires. The usage estimator must produce this shape: the services grouped by category, with one object per service that holds its configuration and its monthly usage.

```json
{
  "services": {
    "networking_and_cdn": [
      {
        "service": "Amazon Route 53",
        "hosted_zones": 5,
        "queries_per_month": 10000000,
        "routing_policy": "Latency-based",
        "health_checks": 10
      },
      {
        "service": "Amazon CloudFront",
        "data_transfer_out_gb": 5000,
        "requests_per_month": 50000000,
        "origin_type": "S3 + ALB",
        "price_class": "All Edge Locations"
      },
      {
        "service": "AWS WAF",
        "web_acl_count": 3,
        "rules_per_acl": 10,
        "requests_inspected_per_month": 50000000,
        "managed_rule_groups": 2
      },
      {
        "service": "Internet Gateway",
        "data_transfer_in_gb": 1000,
        "data_transfer_out_gb": 2000,
        "attached_vpc_count": 1
      },
      {
        "service": "NAT Gateway",
        "gateway_count": 2,
        "data_processed_gb": 500,
        "availability_zones": 2
      },
      {
        "service": "AWS PrivateLink",
        "endpoint_count": 5,
        "data_processed_gb": 200,
        "endpoint_type": "Interface"
      },
      {
        "service": "Application Load Balancer",
        "load_balancer_count": 2,
        "lcu_per_hour": 10,
        "rules_count": 20,
        "availability_zones": 2
      },
      {
        "service": "Amazon API Gateway",
        "api_type": "REST",
        "api_calls_per_month": 20000000,
        "cache_size_gb": 1.6,
        "data_transfer_out_gb": 100
      }
    ],
    "serverless_compute": [
      {
        "service": "AWS Lambda",
        "invocations_per_month": 5000000,
        "average_duration_seconds": 2,
        "memory_mb": 2048,
        "memory_gb": 2,
        "architecture": "x86_64"
      },
      {
        "service": "AWS Fargate",
        "vcpu_hours_per_month": 1000,
        "memory_gb_per_month": 2000,
        "operating_system": "Linux",
        "architecture": "x86_64"
      }
    ],
    "containers": [
      {
        "service": "Amazon ECS",
        "cluster_count": 2,
        "task_count": 20,
        "launch_type": "Fargate",
        "average_task_duration_hours": 8
      },
      {
        "service": "Amazon ECR",
        "storage_gb": 50,
        "data_transfer_out_gb": 100,
        "repositories": 10,
        "image_scans_per_month": 200
      }
    ],
    "compute": [
      {
        "service": "Amazon EC2",
        "instance_type": "t3.medium",
        "instance_count": 4,
        "operating_system": "Linux",
        "pricing_model": "On-Demand",
        "hours_per_month": 720,
        "storage_gb": 100
      }
    ],
    "database": [
      {
        "service": "Amazon RDS",
        "engine": "PostgreSQL",
        "instance_class": "db.t3.medium",
        "instance_count": 2,
        "storage_gb": 500,
        "multi_az": true,
        "backup_retention_days": 7
      },
      {
        "service": "Amazon Aurora",
        "engine": "Aurora PostgreSQL",
        "instance_class": "db.r6g.large",
        "reader_instances": 2,
        "storage_gb": 200,
        "serverless": false,
        "multi_az": true
      },
      {
        "service": "Amazon DynamoDB",
        "storage_gb": 100,
        "capacity_mode": "Provisioned",
        "read_capacity_units": 50,
        "write_capacity_units": 25,
        "point_in_time_recovery": true
      },
      {
        "service": "Amazon ElastiCache",
        "engine": "Redis",
        "node_type": "cache.t3.medium",
        "node_count": 2,
        "multi_az": true,
        "snapshot_retention_days": 1
      },
      {
        "service": "Amazon OpenSearch Service",
        "instance_type": "t3.medium.search",
        "instance_count": 2,
        "storage_gb": 100,
        "dedicated_master": false,
        "availability_zones": 2
      }
    ],
    "storage": [
      {
        "service": "Amazon S3",
        "storage_class": "Standard",
        "storage_gb": 1000,
        "get_requests_per_month": 5000000,
        "put_requests_per_month": 500000,
        "data_transfer_out_gb": 200
      },
      {
        "service": "Amazon EFS",
        "storage_class": "Standard",
        "storage_gb": 500,
        "throughput_mode": "Bursting",
        "data_transfer_gb": 100,
        "availability_zones": 2
      }
    ],
    "messaging_and_events": [
      {
        "service": "Amazon SQS",
        "queue_type": "Standard",
        "requests_per_month": 10000000,
        "average_message_size_kb": 10,
        "data_transfer_out_gb": 50
      },
      {
        "service": "Amazon SNS",
        "topic_count": 10,
        "publish_requests_per_month": 5000000,
        "notification_deliveries_per_month": 10000000,
        "protocols": ["SQS", "Lambda", "Email"]
      },
      {
        "service": "Amazon SES",
        "emails_per_month": 500000,
        "attachments_gb": 10,
        "dedicated_ip": false,
        "region": "us-east-1"
      },
      {
        "service": "Amazon EventBridge",
        "event_bus_count": 3,
        "events_per_month": 5000000,
        "rules_count": 20,
        "targets_per_rule": 3
      },
      {
        "service": "AWS Step Functions",
        "workflow_type": "Standard",
        "state_transitions_per_month": 1000000,
        "executions_per_month": 50000,
        "average_states_per_execution": 20
      },
      {
        "service": "Amazon Kinesis Data Streams",
        "shard_count": 4,
        "data_ingestion_gb_per_month": 500,
        "retention_hours": 24,
        "consumer_count": 3
      }
    ],
    "analytics_and_etl": [
      {
        "service": "AWS Glue",
        "dpu_hours_per_month": 200,
        "job_type": "Spark",
        "crawlers_per_month": 100,
        "data_catalog_objects": 10000
      },
      {
        "service": "Amazon Athena",
        "queries_per_month": 10000,
        "data_scanned_tb_per_month": 5,
        "data_format": "Parquet",
        "workgroup_count": 2
      }
    ],
    "ai_and_ml": [
      {
        "service": "Amazon Bedrock",
        "model_id": "claude-opus-5-5",
        "input_tokens_per_month": 50000000,
        "output_tokens_per_month": 10000000,
        "invocations_per_month": 100000,
        "inference_type": "On-Demand"
      },
      {
        "service": "Amazon Textract",
        "pages_processed_per_month": 100000,
        "features": ["FORMS", "TABLES", "QUERIES"],
        "async_jobs_per_month": 5000,
        "document_type": "PDF"
      }
    ],
    "security_and_identity": [
      {
        "service": "Amazon Cognito",
        "monthly_active_users": 10000,
        "user_pool_count": 2,
        "advanced_security": true,
        "mfa_enabled": true
      },
      {
        "service": "AWS Secrets Manager",
        "secrets_count": 50,
        "api_calls_per_month": 500000,
        "rotation_enabled": true,
        "rotation_frequency_days": 30
      },
      {
        "service": "AWS KMS",
        "customer_managed_keys": 20,
        "api_calls_per_month": 1000000,
        "key_type": "Symmetric",
        "multi_region": false
      }
    ],
    "observability": [
      {
        "service": "Amazon CloudWatch",
        "metrics_per_month": 100,
        "logs_ingested_gb_per_month": 50,
        "log_storage_gb": 200,
        "alarms_count": 50,
        "dashboards_count": 10
      },
      {
        "service": "AWS CloudTrail",
        "trails_count": 2,
        "events_per_month": 5000000,
        "s3_logging_enabled": true,
        "insight_events": true,
        "multi_region": true
      }
    ]
  }
}
```
