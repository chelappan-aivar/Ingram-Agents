import asyncio

import pytest

from app.meeting_link import parse_meeting_link
from app.vexa_client import (
    TranscriptStream,
    Utterance,
    VexaError,
    VexaSettings,
    check_read_access,
    request_bot,
    stop_bot,
)
from tests.fake_vexa import BOT_KEY, BOTH_KEY, TX_KEY, FakeVexa, segment, status, transcript

TEAMS = parse_meeting_link("https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34")
MEET = parse_meeting_link("https://meet.google.com/abc-defg-hij")
ZOOM = parse_meeting_link("https://us02web.zoom.us/j/81234567890?pwd=abc123")


@pytest.fixture
def vexa():
    fake = FakeVexa()
    fake.start()
    yield fake
    fake.stop()


def settings(vexa, **kw) -> VexaSettings:
    defaults = {"api_key": BOTH_KEY, "api_base": vexa.base, "promote_after_s": 0.0}
    return VexaSettings(**{**defaults, **kw})


async def collect(vexa, *, seconds=1.5, **kw) -> tuple[list[Utterance], list[str]]:
    """Run a stream against the fake for a while and return what it delivered."""
    got, statuses = [], []

    async def on_utterance(u):
        got.append(u)

    async def on_status(s):
        statuses.append(s)

    stream = TranscriptStream(settings(vexa, **kw), MEET, on_utterance, on_status)
    task = asyncio.create_task(stream.run())
    await asyncio.sleep(seconds)
    stream.stop()
    await asyncio.wait_for(task, timeout=5)
    return got, statuses


# --- settings ----------------------------------------------------------------------------


def test_settings_from_env():
    s = VexaSettings.from_env({"VEXA_API_KEY": "from-env", "VEXA_TX_KEY": "from-env", "VEXA_PROMOTE_AFTER_SECONDS": "3"})
    # Keys come from the console only; the environment holds the address and timing.
    assert (s.api_key, s.tx_key, s.api_base, s.promote_after_s, s.enabled) == ("", "", "https://api.cloud.vexa.ai", 3.0, False)
    assert s.stream_url == "wss://api.cloud.vexa.ai/ws"
    keyed = VexaSettings(api_key="k", tx_key="t")
    assert keyed.read_headers == {"X-API-Key": "t"} and keyed.bot_headers == {"X-API-Key": "k"}
    local = VexaSettings.from_env({"VEXA_API_BASE": "http://localhost:18056/"})
    assert local.stream_url == "ws://localhost:18056/ws" and not local.enabled
    assert VexaSettings(api_key="k").read_headers == {"X-API-Key": "k"}  # No separate key: the same one reads


@pytest.mark.parametrize("env", [{"VEXA_API_BASE": "http://evil.example.com"}, {"VEXA_PROMOTE_AFTER_SECONDS": "soon"}])
def test_settings_reject_unsafe_or_malformed_values(env):
    with pytest.raises(ValueError):
        VexaSettings.from_env(env)


def test_the_api_key_is_not_printed():
    assert "secret-key" not in repr(VexaSettings(api_key="secret-key", tx_key="secret-tx"))


# --- REST --------------------------------------------------------------------------------


async def test_request_bot_bodies_per_platform(vexa):
    await request_bot(settings(vexa), TEAMS, "Assist")
    await request_bot(settings(vexa), MEET, "Assist")
    await request_bot(settings(vexa), ZOOM, "Assist")
    teams, meet, zoom = vexa.bot_requests
    assert teams == {"bot_name": "Assist", "transcribe_enabled": True, "platform": "teams",
                     "native_meeting_id": "123456789012", "passcode": "AbCdEf12GhIj34"}
    assert meet == {"bot_name": "Assist", "transcribe_enabled": True, "platform": "google_meet",
                    "native_meeting_id": "abc-defg-hij"}
    assert zoom == {"bot_name": "Assist", "transcribe_enabled": True, "meeting_url": ZOOM.url}


@pytest.mark.parametrize("code", [409, 429, 503])
async def test_request_bot_failures_carry_the_status(vexa, code):
    vexa.bot_response = (code, {"detail": "nope"})
    with pytest.raises(VexaError) as e:
        await request_bot(settings(vexa), MEET, "Assist")
    assert e.value.status == code and e.value.detail == "nope"


async def test_unreachable_service_is_a_503():
    with pytest.raises(VexaError) as e:
        await request_bot(VexaSettings(api_key="k", api_base="http://127.0.0.1:9"), MEET, "Assist")
    assert e.value.status == 503


async def test_check_read_access_catches_a_key_without_transcript_scope(vexa):
    await check_read_access(settings(vexa), MEET)  # both scopes
    await check_read_access(settings(vexa, api_key=BOT_KEY, tx_key=TX_KEY), MEET)  # separate read key
    with pytest.raises(VexaError) as e:
        await check_read_access(settings(vexa, api_key=BOT_KEY), MEET)  # a bot-only key reads nothing
    assert e.value.status == 403 and "tx" in e.value.detail
    vexa.transcript_status = 404  # the meeting does not exist yet: fine
    await check_read_access(settings(vexa), MEET)


async def test_stop_bot_treats_gone_as_done(vexa):
    await stop_bot(settings(vexa), TEAMS)
    assert vexa.stop_requests == ["teams/123456789012"]


