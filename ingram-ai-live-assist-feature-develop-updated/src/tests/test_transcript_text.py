from datetime import datetime, timezone

import pytest

from app.conversation_import import compute_delays_seconds, parse_conversation
from app.transcript_text import (
    MAX_TRANSCRIPT_CHARS,
    MAX_TURN_CHARS,
    InvalidTranscriptTextError,
    transcript_to_conversation,
)

START = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)
BA = ["Ravi Menon"]


def convert(text, ba=BA):
    data = transcript_to_conversation(text, ba, start=START)
    parse_conversation(data)  # The replayer must accept the result.
    return data["conversation"]


def test_plain_text_roles_names_and_wrapped_lines():
    turns = convert(
        "Ravi Menon (Partner): Tell me about your project.\n"
        "Helen Brooks: We run on-prem today,\nand want to move to AWS.\n"
    )
    assert [(t["speaker"], t["name"]) for t in turns] == [("ba", "Ravi Menon (Partner)"), ("cx", "Helen Brooks")]
    assert turns[1]["text"] == "We run on-prem today, and want to move to AWS."


def test_times_in_the_text_are_used():
    turns = convert("[00:05] Ravi Menon: Hi.\n[01:20] Helen: Hello.")
    assert turns[0]["timestamp"] == "2026-10-23T10:00:05Z"
    assert turns[1]["timestamp"] == "2026-10-23T10:01:20Z"


def test_missing_times_are_estimated_from_the_amount_of_speech():
    turns = convert("Helen: " + "word " * 50 + "\nRavi Menon: ok")
    gap = compute_delays_seconds(parse_conversation({"conversation": turns}))[1]
    assert 15 < gap < 25  # about 50 words at 150 a minute


def test_webvtt_voice_tags_and_same_speaker_cues_are_merged():
    turns = convert(
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Ravi Menon>Thanks for joining.</v>\n\n"
        "2\n00:00:05.000 --> 00:00:09.000\n<v Helen Brooks>We have 60 VMs.</v>\n\n"
        "3\n00:00:09.500 --> 00:00:12.000\n<v Helen Brooks>And a December deadline.</v>\n"
    )
    assert [(t["speaker"], t["text"]) for t in turns] == [
        ("ba", "Thanks for joining."), ("cx", "We have 60 VMs. And a December deadline.")]
    assert turns[0]["timestamp"] == "2026-10-23T10:00:01Z"


def test_srt_with_name_prefix():
    turns = convert("1\n00:00:01,000 --> 00:00:03,000\nRavi Menon: Hello there.\n\n"
                    "2\n00:00:04,000 --> 00:00:06,000\nHelen: Hi Ravi.\n")
    assert [t["speaker"] for t in turns] == ["ba", "cx"]


def test_text_without_speakers_is_the_customer():
    turns = convert("We need a migration.\nBudget is open.")
    assert [(t["speaker"], t["name"]) for t in turns] == [("cx", "Customer")]


def test_a_long_paragraph_is_split_on_sentence_ends():
    turns = convert("Helen: " + " ".join(f"Sentence number {i} is here." for i in range(120)))
    assert len(turns) > 1 and all(len(t["text"]) <= MAX_TURN_CHARS for t in turns)
    assert "Sentence number 0 is here." in turns[0]["text"] and "Sentence number 119 is here." in turns[-1]["text"]


def test_conversation_json_passes_through():
    data = {"conversation": [{"timestamp": "2026-10-08T10:00:00Z", "speaker": "ba", "text": "Hello."}]}
    import json
    assert transcript_to_conversation(json.dumps(data), []) == data


@pytest.mark.parametrize("text", [None, "", "   \n ", 5, "x" * (MAX_TRANSCRIPT_CHARS + 1), '{"nope": 1}', "WEBVTT\n\n"])
def test_unusable_text_is_refused_with_a_message(text):
    with pytest.raises(InvalidTranscriptTextError) as e:
        transcript_to_conversation(text, [])
    assert str(e.value)
