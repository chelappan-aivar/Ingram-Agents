import pytest

from app.meeting_link import (
    GOOGLE_MEET,
    TEAMS,
    ZOOM,
    InvalidMeetingLinkError,
    parse_meeting_link,
)


@pytest.mark.parametrize("raw, platform, native_id, passcode, url", [
    ("https://meet.google.com/abc-defg-hij", GOOGLE_MEET, "abc-defg-hij", None, "https://meet.google.com/abc-defg-hij"),
    ("https://meet.google.com/meet/abc-defg-hij", GOOGLE_MEET, "abc-defg-hij", None, "https://meet.google.com/abc-defg-hij"),
    ("meet.google.com/abc-defg-hij?authuser=1", GOOGLE_MEET, "abc-defg-hij", None, "https://meet.google.com/abc-defg-hij"),
    ("  https://MEET.google.com/abc-defg-hij/  ", GOOGLE_MEET, "abc-defg-hij", None, "https://meet.google.com/abc-defg-hij"),
    ("https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34", TEAMS, "123456789012", "AbCdEf12GhIj34",
     "https://teams.microsoft.com/meet/123456789012?p=AbCdEf12GhIj34"),
    ("https://teams.live.com/meet/9876543210123", TEAMS, "9876543210123", None, "https://teams.live.com/meet/9876543210123"),
    ("https://us02web.zoom.us/j/81234567890?pwd=abc123", ZOOM, "81234567890", None,
     "https://us02web.zoom.us/j/81234567890?pwd=abc123"),
    ("https://acme.zoom.com/wc/join/81234567890", ZOOM, "81234567890", None, "https://acme.zoom.com/wc/join/81234567890"),
])
def test_valid_links(raw, platform, native_id, passcode, url):
    link = parse_meeting_link(raw)
    assert (link.platform, link.native_id, link.passcode, link.url) == (platform, native_id, passcode, url)


@pytest.mark.parametrize("raw", [
    None, "", "   ", 42,
    "http://meet.google.com/abc-defg-hij",  # not https
    "https://meet.google.com/lookup/abc-defg-hij",
    "https://meet.google.com/landing",
    "https://meet.google.com.evil.com/abc-defg-hij",  # host that only starts with a known one
    "https://evil.com/meet.google.com/abc-defg-hij",
    "https://user:pw@meet.google.com/abc-defg-hij",  # credentials in the URL
    "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x/0",  # long Teams link
    "https://teams.microsoft.com/meet/123",  # id too short
    "https://teams.microsoft.com/meet/123456789012?p=bad passcode!",
    "https://zoom.us/my/someone",
    "https://notzoom.us/j/81234567890",
    "https://example.com/",
    "javascript:alert(1)",
    "https://meet.google.com/abc-defg-hij\nHost: evil",
    "https://meet.google.com/" + "a" * 3000,
])
def test_invalid_links_are_refused_with_a_message(raw):
    with pytest.raises(InvalidMeetingLinkError) as e:
        parse_meeting_link(raw)
    assert str(e.value)