# --- live transcript ---------------------------------------------------------------------


async def test_subscribes_with_the_read_key_and_delivers_only_confirmed_sentences_once(vexa):
    vexa.script = [
        transcript(pending=[segment("s1", "We run on")]),  # draft: ignored
        transcript(confirmed=[segment("s1", "We run on-prem today.", completed=True)]),
        transcript(confirmed=[segment("s1", "We run on-prem today.", completed=True)]),  # duplicate
        transcript(confirmed=[segment("s2", "   ", completed=True)]),  # empty
        transcript(confirmed=[segment("s3", "And we want AWS.", speaker="", completed=True)]),
        status("active"),
    ]
    got, statuses = await collect(vexa, api_key=BOT_KEY, tx_key=TX_KEY)
    assert [(u.segment_id, u.speaker, u.text, u.promoted) for u in got] == [
        ("s1", "Helen Brooks", "We run on-prem today.", False), ("s3", "", "And we want AWS.", False)]
    assert statuses == ["active"]
    assert vexa.subscriptions[0] == {"action": "subscribe", "meetings": [{"platform": "google_meet", "native_id": "abc-defg-hij"}]}
    assert vexa.ws_keys == [TX_KEY]  # The bot-only key is never used to read


async def test_a_stable_draft_is_delivered_early_and_the_later_confirmation_is_ignored(vexa):
    vexa.script = [
        transcript(pending=[segment("s1", "We have sixty virtual machines")]),
        ("sleep", 0.8),
        transcript(confirmed=[segment("s1", "We have sixty virtual machines.", completed=True)]),
    ]
    got, _ = await collect(vexa, seconds=2.0, promote_after_s=0.3)
    assert [(u.segment_id, u.text, u.promoted) for u in got] == [("s1", "We have sixty virtual machines", True)]


async def test_a_draft_that_keeps_changing_is_not_promoted_until_confirmed(vexa):
    vexa.script = [
        transcript(pending=[segment("s1", "We have sixty")]),
        ("sleep", 0.2),
        transcript(pending=[segment("s1", "We have sixty virtual")]),
        ("sleep", 0.2),
        transcript(pending=[segment("s1", "We have sixty virtual machines")]),
        ("sleep", 0.2),
        transcript(confirmed=[segment("s1", "We have sixty virtual machines.", completed=True)]),
    ]
    got, _ = await collect(vexa, seconds=1.8, promote_after_s=0.5)
    assert [(u.text, u.promoted) for u in got] == [("We have sixty virtual machines.", False)]


async def test_short_drafts_are_not_promoted(vexa):
    vexa.script = [transcript(pending=[segment("s1", "Hello there")])]  # two words
    got, _ = await collect(vexa, seconds=1.2, promote_after_s=0.2)
    assert got == []


async def test_a_retracted_draft_is_never_delivered(vexa):
    vexa.script = [
        transcript(pending=[segment("s1", "This was background chatter")]),
        {"type": "transcript_retract", "meeting": {"id": 1}, "segment_ids": ["s1"]},
    ]
    got, _ = await collect(vexa, seconds=1.2, promote_after_s=0.3)
    assert got == []


async def test_a_draft_missing_from_the_speakers_next_frame_is_dropped(vexa):
    vexa.script = [
        transcript(pending=[segment("s1", "Let me think about that")]),
        transcript(pending=[]),  # the speaker's drafts are gone
    ]
    got, _ = await collect(vexa, seconds=1.2, promote_after_s=0.3)
    assert got == []


async def test_older_frame_shape_with_a_completed_flag(vexa):
    vexa.script = [
        {"type": "transcript.mutable", "payload": {"segments": [
            segment("o1", "Draft only", completed=False), segment("o2", "Final one.", completed=True)]}},
    ]
    got, _ = await collect(vexa)
    assert [u.segment_id for u in got] == ["o2"]


async def test_the_stream_ends_when_the_meeting_does(vexa):
    vexa.script = [transcript(confirmed=[segment("s1", "Bye everyone.", completed=True)]), status("completed")]
    stream = TranscriptStream(settings(vexa), MEET, lambda u: asyncio.sleep(0))
    await asyncio.wait_for(stream.run(), timeout=5)  # returns on its own


async def test_reconnects_after_the_connection_drops_without_losing_or_repeating_lines(vexa):
    vexa.drop_first_connection = True
    vexa.script = [transcript(confirmed=[segment("s1", "After the drop.", completed=True)])]
    got, _ = await collect(vexa, seconds=3.5)
    assert vexa.connections >= 2
    assert [u.text for u in got] == ["After the drop."]


async def test_a_consumer_that_is_slow_does_not_break_ordering(vexa):
    vexa.script = [transcript(confirmed=[segment(f"s{i}", f"Line number {i} here.", completed=True)]) for i in range(5)]
    got = []

    async def slow(u):
        await asyncio.sleep(0.05)
        got.append(u.segment_id)

    stream = TranscriptStream(settings(vexa), MEET, slow)
    task = asyncio.create_task(stream.run())
    await asyncio.sleep(1.5)
    stream.stop()
    await asyncio.wait_for(task, timeout=5)
    assert got == [f"s{i}" for i in range(5)]
