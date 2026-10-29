"""Fake agent replies, shaped like the real ones, so the whole pipeline runs without AWS."""
from __future__ import annotations

from typing import Any

SOW_MARKDOWN = """# About Ingram Micro
Ingram Micro Holding Corporation is a leading global distributor.
# Statement of Work
This SOW is entered into by and between Ingram Micro and Fabrikam Insurance.
# Scope of Work
## In Scope
- Amazon Bedrock (Claude) agents for claims triage
- Amazon S3 document store with encryption at rest via AWS KMS and TLS in transit
- Least-privilege IAM roles federated through Microsoft Entra ID
- Amazon CloudWatch logs, metrics and alarms to the on-call rota
- Automated backups, 7-day retention, point-in-time recovery; RPO 15 minutes, RTO 2 hours
- Availability target 99.9% monthly across two Availability Zones
- 40 concurrent users, p95 latency target 800 ms
- Right-sizing review, cost allocation tagging and a monthly budget alarm
- Graviton (arm64) and S3 Intelligent-Tiering lifecycle policies
- A runbook and operational handover with training
## Out of Scope
- Amazon Kinesis streaming ingestion
- Data migration from the legacy platform
# Pricing
One-time delivery (fixed price): USD 50,000.
Refer to the cost estimate below for the AWS run-rate:
AWS Pricing Calculator estimate: [cost calculator link]
# Schedule I: Estimated Project Schedule
The engagement runs for 10 weeks.
"""

DESIGN: dict[str, Any] = {
    "title": "Claims FNOL Assistant",
    "region": "us-east-2",
    "availability_zones": ["us-east-2a", "us-east-2b"],
    "requirements": [
        {"id": "REQ-01", "text": "Triage 18,000 FNOLs per month.", "kind": "functional"},
        {"id": "REQ-02", "text": "p95 latency under 800 ms.", "kind": "non_functional"},
        {"id": "REQ-03", "text": "Data stays in us-east-2.", "kind": "constraint"},
    ],
    "boundaries": [
        {"id": "main_vpc", "type": "vpc", "name": "Main VPC"},
        {"id": "az_a", "type": "az", "name": "us-east-2a", "parent": "main_vpc"},
        {"id": "az_b", "type": "az", "name": "us-east-2b", "parent": "main_vpc"},
    ],
    "components": [
        {"id": "C-01", "name": "Claims Adjuster", "service": "Actor", "category": "actor",
         "role": "Reviews triaged claims"},
        {"id": "C-02", "name": "Triage Agent", "service": "Amazon Bedrock", "category": "ai_ml",
         "role": "Classifies claims", "purpose": "Runs the FNOL triage prompt",
         "sku_summary": "Claude Sonnet, on-demand", "quantity": 1},
        {"id": "C-03", "name": "Document Store", "service": "Amazon S3", "category": "storage",
         "role": "Stores attachments", "purpose": "FNOL attachments, 1-year retention",
         "sku_summary": "S3 Standard", "quantity": 1},
        {"id": "C-04", "name": "Claims Database", "service": "Amazon RDS", "category": "database",
         "role": "System of record", "purpose": "Claim records",
         "sku_summary": "db.t4g.medium, PostgreSQL 17, Multi-AZ", "quantity": 1},
    ],
    "assumptions": [{"id": "A-01", "text": "18,000 FNOLs per month on average.", "reason": "SRS"}],
}

USAGE_JSON: dict[str, Any] = {
    "services": {
        "ai_and_ml": [{"service": "Amazon Bedrock", "model_id": "claude-sonnet",
                       "invocations_per_month": 18000, "input_tokens_per_month": 450000000,
                       "output_tokens_per_month": 45000000}],
        "storage": [{"service": "Amazon S3", "storage_class": "Standard", "storage_gb": 250.5,
                     "get_requests_per_month": 90000, "put_requests_per_month": 30000}],
        "database": [{"service": "Amazon RDS", "engine": "PostgreSQL",
                      "instance_class": "db.t4g.medium", "instance_count": 1,
                      "storage_gb": 100, "multi_az": True, "availability_zones": 2}],
    }
}

