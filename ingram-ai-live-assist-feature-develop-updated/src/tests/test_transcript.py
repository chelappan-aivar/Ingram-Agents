import pytest

from app.transcript import InvalidTranscriptChunkError, TranscriptChunk


def test_valid_chunk_parses():
    chunk = TranscriptChunk.from_payload({"speaker": "cx", "text": "  hello there  "})
    assert chunk.speaker == "cx"
    assert chunk.text == "hello there"


@pytest.mark.parametrize(
    "payload",
    [
        {"speaker": "unknown", "text": "hi"},
        {"speaker": "cx", "text": ""},
        {"speaker": "cx", "text": "   "},
        {"speaker": "cx"},
        {"text": "hi"},
        {},
    ],
)
def test_invalid_chunk_rejected(payload):
    with pytest.raises(InvalidTranscriptChunkError):
        TranscriptChunk.from_payload(payload)


def test_parse_chunks_accepts_object_or_list():
    from app.transcript import parse_chunks

    assert [c.text for c in parse_chunks({"speaker": "cx", "text": "a"})] == ["a"]
    batch = parse_chunks([{"speaker": "ba", "text": "q?"}, {"speaker": "cx", "text": "a"}])
    assert [(c.speaker, c.text) for c in batch] == [("ba", "q?"), ("cx", "a")]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "nope",
        [{"speaker": "cx", "text": "ok"}, "bad"],
        [{"speaker": "cx", "text": "ok"}, {"speaker": "nobody", "text": "x"}],
        [{"speaker": "cx", "text": "x"}] * 21,
    ],
)
def test_parse_chunks_rejects_bad_batches(payload):
    from app.transcript import parse_chunks

    with pytest.raises(InvalidTranscriptChunkError):
        parse_chunks(payload)


def test_chunk_keeps_name_and_normalises_timestamp():
    chunk = TranscriptChunk.from_payload({
        "speaker": "cx", "text": "We want to migrate.",
        "name": "Alice\n(Customer) | [x]", "timestamp": "2026-10-08T15:30:18+05:30",
    })

    assert chunk.timestamp == "2026-10-08T10:00:18Z"
    assert chunk.name == "Alice (Customer) x"
    assert chunk.format_line() == "[2026-10-08T10:00:18Z | cx | Alice (Customer) x]: We want to migrate."


def test_chunk_without_name_or_timestamp_keeps_short_format():
    chunk = TranscriptChunk.from_payload({"speaker": "ba", "text": "Hi"})
    assert chunk.format_line() == "[ba]: Hi"


@pytest.mark.parametrize("extra", [{"timestamp": "yesterday"}, {"timestamp": 5}, {"name": 5}])
def test_chunk_rejects_bad_name_or_timestamp(extra):
    with pytest.raises(InvalidTranscriptChunkError):
        TranscriptChunk.from_payload({"speaker": "cx", "text": "hi", **extra})
