"""The funding facts an email can state, in the labels the intake form (and so the funding check) uses.

The funding check reads a Dynamics opportunity's fund_* answers (agent/funding/facts.py). An email never reaches it, so
the extraction also records these facts, each with the passage that states it, and dynamics_sync.py writes the verified
ones into a Dynamics opportunity. Values must be exactly the form's option labels; anything else is dropped.
"""
from typing import Any, Callable

# fact -> (form answer id, allowed values)
CHOICES: dict[str, tuple[str, list[str]]] = {
    "segment": ("fund_segment", ["Startup", "Non-startup (enterprise and others)"]),
    "workload": ("fund_workload", ["Migration", "Generative AI", "ISV workload migration", "SAP migration",
                                   "Oracle migration", "Other"]),
    "migration_source": ("fund_migration_source", ["On-premises", "Another cloud", "Colocation"]),
    "vms": ("fund_vms", ["Yes", "No"]),
    "genai_stage": ("fund_genai_stage", ["Proof of concept", "Production-ready"]),
    "genai_type": ("fund_genai_type", ["Fresh workload", "Modernization or optimization"]),
}
NUMBERS: dict[str, str] = {"mrr_usd": "fund_mrr_usd", "arr_usd": "fund_arr_usd"}
FACTS = list(CHOICES) + list(NUMBERS)
MAX_AMOUNT = 1e12

PROMPT_SECTION = """\
4. Funding facts: the AWS funding check needs these facts. Record each one the document STATES, in "funding", with the
   exact passage that states it (same rules as the fields: word for word, one sentence). The passage itself must say it:
   never infer a fact from the industry, the country or the customer's name, and never compute one. A fact the document
   does not state is left out; the funding check then asks for it instead of assuming it.
   - segment: "Startup" only when the passage says startup, seed or Series funding, or similar early-stage wording;
     "Non-startup (enterprise and others)" only when it says the company is established or large (employee count in the
     thousands, "established", "not a startup").
   - workload: "Migration", "Generative AI", "ISV workload migration", "SAP migration", "Oracle migration" or "Other".
   - migration_source (migrations only): "On-premises", "Another cloud" (Azure, Google Cloud) or "Colocation".
   - vms: "Yes" when the passage mentions virtual machines, VMs or servers in scope; "No" only when it says there are none.
   - genai_stage (Gen AI only): "Proof of concept" or "Production-ready".
   - genai_type (Gen AI only): "Fresh workload" (a new workload) or "Modernization or optimization" (improving an
     existing one), only when the passage says which.
   - mrr_usd: expected AWS spend per month, only when the document gives a monthly figure. arr_usd: expected AWS spend per
     year, only when the document gives a yearly figure. A plain number; set currency to the ISO code the document uses
     (USD, EUR, ...). Do not convert currencies and do not turn one figure into the other."""

SCHEMA_ITEM: dict[str, Any] = {"type": "object", "properties": {
    "fact": {"type": "string", "enum": FACTS}, "value": {"type": "string"}, "source": {"type": "string"},
    "currency": {"type": "string"}}, "required": ["fact", "value", "source"]}


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace(" ", ""))
    except ValueError:
        return None
    return number if 0 < number <= MAX_AMOUNT else None


def clean(raw: list[dict[str, Any]] | None, normalised_doc: str,
          verified: Callable[[str, str], bool]) -> tuple[list[dict[str, Any]], list[str]]:
    """Model output -> (facts safe to write, notes for amounts that cannot be used).

    A fact is kept once, with a value from the form's labels (numbers: a positive USD amount) and a passage found word
    for word in the document. An amount in another currency is not converted (no invented rates); it is returned as a
    note so the opportunity description still carries it.
    """
    facts: list[dict[str, Any]] = []
    notes: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        fact, value = item.get("fact"), str(item.get("value", "")).strip()
        source = str(item.get("source", "")).strip()
        if fact not in FACTS or fact in seen or not verified(source, normalised_doc):
            continue
        if fact in NUMBERS:
            number = _number(value)
            if number is None:
                continue
            currency = str(item.get("currency", "")).strip().upper()
            if currency != "USD":
                notes.append(f"{fact.split('_')[0].upper()} stated as {value} {currency or 'with no currency'}: \"{source}\"")
                continue
            facts.append({"fact": fact, "value": int(number) if number == int(number) else number, "source": source})
        elif value in CHOICES[fact][1]:
            facts.append({"fact": fact, "value": value, "source": source})
        else:
            continue
        seen.add(fact)
    return facts, notes


def answers(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Verified facts -> form answers {fund_segment: "Startup", fund_mrr_usd: 30000, ...}."""
    out: dict[str, Any] = {}
    for f in facts:
        form_id = CHOICES[f["fact"]][0] if f["fact"] in CHOICES else NUMBERS[f["fact"]]
        out[form_id] = f["value"]
    return out
