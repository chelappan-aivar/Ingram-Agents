"""The opportunity form: every question in "Form input questions .xlsx", plus the inputs the AWS Funding
Programs Decision Framework needs that the form does not ask (section "AWS funding", marked added=True).

The xlsx lists questions only, no answer options. Choice options below are assumptions; questions whose
options could not be inferred stay free text. Edit this file to change the form, then run
scripts/gen_form_js.py (deploy.sh does) so the UI renders the same form the API validates.

Not included: the two "(optional) Fileupload" questions (attachments are not stored in this version), and the
Microsoft Forms metadata (Id, Start time, Completion time, Email, Name), which are filled in automatically.

Used by the API Lambda (validate) and the agent (answers_text), through the shared layer.
"""

import re
from datetime import date
from typing import Any

YES_NO = ["Yes", "No"]
YES_NO_UNSURE = ["Yes", "No", "Not sure"]
TIMELINE = ["Within 1 month", "1-3 months", "3-6 months", "6-12 months", "More than 12 months"]
LIKELIHOOD = ["High", "Medium", "Low"]
REQUEST_TYPES = ["Assessment", "Cloud Services Opportunity", "Cyber Security Opportunity", "Marketing Campaign",
                 "Generic Service Request", "Data & AI Opportunity", "Microsoft Copilot Opportunity", "General Inquiry"]
OPPORTUNITY_TYPES = ["Cloud Services Opportunity", "Cyber Security Opportunity", "Generic Service Request",
                     "Data & AI Opportunity", "Microsoft Copilot Opportunity"]
SOURCE_ENVIRONMENTS = ["On-premises data centre", "VMware", "Colocation", "Microsoft Azure", "Google Cloud",
                       "Other cloud", "AWS (already)", "Mixed"]


def _f(fid: str, label: str, kind: str = "text", **extra: Any) -> dict[str, Any]:
    return {"id": fid, "label": label, "type": kind, **extra}


def _when(field: str, *values: str) -> dict[str, Any]:
    return {"field": field, "in": list(values)}


