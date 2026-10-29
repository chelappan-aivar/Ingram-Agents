import pytest

from app.conversation_import import (
    InvalidConversationFileError,
    compute_delays_seconds,
    parse_conversation,
)


def test_parse_valid_conversation():
    data = {
        "conversation": [
            {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "name": "John", "text": "Hi there"},
            {"timestamp": "2026-10-08T10:00:18Z", "speaker": "cx", "name": "Alice", "text": "Hello"},
        ]
    }
    turns = parse_conversation(data)
    assert len(turns) == 2
    assert turns[0].speaker == "ba"
    assert turns[0].name == "John"
    assert turns[0].text == "Hi there"
    assert turns[1].speaker == "cx"


def test_parse_sorts_out_of_order_turns():
    data = {
        "conversation": [
            {"timestamp": "2026-10-08T10:00:30Z", "speaker": "cx", "text": "second"},
            {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "text": "first"},
        ]
    }
    turns = parse_conversation(data)
    assert [t.text for t in turns] == ["first", "second"]


def test_name_falls_back_to_default_label():
    data = {"conversation": [{"timestamp": "2026-10-08T10:00:00Z", "speaker": "cx", "text": "hi"}]}
    turns = parse_conversation(data)
    assert turns[0].name == "Customer"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"conversation": []},
        {"conversation": "not a list"},
        {"conversation": [{"speaker": "cx", "text": "hi"}]},  # missing timestamp
        {"conversation": [{"timestamp": "bad", "speaker": "cx", "text": "hi"}]},
        {"conversation": [{"timestamp": "2026-10-08T10:00:00Z", "speaker": "nobody", "text": "hi"}]},
        {"conversation": [{"timestamp": "2026-10-08T10:00:00Z", "speaker": "cx", "text": ""}]},
        {"conversation": [{"timestamp": "2026-10-08T10:00:00Z", "speaker": "cx"}]},
        {"conversation": ["not an object"]},
    ],
)
def test_invalid_conversation_rejected(data):
    with pytest.raises(InvalidConversationFileError):
        parse_conversation(data)


def test_compute_delays_seconds():
    data = {
        "conversation": [
            {"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "text": "a"},
            {"timestamp": "2026-10-08T10:00:05Z", "speaker": "cx", "text": "b"},
            {"timestamp": "2026-10-08T10:00:20Z", "speaker": "ba", "text": "c"},
        ]
    }
    turns = parse_conversation(data)
    delays = compute_delays_seconds(turns)
    assert delays == [0.0, 5.0, 15.0]


def test_compute_delays_seconds_empty():
    assert compute_delays_seconds([]) == []


def test_mixed_timezone_offsets_still_compute_correct_delay():
    # 10:00:00 UTC+2 == 08:00:00 UTC; 08:00:10 UTC (no offset, treated as UTC)
    # should be a 10 second delay despite the differing representations.
    data = {
        "conversation": [
            {"timestamp": "2026-10-08T10:00:00+02:00", "speaker": "ba", "text": "a"},
            {"timestamp": "2026-10-08T08:00:10Z", "speaker": "cx", "text": "b"},
        ]
    }
    turns = parse_conversation(data)
    delays = compute_delays_seconds(turns)
    assert delays == [0.0, 10.0]
