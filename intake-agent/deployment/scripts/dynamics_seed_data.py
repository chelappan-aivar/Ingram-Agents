"""Dummy Dynamics 365 opportunities, as the Ingram intake form would create them: every opportunity has its form
answers in the ingr_ columns (shared/python/opportunity_form.COLUMNS), plus the standard opportunity columns.

Realistic, not perfect: most sellers answered the AWS funding section; some chose "Not sure" or skipped questions,
so the agent has to read the free-text answers (Claude) or assume the best fit. The 6 here are the Dynamics side of the demo (MAP, MAP Lite and a Gen AI POC); four more opportunities come from Outlook
(test-data/outlook-sample-emails.md, set 1). Every opportunity maps to a programme; the best-fit assumptions are listed on the decision when the seller left a fact open.
Fictional companies (Microsoft sample names). Loaded by scripts/seed_dynamics.py; answers are checked against the
form (opportunity_form.validate) before they are written, so they always fit the columns.
"""

INGRAM_IN = {"im_country": "India", "im_contact": "Anita Desai", "im_contact_phone": "+91 80 4012 5500",
             "im_bu": "Cloud", "im_account_manager": "Rahul Menon"}
INGRAM_UK = {"im_country": "United Kingdom", "im_contact": "Alex Parkinson", "im_contact_phone": "+44 1442 885 000",
             "im_bu": "Advanced Solutions", "im_account_manager": "Sophie Turner"}
INGRAM_EU = {"im_country": "Germany", "im_contact": "Jonas Weber", "im_contact_phone": "+49 89 4208 0",
             "im_bu": "Cloud", "im_account_manager": "Lena Fischer"}


def partner(company, contact, email, phone, signs=True, kind="Reseller"):
    p = {"company_name": company, "partner_contact_name": contact, "partner_contact_email": email,
         "partner_contact_phone": phone, "partner_sow_authority": "Yes" if signs else "No", "partner_contact_type": kind}
    if not signs:
        p.update(sow_signer_name="Meera Iyer", sow_signer_email="meera.iyer@" + email.split("@")[1])
    return p


AIVAR = partner("Aivar Innovations", "Priya Raman", "priya.raman@aivar.tech", "+91 98450 11223")
CONTOSO_P = partner("Contoso Partners", "Daniel Brooks", "daniel.brooks@contosopartners.example", "+44 20 7946 0101")
FABRIKAM_R = partner("Fabrikam Reseller", "Karl Schmidt", "karl.schmidt@fabrikamreseller.example", "+49 30 1234 567",
                     signs=False)


def customer(name, contact, title, email, phone, domain, country, postcode, industry, tpid=""):
    c = {"ec_name": name, "ec_contact_name": contact, "ec_job_title": title, "ec_contact_email": email,
         "ec_contact_phone": phone, "ec_domain": domain, "ec_country": country, "ec_postcode": postcode,
         "ec_industry": industry}
    if tpid:
        c["ec_tpid"] = tpid
    return c


def qualification(timeline="1-3 months", budget="Yes", proceed="High", selected="High", event="", competition="",
                  larger="No", close="Yes"):
    q = {"part_of_larger_program": larger, "close_to_decision_maker": close, "delivery_timeline": timeline,
         "budget_allocated": budget, "likelihood_proceed": proceed, "likelihood_selected": selected}
    if event:
        q["compelling_event"] = event
    if competition:
        q["competition"] = competition
    return q


def cloud(opp_type, opp_id, title, details, arr="", timeline="1-3 months", ace=None, next_step=""):
    c = {"request_type": "Cloud Services Opportunity", "cs_vendor": "AWS", "aws_opportunity_type": opp_type,
         "cs_opportunity_id": opp_id, "cs_opportunity_title": title, "cs_opportunity_details": details,
         "cs_timeline": timeline, "ace_registered": "Yes" if ace else "No"}
    if ace:
        c["ace_id"] = ace
    if arr:
        c["estimated_arr"] = arr
    if next_step:
        c["cs_next_step"] = next_step
    return c


