"""Sample opportunities for the demo store (POST /funding/demo/seed), one per branch of the decision framework.

Fixed IDs, so seeding again resets them instead of adding duplicates. Some answer the AWS funding questions on the
form; others only describe the facts in free text, so the agent (Claude) has to find them.
"""

import uuid

NS = uuid.UUID("6f1c2b0e-6a43-4d4b-9a57-1b2f6c0d7e11")

SAMPLES = [
    ("tailwind", "Tailwind Robotics VMware exit to AWS", "Tailwind Robotics", 180_000, "2026-12-15", {
        "request_type": "Cloud Services Opportunity", "company_name": "Aivar Innovations", "ec_name": "Tailwind Robotics",
        "ec_country": "India", "cs_vendor": "AWS", "aws_opportunity_type": "Migration",
        "cs_opportunity_details": "Seed-funded robotics startup running 35 VMs on VMware in a Bengaluru colo. Wants to "
                                  "rehost on EC2 in ap-south-1 before the colo contract ends in March.",
        "fund_segment": "Startup", "fund_workload": "Migration", "fund_migration_source": "On-premises",
        "fund_vms": "Yes", "fund_mrr_usd": 15000}),
    ("northwind", "Clinical notes summarisation on Amazon Bedrock", "Northwind Health", 300_000, "2027-01-31", {
        "request_type": "Data & AI Opportunity", "company_name": "Contoso Partners", "ec_name": "Northwind Health",
        "ec_country": "United Kingdom", "dai_vendor": "AWS",
        "dai_details": "Northwind Health is a 12,000-employee hospital group. They want a proof of concept for "
                       "summarising clinical notes with Amazon Bedrock. Expected AWS spend is about 25,000 USD per "
                       "month once live."}),
    ("woodgrove", "Woodgrove Bank data centre migration", "Woodgrove Bank", 720_000, "2027-03-31", {
        "request_type": "Cloud Services Opportunity", "company_name": "Fabrikam Reseller", "ec_name": "Woodgrove Bank",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration",
        "cs_opportunity_details": "Woodgrove Bank, a listed retail bank, is exiting two on-premises data centres: 400 "
                                  "VMs and 60 SQL Server instances. Expected AWS run rate is 60,000 USD per month.",
        "estimated_arr": "720,000"}),
    ("litware", "Litware SaaS platform move from Azure", "Litware Inc", 240_000, "2027-02-28", {
        "request_type": "Cloud Services Opportunity", "company_name": "Aivar Innovations", "ec_name": "Litware Inc",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration",
        "cs_opportunity_details": "Litware is a mid-sized logistics software company (800 employees) moving its "
                                  "customer portal from Microsoft Azure to AWS. About 20,000 USD AWS spend per month.",
        "fund_segment": "Non-startup (enterprise and others)", "fund_migration_source": "Another cloud"}),
    ("proseware", "Proseware AI support assistant", "Proseware", 96_000, "2026-12-31", {
        "request_type": "Data & AI Opportunity", "company_name": "Contoso Partners", "ec_name": "Proseware",
        "dai_vendor": "AWS",
        "dai_details": "Proseware is a Series A startup building a brand-new customer support assistant on Amazon "
                       "Bedrock. Expected AWS spend of 8,000 USD per month.",
        "fund_segment": "Startup", "fund_workload": "Generative AI", "fund_genai_type": "Fresh workload"}),
    ("contoso", "Contoso Retail SAP ECC to AWS Mumbai", "Contoso Retail", 600_000, "2027-06-30", {
        "request_type": "Cloud Services Opportunity", "company_name": "Fabrikam Reseller", "ec_name": "Contoso Retail",
        "cs_vendor": "AWS", "aws_opportunity_type": "SAP migration",
        "cs_opportunity_details": "Move SAP ECC (45 servers, 14 TB) from on-premises to AWS ap-south-1 by Q2 2027.",
        "estimated_arr": "600000"}),
    ("adatum", "A. Datum ISV product on AWS Marketplace", "A. Datum Software", 150_000, "2027-01-15", {
        "request_type": "Cloud Services Opportunity", "company_name": "Aivar Innovations", "ec_name": "A. Datum Software",
        "cs_vendor": "AWS", "aws_opportunity_type": "ISV workload migration",
        "cs_opportunity_details": "A. Datum, an ISV, wants to migrate its SaaS product to AWS and list it on AWS Marketplace."}),
    ("fabrikam", "Fabrikam Logistics rehost (segment unknown)", "Fabrikam Logistics", 180_000, "2027-02-01", {
        "request_type": "Cloud Services Opportunity", "company_name": "Contoso Partners", "ec_name": "Fabrikam Logistics",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration",
        "cs_opportunity_details": "Fabrikam Logistics runs 40 Windows VMs in their Pune data centre and wants to move "
                                  "them to AWS Mumbai.",
        "estimated_arr": "180,000"}),
    ("nimbus", "Nimbus Payments rehost (no VMs)", "Nimbus Payments", 144_000, "2027-02-10", {
        "request_type": "Cloud Services Opportunity", "company_name": "Fabrikam Reseller", "ec_name": "Nimbus Payments",
        "cs_vendor": "AWS", "aws_opportunity_type": "Migration",
        "cs_opportunity_details": "Seed-funded payments startup moving a container platform to AWS. No virtual machines "
                                  "to rehost.",
        "fund_segment": "Startup", "fund_workload": "Migration", "fund_migration_source": "Colocation",
        "fund_vms": "No", "fund_mrr_usd": 12000}),
    ("pioneer", "Pioneer Mutual claims copilot in production", "Pioneer Mutual", 480_000, "2027-02-20", {
        "request_type": "Data & AI Opportunity", "company_name": "Contoso Partners", "ec_name": "Pioneer Mutual",
        "dai_vendor": "AWS",
        "dai_details": "Pioneer Mutual is rolling a claims-handling copilot on Amazon Bedrock into production after a "
                       "successful pilot. Expected AWS spend is 40,000 USD per month.",
        "fund_segment": "Non-startup (enterprise and others)", "fund_workload": "Generative AI",
        "fund_genai_stage": "Production-ready", "fund_genai_type": "Fresh workload"}),
    ("quantum", "Quantum Learn tutor assistant modernisation", "Quantum Learn", 90_000, "2027-01-25", {
        "request_type": "Data & AI Opportunity", "company_name": "Aivar Innovations", "ec_name": "Quantum Learn",
        "dai_vendor": "AWS",
        "dai_details": "Quantum Learn is an edtech startup modernising its existing tutoring chatbot to Amazon Bedrock. "
                       "About 7,500 USD AWS spend per month.",
        "fund_segment": "Startup", "fund_workload": "Generative AI", "fund_genai_type": "Modernization or optimization"}),
]


def sample_records() -> list[tuple[str, dict, dict]]:
    """-> [(opportunity id, fields, answers)] in display order (newest first after seeding)."""
    out = []
    for key, name, customer, value, close, answers in SAMPLES:
        out.append((str(uuid.uuid5(NS, key)), {"name": name, "customer": customer, "estimated_value": value,
                                               "close_date": close, "state": "Open",
                                               "description": answers.get("cs_opportunity_details")
                                               or answers.get("dai_details", "")}, answers))
    return out