SECTIONS: list[dict[str, Any]] = [
    {"id": "request", "title": "Request", "fields": [
        _f("request_type", "What type of request would you like to submit?", "select", options=REQUEST_TYPES,
           required=True),
    ]},
    {"id": "ingram", "title": "Ingram Micro", "fields": [
        _f("im_country", "Ingram Micro Country"),
        _f("im_contact", "Ingram Micro Associate Contact (Primary Contact)"),
        _f("im_contact_phone", "Ingram Micro Associate Contact Phone Number", "tel"),
        _f("im_bu", "Ingram Micro BU"),
        _f("im_account_manager", "Responsible Ingram Micro Account Manager"),
        _f("campaign_code", "Campaign Code (if known)"),
    ]},
    {"id": "partner", "title": "Partner", "fields": [
        _f("company_name", "Company Name", required=True, help="The partner (reseller) company"),
        _f("partner_contact_name", "Partner Contact Name"),
        _f("partner_contact_phone", "Partner Contact Phone Number", "tel"),
        _f("partner_contact_email", "Partner Contact Email Address", "email"),
        _f("partner_sow_authority", "Does the partner contact have signing authority for the Statement of Work (SOW)?",
           "radio", options=YES_NO),
        _f("sow_signer_name", "Please provide the name of the person authorized to sign the SOW.",
           show_when=_when("partner_sow_authority", "No")),
        _f("sow_signer_email", "Please provide the e-mail address of the person authorized to sign the SOW.", "email",
           show_when=_when("partner_sow_authority", "No")),
        _f("partner_contact_type", "Partner Contact Type"),
    ]},
    {"id": "end_customer", "title": "End customer", "fields": [
        _f("ec_name", "End-Customer Customer Name", required=True),
        _f("ec_contact_name", "End-Customer Contact Name"),
        _f("ec_contact_phone", "End-Customer Contact Phone Number", "tel"),
        _f("ec_contact_email", "End-Customer Contact Email Address", "email"),
        _f("ec_job_title", "End-Customer Contact Job Title"),
        _f("ec_domain", "End-Customer Business Domain", help="e.g. contoso.com"),
        _f("ec_country", "End-Customer Country"),
        _f("ec_postcode", "End-Customer Post Code / Zip Code"),
        _f("ec_industry", "End-Customer Industry Vertical"),
        _f("ec_tpid", "End-Customer Microsoft Top Parent ID (TPID) if known"),
    ]},
    {"id": "assessment", "title": "Assessment", "show_when": _when("request_type", "Assessment"), "fields": [
        _f("service_selection", "Service Selection"),
        _f("assessment_vendor", "Which Vendor Assessment?", "select", options=["AWS", "Microsoft Azure", "Google Cloud", "Other"]),
        _f("key_driver", "What is the customers key driver for considering the cloud?", "textarea"),
        _f("ace_validated", "Has this opportunity been registered and validated in ACE?", "radio", options=YES_NO),
        _f("ace_id_assessment", "Please provide the ACE ID.", show_when=_when("ace_validated", "Yes")),
        _f("anticipated_start_date", "Anticipated Start Date (dd/mm/yyyy)", "date"),
        _f("servers_total", "Total Number of Servers", "number"),
        _f("servers_windows", "Total Number of Windows Servers", "number"),
        _f("servers_sql", "Total Number of SQL Servers", "number"),
        _f("sql_cores", "Total Number of SQL Cores", "number"),
        _f("source_environment", "What source environment does the customer currently have?", "select",
           options=SOURCE_ENVIRONMENTS),
        _f("discovery_tool_permitted",
           "Will the end customer permit the deployment of a data discovery tool within their environment?",
           "radio", options=YES_NO_UNSURE),
    ]},
    {"id": "cloud_services", "title": "Cloud Services opportunity",
     "show_when": _when("request_type", "Cloud Services Opportunity"), "fields": [
        _f("cs_vendor", "Which vendor is your request against?", "select",
           options=["AWS", "Microsoft", "Google Cloud", "Other"]),
        _f("ms_opportunity_type", "What Microsoft Opportunity Type is this?", show_when=_when("cs_vendor", "Microsoft")),
        _f("aws_opportunity_type", "What AWS opportunity type is this?", "select", show_when=_when("cs_vendor", "AWS"),
           options=["Migration", "Generative AI", "ISV workload migration", "SAP migration", "Oracle migration",
                    "Modernization", "Data & Analytics", "New workload", "Other"]),
        _f("ace_registered", "Has this opportunity been registered in ACE yet?", "radio", options=YES_NO),
        _f("ace_id", "Please enter the ACE ID.", show_when=_when("ace_registered", "Yes")),
        _f("cs_opportunity_id", "Cloud Services Opportunity ID"),
        _f("cs_opportunity_title", "Cloud Services Opportunity Title"),
        _f("cs_opportunity_details", "Cloud Services Opportunity Details", "textarea"),
        _f("estimated_arr", "Estimated Annual Recurring Revenue (ARR)", help="AWS spend per year in USD, e.g. 350,000"),
        _f("cs_timeline", "What's the expected timeline for commencing the delivery of this opportunity?", "select",
           options=TIMELINE),
        _f("cs_next_step", "What is your preferred next step from the Ingram Micro Cloud Services team?", "textarea"),
    ]},
    {"id": "cyber", "title": "Cyber Security opportunity",
     "show_when": _when("request_type", "Cyber Security Opportunity"), "fields": [
        _f("cyber_opportunity_id", "Cyber Security Opportunity ID"),
        _f("cyber_opportunity_title", "Cyber Security Opportunity Title"),
        _f("cyber_service_kind", "What kind of Cyber Security Service are you looking for?"),
        _f("cyber_customer_knows", "Does the customer know what they want , or do they need additional Information ?",
           "radio", options=["They know what they want", "They need additional information"]),
        _f("cyber_interested_services", "They are interested in the following Services (please add the Details in the "
           "seperate Box below (Number of IP adresses)", "textarea"),
        _f("cyber_details_ips", "Additional Details (Scope of the Service - Number of external/internal IPs | URLs of "
           "WebApplications)", "textarea"),
        _f("cyber_security_service", "Which kind of security service ?"),
        _f("cyber_details_scope", "Additional Details (Scope of the Service)", "textarea"),
    ]},
    {"id": "campaign", "title": "Marketing campaign", "show_when": _when("request_type", "Marketing Campaign"), "fields": [
        _f("campaign_for", "The campaign should be made for a"),
        _f("campaign_vendors", "Which vendor(s) is this campaign focused on?"),
        _f("campaign_domains", "How many domains are involved ?", "number"),
        _f("campaign_help", "Where can we help ?", "textarea"),
    ]},
    {"id": "generic", "title": "Generic service request", "show_when": _when("request_type", "Generic Service Request"),
     "fields": [
        _f("gsr_opportunity_id", "Generic Service Request Opportunity ID"),
        _f("gsr_title", "Generic Service Request Opportunity title"),
        _f("gsr_description", "Generic Service Request detailed opportunity description", "textarea"),
        _f("gsr_vendors", "Service request is related to which vendor(s)?"),
        _f("delivery_language", "Required delivery language."),
        _f("delivery_method", "Required delivery method.", "select", options=["Remote", "On premise", "Hybrid"]),
        _f("fulfillment_address", "Fulfillment address (required for on premise delivery)", "textarea",
           show_when=_when("delivery_method", "On premise", "Hybrid")),
        _f("delivery_timeframe", "Delivery time frame"),
        _f("response_criticality", "How critical is the response time?", "select", options=["Critical", "High", "Normal", "Low"]),
        _f("why_other_delivery", "Why are you looking to leverage other service delivery organizations on this opportunity?",
           "textarea"),
        _f("gsr_next_step", "What is your preferred next step?", "textarea"),
    ]},
    {"id": "data_ai", "title": "Data & AI opportunity", "show_when": _when("request_type", "Data & AI Opportunity"),
     "fields": [
        _f("dai_vendor", "Please select a vendor.", "select", options=["AWS", "Microsoft", "Google Cloud", "Other"]),
        _f("dai_stage", "Which Stage Is the Opportunity In", "select",
           options=["Prospecting", "Qualification", "Proposal", "Negotiation", "Committed"]),
        _f("dai_opportunity_id", "Data & AI Opportunity ID"),
        _f("dai_title", "Data & AI Opportunity Title"),
        _f("dai_details", "Data & AI Opportunity Details", "textarea"),
        _f("dai_project_kind", "Is this project for a specific end-customer (Project Delivery), or is the partner developing "
           "a new solution to support multiple end-customers (Solution Factory)?", "radio",
           options=["Project Delivery", "Solution Factory"]),
        _f("dai_budget", "Does the customer have a budget allocated for this Data & AI project?", "radio", options=YES_NO_UNSURE),
        _f("partner_capabilities", "Partner Delivery Capabilities", "textarea"),
        _f("partner_support_areas", "Areas where Partner requires support", "textarea"),
        _f("dai_timeline", "Expected timeline for commencing the delivery of this Data & AI opportunity?", "select",
           options=TIMELINE),
        _f("dai_next_step", "Preferred next step for this Data & AI opportunity?", "textarea"),
    ]},
    {"id": "copilot", "title": "Microsoft Copilot opportunity",
     "show_when": _when("request_type", "Microsoft Copilot Opportunity"), "fields": [
        _f("copilot_opportunity_id", "Microsoft Copilot Opportunity ID"),
        _f("copilot_title", "Microsoft Copilot Opportunity Title"),
        _f("ec_business_size", "End-customer Business Size", "select",
           options=["Startup", "Small business", "Mid-market", "Enterprise", "Public sector"]),
        _f("copilot_licenses", "How many Copilot licenses does the end-customer currently have?", "number"),
        _f("ec_website", "End-customer Website"),
        _f("exec_sponsor", "Does the end-customer have an executive sponsor for AI adoption?", "radio", options=YES_NO),
        _f("exec_attend", "Will executive leadership attend the Exec Planning Session?", "radio", options=YES_NO),
        _f("ai_budget", "Does the end-customer have a budget allocated for AI tools & related services for the next 3-6 "
           "months?", "radio", options=YES_NO_UNSURE),
    ]},
    {"id": "qualification", "title": "Qualification", "show_when": _when("request_type", *OPPORTUNITY_TYPES), "fields": [
        _f("part_of_larger_program", "Is this opportunity part of a larger end-customer program / project?", "radio",
           options=YES_NO_UNSURE),
        _f("close_to_decision_maker", "Are you close to the reseller / end-customer decision maker for this opportunity?",
           "radio", options=YES_NO),
        _f("compelling_event", "Are you aware of a compelling end-customer event that is driving this opportunity?",
           "textarea"),
        _f("delivery_timeline", "What's the expected timeline for commencing the delivery of this opportunity?", "select",
           options=TIMELINE),
        _f("budget_allocated", "Does the end-customer already have a budget allocated?", "radio", options=YES_NO_UNSURE),
        _f("competition", "Are you aware of anyone competing for the same opportunity (provide details)?", "textarea"),
        _f("likelihood_proceed", "How likely do you believe the reseller / end-customer is going to proceed with the "
           "opportunity?", "select", options=LIKELIHOOD),
        _f("likelihood_selected", "How likely do you believe the reseller / end-customer will select yourselves as the "
           "preferred delivery partner?", "select", options=LIKELIHOOD),
    ]},
    {"id": "inquiry", "title": "General inquiry", "show_when": _when("request_type", "General Inquiry"), "fields": [
        _f("requesting_country", "Requesting Country"),
        _f("practice", "Practice"),
        _f("vendors_involved", "Vendor(s) Involved"),
        _f("technology", "Technology"),
        _f("estimated_delivery_time", "Estimated Delivery Time (if known)"),
        _f("inquiry_details", "Inquiry Details", "textarea"),
    ]},
    {"id": "funding", "title": "AWS funding", "added": True,
     "description": "Not in the original form: the inputs the AWS Funding Programs Decision Framework needs. "
                    "Leave anything you don't know as 'Not sure'; the agent looks for it in the other answers.",
     "fields": [
        _f("fund_segment", "Customer segment", "radio", options=["Startup", "Non-startup (enterprise and others)", "Not sure"]),
        _f("fund_workload", "Workload", "select",
           options=["Migration", "Generative AI", "ISV workload migration", "SAP migration", "Oracle migration",
                    "Other", "Not sure"]),
        _f("fund_migration_source", "Migrating from", "radio",
           options=["On-premises", "Another cloud", "Colocation", "Not sure"],
           show_when=_when("fund_workload", "Migration")),
        _f("fund_vms", "Are virtual machines (VMs) in scope?", "radio", options=YES_NO_UNSURE,
           show_when=_when("fund_workload", "Migration", "Not sure")),
        _f("fund_genai_stage", "Gen AI stage", "radio", options=["Proof of concept", "Production-ready", "Not sure"],
           show_when=_when("fund_workload", "Generative AI")),
        _f("fund_genai_type", "Gen AI workload", "radio",
           options=["Fresh workload", "Modernization or optimization", "Not sure"],
           show_when=_when("fund_workload", "Generative AI")),
        _f("fund_mrr_usd", "Expected AWS monthly run rate (MRR), USD", "number",
           help="ARR = MRR x 12. Leave empty if you only know the ARR."),
        _f("fund_arr_usd", "Expected AWS annual recurring revenue (ARR), USD", "number",
           help="Used when MRR is empty."),
    ]},
    {"id": "closing", "title": "Anything else", "fields": [
        _f("other_remarks", "Other remarks or additional comments", "textarea"),
    ]},
]

