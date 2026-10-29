# Worked example (style and level of detail only)
This excerpt shows the expected precision for a different system. Do not copy its services or values; design from the SRS you are given. It happens to show a two-zone deployment; the zone count you must use is the one in the availability model, not the one here.

SRS excerpt: "Field technicians upload inspection photos and notes from a web app. About 300 users, 40 concurrent at peak. Reports are generated nightly as PDF. Data stays in eu-west-1. Sign-in uses the company's Microsoft Entra ID (SAML)."

Excerpt of the `submit_design` input:
```json
{
  "region": "eu-west-1",
  "availability_zones": ["eu-west-1a", "eu-west-1b"],
  "requirements": [
    {"id": "REQ-03", "text": "Support about 300 users with 40 concurrent at peak.", "kind": "non_functional"}
  ],
  "decisions": [
    {"id": "ADR-002", "title": "Use Amazon RDS for PostgreSQL for inspection records",
     "status": "Proposed",
     "context": "Inspections, sites and technicians are relational and reports join them (REQ-02). Load is modest: 40 concurrent users (REQ-03).",
     "decision": "Use one Amazon RDS for PostgreSQL 17 instance, db.t4g.medium, gp3 50 GiB, Multi-AZ with a standby in eu-west-1b, 7-day automated backups, encrypted with the customer managed KMS key.",
     "consequences": ["Familiar SQL for nightly reporting.", "The standby makes an eu-west-1a failure a failover of about a minute instead of a restore from backup.", "Multi-AZ roughly doubles the instance hours and adds cross-AZ replication traffic.", "Move to db.m7g.large when CPU stays above 70%."],
     "rejected_alternatives": [{"option": "Amazon DynamoDB", "reason": "Nightly reports need joins across three entities; it would win only if access became purely key-based."}],
     "requirement_ids": ["REQ-02", "REQ-03"], "component_ids": ["C-08"]}
  ],
  "components": [
    {"id": "C-05", "name": "Inspection API", "service": "AWS Fargate", "category": "containers",
     "role": "Serves the web app API", "quantity": 2, "boundary": "private_subnet_a",
     "configuration": {"platform": "Linux/ARM64", "vcpu": "0.5", "memory_gb": "1", "desired_tasks": "2",
                       "placement": "one task per AZ across eu-west-1a and eu-west-1b"},
     "sku_summary": "Fargate Linux/ARM64, 0.5 vCPU / 1 GB, 2 tasks across 2 AZs",
     "purpose": "Runs the stateless API behind the load balancer.", "assumed": true,
     "assumption_reason": "SRS gives 40 concurrent users but no throughput; one small task per zone covers it and survives losing a zone.",
     "requirement_ids": ["REQ-03"], "decision_ids": ["ADR-001"]},
    {"id": "C-08", "name": "Inspection Database", "service": "Amazon RDS", "category": "database",
     "role": "Stores inspection records", "quantity": 1, "boundary": "private_subnet_a",
     "configuration": {"engine": "postgres", "engine_version": "17", "instance_type": "db.t4g.medium",
                       "deployment": "Multi-AZ (standby in eu-west-1b)", "storage_type": "gp3", "storage_gb": "50",
                       "backup_retention_days": "7", "db_subnet_group": "2 subnets, eu-west-1a and eu-west-1b"},
     "sku_summary": "db.t4g.medium, PostgreSQL 17, gp3 50 GiB, Multi-AZ",
     "purpose": "System of record for inspections.", "assumed": false,
     "requirement_ids": ["REQ-02"], "decision_ids": ["ADR-002"]}
  ],
  "relationships": [
    {"id": "F-03", "source": "C-05", "target": "C-08", "label": "reads and writes inspections",
     "sentence": "The Inspection API reads and writes inspection records in the Inspection Database.",
     "protocol": "PostgreSQL", "port": "5432"}
  ]
}
```
