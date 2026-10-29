"""The registry must stay identical to the cost-calculator schema in usage_estimator/expectedOutput.md."""
from __future__ import annotations

import json
import re
from pathlib import Path

from usage_estimator import registry

EXPECTED_MD = Path(registry.__file__).parent / "expectedOutput.md"
TYPES = {bool: "boolean", int: "integer", float: "number", str: "string", list: "list"}


def expected() -> dict:
    text = EXPECTED_MD.read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))["services"]


def test_groups_and_services_match_the_schema_in_order():
    exp = expected()
    assert list(exp) == registry.GROUPS
    for group in registry.GROUPS:
        assert [e["service"] for e in exp[group]] == [s.name for s in registry.SERVICES if s.group == group]


def test_every_field_name_order_and_type_match():
    for group, entries in expected().items():
        for e in entries:
            svc = registry.BY_NAME[e["service"]]
            assert [f.name for f in svc.fields] == [k for k in e if k != "service"], svc.name
            for f in svc.fields:
                got = TYPES[type(e[f.name])]
                # the sample writes whole numbers (2, 720) for fields that may hold fractions
                assert got == f.type or (f.type == "number" and got == "integer"), (svc.name, f.name)


def test_keys_are_unique_and_rules_name_real_fields():
    assert len(registry.BY_KEY) == len(registry.SERVICES) == 35
    names = {f.name for s in registry.SERVICES for f in s.fields}
    assert set(registry.AZ_FIELDS) <= names and registry.DERIVED <= names