ARCHITECTURE_RESULT: dict[str, Any] = {
    "status": "completed",
    "run_id": "20261027-120000-abc123",
    "adr": {"adr_md": "# Architecture Decision Record", "spec_json": {"schema_version": "1.0"},
            "design": DESIGN},
    "usage": {"status": "completed", "usage_json": USAGE_JSON,
              "usage_assumptions_md": "# Usage estimate"},
    "diagram": {"png_base64": "aVZCT1J3MEtHZ29BQUFBTlNVaEVVZw==", "svg_base64": None,
                "drawio_xml": "<mxfile/>", "report": {"status": "pass"}},
    "scope": {"in_scope": ["Amazon Bedrock (Claude) agents for claims triage"],
              "out_of_scope": ["Amazon Kinesis streaming ingestion"], "violations": []},
    "scope_warnings": [],
    "report_status": "pass",
    "job_id": "job-1",
}

COST_ESTIMATE: dict[str, Any] = {
    "estimate_name": "fabrikam-fnol",
    "estimate_id": "abc123",
    "calculator_url": "https://calculator.aws/#/estimate?id=abc123",
    "region": "us-east-2",
    "currency": "USD",
    "monthly_total": 8500,
    "environments": {
        "production": {"services": [
            {"service": "Amazon Bedrock", "monthly_cost": 4200},
            {"service": "Amazon S3", "monthly_cost": 120},
            {"service": "Amazon RDS", "monthly_cost": 4180},
        ]}
    },
    "assumptions": ["On-demand pricing in us-east-2, October 2026"],
}

COST_RESULT: dict[str, Any] = {
    "status": "completed",
    "estimate": COST_ESTIMATE,
    "estimate_id": "abc123",
    "calculator_url": "https://calculator.aws/#/estimate?id=abc123",
    "estimate_name": "fabrikam-fnol",
    "services_count": 3,
    "checklist": ["Amazon Bedrock", "Amazon S3", "Amazon RDS"],
    "coverage": {"missing": []},
}

UPSTREAM: dict[str, Any] = {
    "meta": {"schema_version": "1.0", "opportunity_id": "OPP-2026-0517", "template_id": "aiml/ai-ml"},
    "intake": {"customer_name": "Fabrikam Insurance", "problem_statement": "Manual FNOL triage is slow.",
               "timeline": "10 weeks"},
}


class FakeAgents:
    """Records every call and answers from a per-(agent, action) script."""

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str]] = []
        self._overrides = overrides
        self.jobs: dict[str, dict[str, Any]] = {}

    def __call__(self, agent: str, payload: dict[str, Any], *, session: str) -> dict[str, Any]:
        action = str(payload.get("action") or "run_pipeline")
        self.calls.append((agent, action, payload, session))
        key = f"{agent}:{action}"
        if key in self._overrides:
            value = self._overrides[key]
            return value(payload) if callable(value) else value
        return self._default(agent, action, payload)

    def _default(self, agent: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if agent == "sow" and action == "generate":
            self.jobs[payload["job_id"]] = {"status": "completed", "stage": "done", "progress": 100,
                                            "markdown": SOW_MARKDOWN}
            return {"status": "accepted"}
        if agent == "sow" and action == "wa_review":
            return {"status": "completed",
                    "review": {"findings": [], "can_proceed": True,
                               "summary": {"total": 0, "high": 0, "medium": 0, "low": 0}}}
        if agent == "sow" and action == "check":
            self.jobs[payload["job_id"]] = {
                "status": "completed", "stage": "done", "progress": 100,
                "check": {"rules": [{"id": "pricing-refers-to-cost-estimate", "name": "Pricing refers to the "
                                     "cost estimate", "severity": "required", "status": "pass"}],
                          "issues": [], "summary": {"total": 1, "passed": 1, "failed": 0, "advisory": 0},
                          "can_export": True},
            }
            return {"status": "accepted"}
        if agent == "architecture":
            return ARCHITECTURE_RESULT
        if agent == "cost" and action == "estimate":
            return COST_RESULT
        raise AssertionError(f"unscripted call: {agent}:{action}")

    def read_job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.get(job_id, {"status": "running", "stage": "generating", "progress": 50})

    def actions(self) -> list[str]:
        return [f"{agent}:{action}" for agent, action, _, _ in self.calls]

    def payload_for(self, key: str) -> dict[str, Any]:
        agent, action = key.split(":", 1)
        for a, act, payload, _ in self.calls:
            if a == agent and act == action:
                return payload
        raise AssertionError(f"{key} was never called")
