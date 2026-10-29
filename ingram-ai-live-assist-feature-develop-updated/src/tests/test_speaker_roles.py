import pytest

from app.speaker_roles import MAX_BA_NAMES, RoleResolver, parse_name_list


@pytest.mark.parametrize("value, expected", [
    (None, []),
    ("", []),
    ("Ravi Menon", ["Ravi Menon"]),
    ("Ravi Menon, Asha Rao;  Tom\nLee", ["Ravi Menon", "Asha Rao", "Tom", "Lee"]),
    ("Ravi Menon, ravi menon", ["Ravi Menon"]),  # duplicates by case
    (["A B", " ", "C D"], ["A B", "C D"]),
    ("Ravi [Partner]|x", ["Ravi Partner x"]),  # characters that could forge a prompt header are stripped
])
def test_parse_name_list(value, expected):
    assert parse_name_list(value) == expected


def test_parse_name_list_limits():
    with pytest.raises(ValueError):
        parse_name_list([f"Name{i} Last" for i in range(MAX_BA_NAMES + 1)])
    for bad in ([1, 2], 5, {"a": 1}, True):
        with pytest.raises(ValueError):
            parse_name_list(bad)


@pytest.mark.parametrize("ba, speaker, role", [
    (["Ravi Menon"], "Ravi Menon", "ba"),
    (["Ravi Menon"], "ravi menon", "ba"),
    (["Ravi Menon"], "Ravi Menon (Partner)", "ba"),  # extra words in the meeting's display name
    (["Ravi Menon (Partner)"], "Ravi Menon", "ba"),
    (["Ravi Menon"], "Menon Ravi", "ba"),  # word order does not matter
    (["Ravi Menon"], "Helen Brooks", "cx"),
    (["Ravi"], "Ravi", "ba"),  # a single word matches itself ...
    (["Ravi"], "Ravi Kumar", "cx"),  # ... but does not claim a customer who shares the first name
    (["Ravi Menon", "Asha Rao"], "Asha Rao", "ba"),
    ([], "Anyone", "cx"),
    (["Ravi Menon"], "", "cx"),  # Vexa could not name the speaker
    (["Ravi Menon"], None, "cx"),
    (["Ravi Menon"], "(Guest)", "cx"),
])
def test_role_for(ba, speaker, role):
    assert RoleResolver(ba).role_for(speaker) == role