def data_ai(opp_id, title, details, stage="Qualification", budget="Yes", timeline="1-3 months", kind="Project Delivery"):
    return {"request_type": "Data & AI Opportunity", "dai_vendor": "AWS", "dai_stage": stage, "dai_opportunity_id": opp_id,
            "dai_title": title, "dai_details": details, "dai_project_kind": kind, "dai_budget": budget,
            "dai_timeline": timeline, "partner_capabilities": "AWS Advanced Tier partner; 6 certified ML engineers.",
            "partner_support_areas": "Solution architecture review and AWS funding paperwork.",
            "dai_next_step": "Joint scoping call with the AWS Gen AI specialist."}


def funding(segment="", workload="", source="", vms="", stage="", kind="", mrr=None, arr=None):
    f = {}
    for key, value in (("fund_segment", segment), ("fund_workload", workload), ("fund_migration_source", source),
                       ("fund_vms", vms), ("fund_genai_stage", stage), ("fund_genai_type", kind)):
        if value:
            f[key] = value
    if mrr is not None:
        f["fund_mrr_usd"] = mrr
    if arr is not None:
        f["fund_arr_usd"] = arr
    return f


STARTUP, ENTERPRISE, NOT_SURE = "Startup", "Non-startup (enterprise and others)", "Not sure"

# (name, account, estimatedvalue, estimatedclosedate, statecode 0 open | 1 won | 2 lost, form answers, timeline notes)
OPPORTUNITIES = [
    ("Northwind Health - clinical notes summarisation", "Northwind Health", 300000, "2027-01-31", 0, {
        **INGRAM_UK, **CONTOSO_P,
        **customer("Northwind Health", "Dr Sam Patel", "Chief Digital Officer", "sam.patel@northwindhealth.example",
                   "+44 161 496 0300", "northwindhealth.example", "United Kingdom", "M1 1AE", "Healthcare"),
        **data_ai("DAI-24-0310", "Clinical notes summarisation on Amazon Bedrock",
                  "Northwind Health is a 12,000-employee hospital group. They want a proof of concept for summarising "
                  "clinical notes with Amazon Bedrock. Expected AWS spend is about 25,000 USD per month once live."),
        **qualification(timeline="1-3 months", event="NHS digital funding window closes in March."),
        **funding(NOT_SURE, "Generative AI", stage=NOT_SURE, kind="Fresh workload")}, []),

    ("Tailwind Robotics - VMware exit to AWS", "Tailwind Robotics", 180000, "2027-01-20", 0, {
        **INGRAM_UK, **AIVAR,
        **customer("Tailwind Robotics", "Sarah Whitfield", "COO", "sarah.whitfield@tailwindrobotics.example",
                   "+44 117 496 0800", "tailwindrobotics.example", "United Kingdom", "BS1 4DJ", "Manufacturing"),
        **cloud("Migration", "CS-24-1401", "Tailwind Robotics VMware exit",
                "Robotics startup leaving VMware after a large renewal increase. About 140 VMs on two clusters, "
                "including a GPU simulation farm.", "180,000", "3-6 months"),
        **qualification(timeline="3-6 months", event="VMware renewal decision due within 90 days."),
        **funding(STARTUP, "Migration", "On-premises", "Yes", mrr=15000)}, []),   # MAP Lite
    ("Woodgrove Bank - data centre exit assessment", "Woodgrove Bank", 720000, "2027-03-31", 0, {
        **INGRAM_UK, **CONTOSO_P,
        **customer("Woodgrove Bank", "Hannah Clarke", "Head of Infrastructure Transformation",
                   "hannah.clarke@woodgrovebank.example", "+44 20 7946 0200", "woodgrovebank.example", "United Kingdom",
                   "EC2N 1HQ", "Financial Services"),
        **cloud("Migration", "CS-24-1402", "Woodgrove Bank data centre exit assessment",
                "Bank exiting two data centres (about 400 VMs) before a lease ends in 18 months. Assessment first, "
                "regulated workloads in scope.", "720,000", "6-12 months"),
        **qualification(timeline="6-12 months", event="Data centre lease ends in 18 months."),
        **funding(ENTERPRISE, "Migration", "On-premises", "Yes", mrr=60000)}, []),   # MAP
    ("Relecloud - SaaS rehost, 18 VMs", "Relecloud", 150000, "2027-02-15", 0, {
        **INGRAM_IN, **AIVAR,
        **customer("Relecloud", "Priya Shah", "CEO", "priya.shah@relecloud.example", "+91 40 4555 1900",
                   "relecloud.example", "India", "500081", "Software"),
        **cloud("Migration", "CS-24-1403", "Relecloud SaaS rehost",
                "Seed-stage startup running 18 VMs in a colocation facility in Hyderabad, rehosting on EC2. Expected "
                "AWS spend is 12,500 USD per month.", "150,000", "1-3 months"),
        **qualification(timeline="1-3 months", event="Colocation contract ends in four months."),
        **funding(STARTUP, "Migration", "Colocation", "Yes", mrr=12500)}, []),   # MAP Lite
    ("Fabrikam Logistics - rehost 40 Windows VMs", "Fabrikam Logistics", 180000, "2027-02-28", 0, {
        **INGRAM_IN, **AIVAR,
        **customer("Fabrikam Logistics", "Lena Fischer", "Head of IT", "lena.fischer@fabrikamlogistics.example",
                   "+91 20 4555 2100", "fabrikamlogistics.example", "India", "411001", "Transportation"),
        **cloud("Migration", "CS-24-1404", "Fabrikam Logistics Windows rehost",
                "Logistics company moving 40 Windows VMs from its Pune data centre to AWS Mumbai.", "180,000",
                "3-6 months"),
        **qualification(timeline="3-6 months", event="Move before the October festive peak."),
        **funding(ENTERPRISE, "Migration", "On-premises", "Yes", mrr=15000)}, []),   # MAP Lite
    ("Initech Energy - billing platform move to AWS", "Initech Energy", 780000, "2027-04-15", 0, {
        **INGRAM_UK, **CONTOSO_P,
        **customer("Initech Energy", "Paul Reeves", "Head of Infrastructure", "paul.reeves@initechenergy.example",
                   "+44 131 496 0700", "initechenergy.example", "United Kingdom", "EH2 2ER", "Energy"),
        **cloud("Migration", "CS-24-1301", "Initech Energy billing platform to AWS",
                "Initech Energy (6,000 employees) is moving its customer billing platform from Azure to AWS. About 150 "
                "servers and two Oracle databases are in scope.", "780,000", "6-12 months", "O3345678"),
        **qualification(timeline="6-12 months", event="Azure enterprise agreement ends in April 2027."),
        **funding(ENTERPRISE, "Migration", "Another cloud", "Yes", mrr=65000)}, []),   # MAP

]