FIELDS: dict[str, dict[str, Any]] = {f["id"]: f for s in SECTIONS for f in s["fields"]}
SECTION_OF: dict[str, dict[str, Any]] = {f["id"]: s for s in SECTIONS for f in s["fields"]}
TITLE_FIELDS = ["cs_opportunity_title", "gsr_title", "dai_title", "copilot_title", "cyber_opportunity_title"]
DETAIL_FIELDS = ["cs_opportunity_details", "gsr_description", "dai_details", "inquiry_details", "campaign_help",
                 "cyber_details_scope", "key_driver", "other_remarks"]
MAX_TEXT = 300
MAX_TEXTAREA = 8000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

assert len(FIELDS) == sum(len(s["fields"]) for s in SECTIONS), "duplicate field id"


class FormError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{FIELDS.get(k, {}).get('label', k)}: {v}" for k, v in errors.items()))
        self.errors = errors


def _visible(rule: dict[str, Any] | None, answers: dict[str, Any]) -> bool:
    return rule is None or answers.get(rule["field"]) in rule["in"]


def visible_fields(answers: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in SECTIONS:
        if _visible(s.get("show_when"), answers):
            out += [f for f in s["fields"] if _visible(f.get("show_when"), answers)]
    return out


def validate(raw: Any) -> dict[str, Any]:
    """Browser answers -> clean answers (visible fields only, trimmed, typed). Raises FormError."""
    if not isinstance(raw, dict):
        raise FormError({"answers": "must be an object"})
    answers: dict[str, Any] = {}
    # Two passes: show_when depends on choice answers, so read those first.
    staged = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k in FIELDS}
    errors: dict[str, str] = {}
    for f in visible_fields(staged):
        value = staged.get(f["id"])
        if value is None or value == "":
            if f.get("required"):
                errors[f["id"]] = "required"
            continue
        kind = f["type"]
        if kind in ("select", "radio"):
            if value not in f["options"]:
                errors[f["id"]] = "not one of the options"
                continue
        elif kind == "number":
            try:
                number = float(str(value).replace(",", ""))
            except ValueError:
                errors[f["id"]] = "must be a number"
                continue
            if number < 0 or number > 1e12:
                errors[f["id"]] = "out of range"
                continue
            value = int(number) if number == int(number) else number
        elif kind == "date":
            try:
                date.fromisoformat(str(value))
            except ValueError:
                errors[f["id"]] = "must be a date (YYYY-MM-DD)"
                continue
        else:
            if not isinstance(value, str):
                errors[f["id"]] = "must be text"
                continue
            limit = MAX_TEXTAREA if kind == "textarea" else MAX_TEXT
            if len(value) > limit:
                errors[f["id"]] = f"at most {limit} characters"
                continue
            if kind == "email" and not _EMAIL.match(value):
                errors[f["id"]] = "not an email address"
                continue
        answers[f["id"]] = value
    if errors:
        raise FormError(errors)
    return answers


