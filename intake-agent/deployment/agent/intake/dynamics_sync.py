"""Give an opportunity that was read from the mailbox a Dynamics record, with its funding details filled in.

The funding check runs on a Dynamics opportunity (agent/funding/pipeline.py); a mailbox-only row has none, so it could not
be checked. After an email is parsed, `sync` either finds the customer's existing Dynamics opportunity or creates one from
the intake record: customer, contact, title, details, and the verified funding facts (fund_* answers, see
funding_facts.py). Nothing here may stop an Intake run, so every failure is returned, not raised.

INTAKE_DYNAMICS_SYNC=false turns the creation off (the agent then only reads the mailbox, as before).
"""
import logging
import os
import re
from typing import Any

import dynamics
import opportunity_form

from intake import funding_facts

logger = logging.getLogger("intake")

MAX_DETAILS_CHARS = 1800
DEFAULT_PARTNER = "Outlook mailbox"     # company_name is required by the form; the mailbox does not state the partner
# intake field id -> form answer id (free text copied as is; the form validates it)
FIELD_TO_ANSWER = {
    "customer_name": "ec_name", "contact_name": "ec_contact_name", "contact_email": "ec_contact_email",
    "contact_phone": "ec_contact_phone", "contact_role": "ec_job_title", "industry": "ec_industry",
    "headquarters_country": "ec_country", "opportunity_title": "cs_opportunity_title",
}
_SENDER = re.compile(r"^\s*\"?([^<\"]*?)\"?\s*<([^>]+@[^>]+)>\s*$")
_CORP = re.compile(r"\b(inc|ltd|llc|plc|gmbh|corp|corporation|company|co|group)\b\.?", re.I)


def enabled() -> bool:
    return os.environ.get("INTAKE_DYNAMICS_SYNC", "true").strip().lower() not in ("false", "0", "no", "off")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _CORP.sub("", (name or "").lower())).strip()


def _values(intake: dict[str, Any]) -> dict[str, str]:
    return {f["field"]: f["value"] for f in intake.get("fields", [])}


def _details(intake: dict[str, Any], values: dict[str, str]) -> str:
    """What the email says about the need, plus the funding passages (so the funding check's text reading sees them)."""
    parts = [values.get("business_problem"), values.get("scope_summary"), intake.get("summary")]
    parts += [f'Funding detail: "{f["source"]}"' for f in intake.get("funding", [])]
    parts += [f"Not converted: {n}" for n in intake.get("funding_notes", [])]
    seen, out = set(), []
    for p in parts:
        p = " ".join(str(p or "").split())
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "\n\n".join(out)[:MAX_DETAILS_CHARS]


def build_answers(intake: dict[str, Any], sender: str = "") -> dict[str, Any]:
    """Intake record -> form answers (unvalidated). Only what the email states is filled."""
    values = _values(intake)
    answers: dict[str, Any] = {"request_type": "Cloud Services Opportunity", "cs_vendor": "AWS",
                               "company_name": DEFAULT_PARTNER}
    for field_id, answer_id in FIELD_TO_ANSWER.items():
        if values.get(field_id):
            answers[answer_id] = values[field_id]
    match = _SENDER.match(sender or "")
    if match:
        answers["partner_contact_email"] = match.group(2).strip()
        if match.group(1).strip():
            answers["partner_contact_name"] = match.group(1).strip()
    facts = funding_facts.answers(intake.get("funding", []))
    answers.update(facts)
    workload = facts.get("fund_workload")
    answers["aws_opportunity_type"] = workload if workload else "Other"
    arr = facts.get("fund_arr_usd") or (facts["fund_mrr_usd"] * 12 if facts.get("fund_mrr_usd") else None)
    if arr:
        answers["estimated_arr"] = f"{round(arr):,}"
    details = _details(intake, values)
    if details:
        answers["cs_opportunity_details"] = details
    return answers


def validated(answers: dict[str, Any]) -> dict[str, Any]:
    """Run the form's validation; a free-text value that does not fit (a malformed email address) is dropped, not fatal."""
    required = {"request_type", "company_name", "ec_name"}
    for _ in range(3):
        try:
            return opportunity_form.validate(answers)
        except opportunity_form.FormError as exc:
            bad = set(exc.errors) - required
            if not bad or set(exc.errors) & required:
                raise
            answers = {k: v for k, v in answers.items() if k not in bad}
    return opportunity_form.validate(answers)


def existing_id(customer: str) -> str | None:
    """The newest open Dynamics opportunity of the same customer, if there is one."""
    wanted = _norm(customer)
    if len(wanted) < 3:
        return None
    for o in dynamics.search_opportunities(customer, 25, "open"):
        if _norm(o.get("customer", "")) == wanted or wanted in _norm(o.get("name", "")):
            return o["id"]
    return None


def sync(intake: dict[str, Any], sender: str = "") -> dict[str, Any]:
    """-> {"action": "created" | "existing" | "skipped" | "failed", "id"?, "url"?, "reason"?}. Never raises."""
    customer = _values(intake).get("customer_name", "")
    if not enabled():
        return {"action": "skipped", "reason": "INTAKE_DYNAMICS_SYNC is off"}
    if not customer:
        return {"action": "skipped", "reason": "the email does not name the customer"}
    try:
        found = existing_id(customer)
        if found:
            return {"action": "existing", "id": found}
        answers = validated(build_answers(intake, sender))
        arr = answers.get("fund_arr_usd") or (answers["fund_mrr_usd"] * 12 if answers.get("fund_mrr_usd") else None)
        created = dynamics.create_opportunity(
            {"name": opportunity_form.title_of(answers), "customer": customer,
             "description": opportunity_form.description_of(answers), "estimated_value": arr}, answers)
        logger.info("dynamics opportunity created from email id=%s funding_facts=%d", created["id"],
                    len(intake.get("funding", [])))
        return {"action": "created", "id": created["id"], "url": created.get("url")}
    except (dynamics.DynamicsError, opportunity_form.FormError, KeyError, ValueError) as exc:
        logger.warning("dynamics sync failed for '%s': %s", customer[:60], type(exc).__name__)
        return {"action": "failed", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