def records():
    """-> [(name, account, body for POST /opportunities without the account bind, notes)], answers validated."""
    import opportunity_form

    out = []
    for n, (name, account, value, close, state, answers, notes) in enumerate(OPPORTUNITIES):
        clean = opportunity_form.validate(answers)   # raises if a sample does not fit the form
        dropped = set(answers) - set(clean)
        assert not dropped, f"{name}: answers not on the form for this request type: {sorted(dropped)}"
        clean.update(_form_response_id=f"{1000 + n}", _form_start_time=f"2026-10-{1 + n:02d}T09:{10 + n:02d}:00Z",
                     _form_completion_time=f"2026-10-{1 + n:02d}T09:{25 + n:02d}:00Z",
                     _submitted_by=answers.get("partner_contact_email", ""),
                     _submitted_name=answers.get("partner_contact_name", ""))
        body = {"name": name, "description": opportunity_form.description_of(clean), "estimatedclosedate": close,
                "statecode": state, **opportunity_form.to_columns(clean)}
        if value is not None:
            body["estimatedvalue"] = float(value)
        out.append((name, account, body, notes))
    return out


def load(dynamics, real: bool = False, log=print) -> int:
    """Create every sample opportunity that is not there yet (matched by name). -> number created."""
    existing = {o["name"] for o in dynamics.search_opportunities("", 100, "all")}
    accounts: dict[str, str] = {}
    created = 0
    for name, account, body, notes in records():
        if name in existing:
            continue
        if account not in accounts:
            accounts[account] = dynamics._find_or_create_account(account)
        body = {**body, "customerid_account@odata.bind": f"/accounts({accounts[account]})"}
        if real:   # real Dataverse closes opportunities with WinOpportunity / LoseOpportunity, not statecode
            body.pop("statecode")
        _, headers = dynamics._request("POST", "opportunities", body)
        opp_id = dynamics._id_from_headers(headers)
        for subject, text in notes:
            dynamics._note(opp_id, subject, text)
        created += 1
        log(f"  + {name}")
    return created