_NUM = re.compile(r"(?i)(?:usd|us\$|\$)?\s*([0-9][0-9,.\s]*)\s*(k|m|mn|million|thousand)?\b")


def parse_amount(value: Any) -> float | None:
    """'350,000' / '$350k' / '1.2M' / 350000 -> number; None when there is no single clear amount."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value or "").strip()
    found = _NUM.findall(text)
    if len(found) != 1:
        return None
    digits, unit = found[0]
    digits = digits.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", digits):
        digits = digits.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", digits):   # 350.000 (European thousands)
        digits = digits.replace(".", "")
    try:
        number = float(digits.replace(",", "."))
    except ValueError:
        return None
    number *= {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6}.get(unit.lower(), 1)
    return number if number > 0 else None


def title_of(answers: dict[str, Any]) -> str:
    title = next((answers[k] for k in TITLE_FIELDS if answers.get(k)), "")
    return (title or f"{answers.get('ec_name', 'Customer')} - {answers.get('request_type', 'Opportunity')}")[:300]


def description_of(answers: dict[str, Any]) -> str:
    return "\n\n".join(str(answers[k]) for k in DETAIL_FIELDS if answers.get(k))[:2000]


def answers_text(answers: dict[str, Any]) -> str:
    """The answers as 'Label: value' lines grouped by section, for the agent's model call."""
    lines, current = [], None
    for fid, value in answers.items():
        f = FIELDS.get(fid)
        if f is None or value in (None, ""):
            continue
        section = SECTION_OF[fid]["title"]
        if section != current:
            lines.append(f"\n## {section}")
            current = section
        lines.append(f"{f['label']}: {value}")
    return "\n".join(lines).strip()


