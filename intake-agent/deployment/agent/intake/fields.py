"""The intake record: 40 fields, grouped as the Discovery and SOW agents will read them.

Each extracted field is stored as {value, source, verified}: `source` is the exact
passage from the email or CRM record that supports the value, and `verified` says
whether that passage was found word for word in the input (checked in code, not by
the model). Fields with `key=True` must be chased if missing.
"""

FIELD_GROUPS: list[tuple[str, list[tuple[str, str, bool]]]] = [
    # (group, [(field_id, label, key)])
    ("Customer", [
        ("customer_name", "Customer name", True),
        ("industry", "Industry", False),
        ("headquarters_country", "Headquarters country", False),
        ("company_size", "Company size (employees / revenue)", False),
        ("existing_aws_customer", "Existing AWS customer / account", False),
    ]),
    ("Contacts", [
        ("contact_name", "Primary contact", True),
        ("contact_role", "Contact role", False),
        ("contact_email", "Contact email", False),
        ("contact_phone", "Contact phone", False),
        ("decision_maker", "Decision maker / economic buyer", True),
    ]),
    ("Opportunity", [
        ("opportunity_title", "Opportunity title", True),
        ("opportunity_type", "Type (migration, modernisation, data, GenAI, security, managed services...)", True),
        ("business_problem", "Business problem / driver", True),
        ("desired_outcomes", "Desired outcomes", False),
        ("success_criteria", "Success criteria / KPIs", True),
        ("scope_summary", "Scope summary", True),
        ("out_of_scope", "Out of scope", False),
        ("urgency_reason", "Urgency / compelling event", False),
    ]),
    ("Current environment", [
        ("current_environment", "Current hosting (on-prem, other cloud, colo)", True),
        ("workload_inventory", "Workloads / applications", True),
        ("server_count", "Number of servers / VMs", True),
        ("databases", "Databases and size", False),
        ("data_volume", "Data volume / storage", False),
        ("operating_systems", "Operating systems", False),
        ("licensing", "Licensing (Microsoft, SAP, Oracle...)", False),
        ("current_cloud_spend", "Current hosting / cloud spend", False),
    ]),
    ("Target and constraints", [
        ("aws_services", "AWS services mentioned", False),
        ("target_region", "Target AWS region / data residency", True),
        ("compliance", "Compliance / regulatory requirements", True),
        ("security_requirements", "Security requirements", False),
        ("availability_dr", "Availability / DR (RTO, RPO)", False),
        ("connectivity", "Network connectivity (VPN, Direct Connect)", False),
    ]),
    ("Commercial", [
        ("budget", "Budget", True),
        ("funding_expectation", "Funding expectation (AWS / partner funding)", False),
        ("expected_aws_arr", "Expected AWS spend (ARR) after the project", True),
        ("timeline", "Timeline / target dates", True),
        ("decision_date", "Decision / signature date", False),
        ("competition", "Competitors / alternatives", False),
    ]),
    ("Source and partner", [
        ("lead_source", "Lead source / partner (e.g. Ingram Micro)", False),
        ("crm_reference", "CRM / opportunity ID", False),
    ]),
]

FIELDS = [field_id for _, fields in FIELD_GROUPS for field_id, _, _ in fields]
LABELS = {field_id: label for _, fields in FIELD_GROUPS for field_id, label, _ in fields}
KEY_FIELDS = [field_id for _, fields in FIELD_GROUPS for field_id, _, key in fields if key]
GROUP_OF = {field_id: group for group, fields in FIELD_GROUPS for field_id, _, _ in fields}

assert len(FIELDS) == 40 and len(set(FIELDS)) == 40


def field_catalogue_text() -> str:
    """Field list for the prompt: one line per field, key fields marked."""
    lines = []
    for group, fields in FIELD_GROUPS:
        lines.append(f"{group}:")
        lines += [f"  - {fid}: {label}{' [key]' if key else ''}" for fid, label, key in fields]
    return "\n".join(lines)
