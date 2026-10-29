"""An email's funding details end up on a Dynamics opportunity, so the funding check can run on it. No AWS, no model.

    cd deployment && python3 -m pytest tests/test_email_funding_sync.py -q
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "shared" / "python")]

import opportunity_form as form  # noqa: E402
from intake import dynamics_sync, funding_facts  # noqa: E402
from funding import facts as F  # noqa: E402

BODY = ("Orbit Mobility is a Series B startup. They are migrating their fleet-tracking platform, about 60 VMs, from a "
        "colocation facility to AWS Mumbai. Expected AWS spend is 30,000 USD per month. Contact: Kavya Nair, VP Engineering.")
DOC = BODY.lower()


def verified(passage, doc):
    return passage.lower().strip(" .") in doc


def pick(*pairs):
    return [{"fact": f, "value": v, "source": s, "currency": c} for f, v, s, c in pairs]


def intake(**extra):
    fields = {"customer_name": "Orbit Mobility", "opportunity_title": "Orbit Mobility fleet platform migration",
              "contact_name": "Kavya Nair", "contact_role": "VP Engineering", "contact_email": "not an address",
              "business_problem": "Move the fleet-tracking platform out of colocation"}
    return {"summary": "A Series B startup migrates 60 VMs.", "funding": [], "funding_notes": [],
            "fields": [{"field": k, "value": v} for k, v in fields.items()], **extra}


def test_only_verified_form_values_are_kept():
    raw = pick(("segment", "Startup", "Orbit Mobility is a Series B startup", ""),
               ("workload", "Migration", "migrating their fleet-tracking platform", ""),
               ("migration_source", "Colocation", "from a colocation facility to AWS Mumbai", ""),
               ("vms", "Yes", "about 60 VMs", ""),
               ("mrr_usd", "30,000", "Expected AWS spend is 30,000 USD per month", "usd"),
               ("segment", "Enterprise", "Orbit Mobility is a Series B startup", ""),       # not a form label, and a repeat
               ("genai_stage", "Proof of concept", "a passage that is not in the email", ""))  # not verified
    facts, notes = funding_facts.clean(raw, DOC, verified)
    assert {f["fact"]: f["value"] for f in facts} == {
        "segment": "Startup", "workload": "Migration", "migration_source": "Colocation", "vms": "Yes", "mrr_usd": 30000}
    assert notes == []


def test_other_currencies_are_not_converted_but_kept_as_a_note():
    raw = pick(("mrr_usd", "45000", "Expected AWS spend is about 45,000 EUR per month", "EUR"))
    facts, notes = funding_facts.clean(raw, "expected aws spend is about 45,000 eur per month", verified)
    assert facts == [] and "45000 EUR" in notes[0]


def test_answers_use_the_form_ids_and_pass_the_form_validation():
    facts, _ = funding_facts.clean(pick(
        ("segment", "Startup", "Orbit Mobility is a Series B startup", ""),
        ("workload", "Migration", "migrating their fleet-tracking platform", ""),
        ("migration_source", "Colocation", "from a colocation facility to AWS Mumbai", ""),
        ("vms", "Yes", "about 60 VMs", ""),
        ("mrr_usd", "30000", "Expected AWS spend is 30,000 USD per month", "USD")), DOC, verified)
    answers = dynamics_sync.validated(dynamics_sync.build_answers(intake(funding=facts), 'Daniel <daniel@ingram.example>'))
    assert answers["fund_segment"] == "Startup" and answers["fund_mrr_usd"] == 30000
    assert answers["aws_opportunity_type"] == "Migration" and answers["estimated_arr"] == "360,000"
    assert answers["partner_contact_name"] == "Daniel" and "ec_contact_email" not in answers   # bad address dropped
    # the funding check reads exactly these answers
    got = F.from_form(answers, {})
    assert {k: got[k]["value"] for k in ("segment", "workload", "migration_source", "vms", "mrr_usd")} == {
        "segment": "startup", "workload": "migration", "migration_source": "colocation", "vms": True, "mrr_usd": 30000}


class FakeDynamics:
    class DynamicsError(Exception):
        pass

    def __init__(self, existing=()):
        self.existing, self.created = list(existing), []

    def search_opportunities(self, text, top, state):
        return [o for o in self.existing if text.lower() in o["name"].lower()]

    def create_opportunity(self, fields, answers):
        self.created.append((fields, answers))
        return {"id": "11111111-1111-4111-8111-111111111111", "url": None}


def test_sync_creates_one_opportunity_with_the_funding_answers(monkeypatch):
    fake = FakeDynamics()
    monkeypatch.setattr(dynamics_sync, "dynamics", fake)
    facts, _ = funding_facts.clean(pick(("segment", "Startup", "Orbit Mobility is a Series B startup", "")), DOC, verified)
    result = dynamics_sync.sync(intake(funding=facts), "Daniel <daniel@ingram.example>")
    assert result["action"] == "created" and result["id"].startswith("1111")
    fields, answers = fake.created[0]
    assert fields["customer"] == "Orbit Mobility" and answers["fund_segment"] == "Startup"


def test_sync_reuses_an_existing_dynamics_opportunity(monkeypatch):
    fake = FakeDynamics([{"id": "22222222-2222-4222-8222-222222222222", "name": "Orbit Mobility - fleet", "customer": "Orbit Mobility"}])
    monkeypatch.setattr(dynamics_sync, "dynamics", fake)
    assert dynamics_sync.sync(intake()) == {"action": "existing", "id": "22222222-2222-4222-8222-222222222222"}
    assert fake.created == []   # a second scan of the same email does not duplicate it


def test_sync_never_raises_and_can_be_turned_off(monkeypatch):
    class Broken(FakeDynamics):
        def create_opportunity(self, fields, answers):
            raise self.DynamicsError("Dynamics is down")
    broken = Broken()
    monkeypatch.setattr(dynamics_sync, "dynamics", broken)
    monkeypatch.setattr(dynamics_sync.dynamics, "DynamicsError", Broken.DynamicsError, raising=False)
    assert dynamics_sync.sync(intake())["action"] == "failed"
    monkeypatch.setenv("INTAKE_DYNAMICS_SYNC", "false")
    assert dynamics_sync.sync(intake())["action"] == "skipped"
    monkeypatch.delenv("INTAKE_DYNAMICS_SYNC")
    assert dynamics_sync.sync({"fields": [], "funding": []})["action"] == "skipped"   # no customer named


def test_extraction_retries_with_a_bigger_budget_when_the_output_is_cut_off(monkeypatch):
    from strands.types.exceptions import MaxTokensReachedException
    from intake import extractor
    budgets = []

    def fake_call(bedrock, model_id, system, text, tool, schema, max_tokens):
        budgets.append(max_tokens)
        if max_tokens < 32_000:
            raise MaxTokensReachedException("cut off")
        return {"is_opportunity": True, "summary": "s", "confidence": 0.9, "fields": [], "gaps": [], "programme_fit": {}}

    monkeypatch.setattr(extractor, "_call", fake_call)
    result, _ = extractor.extract([{"source": "email", "doc": {"subject": "Re: long thread", "body": "x"}}], None, "m")
    assert budgets == [16_000, 32_000] and result["is_opportunity"] and result["funding"] == []