def ui_schema() -> list[dict[str, Any]]:
    """The form as the UI renders it (ui/form.js)."""
    return SECTIONS


# ---------------------------------------------------------------- Dynamics 365 columns: one per question
# Each form question is a custom column on the Dataverse `opportunity` table, prefix `ingr_` (the customization
# prefix of the Ingram publisher). Logical name = ingr_ + question id without underscores, unless renamed below.
# Choice questions are local choice (picklist) columns: option values 100000000, 100000001, ... in option order.
# The Microsoft Forms response metadata (Id, Start time, Completion time, Email, Name) gets columns too.
# scripts/create_dynamics_columns.py creates these columns in a real environment; docs/dynamics-columns.md lists them.

PREFIX = "ingr_"
CHOICE_BASE = 100_000_000
COLUMN_RENAMES = {   # question id -> logical name, where the default would read badly
    "servers_total": "ingr_totalservers", "servers_windows": "ingr_totalwindowsservers",
    "servers_sql": "ingr_totalsqlservers", "sql_cores": "ingr_totalsqlcores",
    "ec_name": "ingr_endcustomername", "ec_contact_name": "ingr_endcustomercontactname",
    "ec_contact_phone": "ingr_endcustomercontactphone", "ec_contact_email": "ingr_endcustomercontactemail",
    "ec_job_title": "ingr_endcustomerjobtitle", "ec_domain": "ingr_endcustomerdomain",
    "ec_country": "ingr_endcustomercountry", "ec_postcode": "ingr_endcustomerpostcode",
    "ec_industry": "ingr_endcustomerindustry", "ec_tpid": "ingr_endcustomertpid",
    "ec_business_size": "ingr_endcustomerbusinesssize", "ec_website": "ingr_endcustomerwebsite",
    "im_country": "ingr_ingramcountry", "im_contact": "ingr_ingramcontact", "im_contact_phone": "ingr_ingramcontactphone",
    "im_bu": "ingr_ingrambu", "im_account_manager": "ingr_ingramaccountmanager",
}
DECIMAL_FIELDS = {"fund_mrr_usd", "fund_arr_usd"}   # money: decimal columns; other numbers are whole numbers
META_COLUMNS = [   # Microsoft Forms response metadata (rows 1-5 of the question list); filled automatically
    {"id": "_form_response_id", "label": "Id", "type": "text", "column": "ingr_formresponseid"},
    {"id": "_form_start_time", "label": "Start time", "type": "datetime", "column": "ingr_formstarttime"},
    {"id": "_form_completion_time", "label": "Completion time", "type": "datetime", "column": "ingr_formcompletiontime"},
    {"id": "_submitted_by", "label": "Email", "type": "email", "column": "ingr_submitteremail"},
    {"id": "_submitted_name", "label": "Name", "type": "text", "column": "ingr_submittername"},
]


def column_of(fid: str) -> str:
    return COLUMN_RENAMES.get(fid) or PREFIX + fid.replace("_", "")


def column_type(f: dict[str, Any]) -> str:
    """Dataverse column type: Choice | WholeNumber | Decimal | DateOnly | DateTime | MultilineText | Email | Phone | Text."""
    kind = f["type"]
    if kind in ("select", "radio"):
        return "Choice"
    if kind == "number":
        return "Decimal" if f["id"] in DECIMAL_FIELDS else "WholeNumber"
    return {"date": "DateOnly", "datetime": "DateTime", "textarea": "MultilineText", "email": "Email",
            "tel": "Phone"}.get(kind, "Text")


COLUMNS: list[dict[str, Any]] = (
    [{"id": m["id"], "label": m["label"], "column": m["column"], "column_type": column_type(m), "section": "Form response",
      "options": None, "meta": True} for m in META_COLUMNS]
    + [{"id": f["id"], "label": f["label"], "column": column_of(f["id"]), "column_type": column_type(f),
        "section": SECTION_OF[f["id"]]["title"], "options": f.get("options"), "meta": False} for f in FIELDS.values()])
COLUMN_OF = {c["id"]: c for c in COLUMNS}
BY_COLUMN = {c["column"]: c for c in COLUMNS}
for _f in FIELDS.values():
    _f["column"] = column_of(_f["id"])   # shown on the form (ui/form.js)

assert len(BY_COLUMN) == len(COLUMNS), "two questions map to the same column"
assert all(len(c["column"]) <= 50 and re.fullmatch(r"ingr_[a-z0-9]+", c["column"]) for c in COLUMNS), "bad column name"


def to_columns(answers: dict[str, Any]) -> dict[str, Any]:
    """Validated answers (question id -> value) -> Dataverse column values (choice label -> option value)."""
    out: dict[str, Any] = {}
    for fid, value in answers.items():
        c = COLUMN_OF.get(fid)
        if c is None or value in (None, ""):
            continue
        if c["column_type"] == "Choice":
            out[c["column"]] = CHOICE_BASE + c["options"].index(value)
        elif c["column_type"] == "WholeNumber":
            out[c["column"]] = int(value)
        elif c["column_type"] == "Decimal":
            out[c["column"]] = float(value)
        else:
            out[c["column"]] = str(value)
    return out


def from_columns(row: dict[str, Any]) -> dict[str, Any]:
    """A Dataverse opportunity row -> answers (question id -> value), in form order. Unknown option values are kept
    as their formatted label when Dataverse sent one, so a renamed option in Dynamics still reads sensibly."""
    out: dict[str, Any] = {}
    for c in COLUMNS:
        value = row.get(c["column"])
        if value is None or value == "":
            continue
        if c["column_type"] == "Choice":
            i = int(value) - CHOICE_BASE
            label = c["options"][i] if 0 <= i < len(c["options"]) else row.get(
                f"{c['column']}@OData.Community.Display.V1.FormattedValue")
            if label is None:
                continue
            value = label
        elif c["column_type"] == "DateOnly":
            value = str(value)[:10]
        elif c["column_type"] in ("WholeNumber", "Decimal"):
            value = int(value) if float(value) == int(float(value)) else float(value)
        out[c["id"]] = value
    return out


def answer_columns() -> list[str]:
    return [c["column"] for c in COLUMNS]
