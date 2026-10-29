"""Meeting link parsing: Google Meet, Microsoft Teams and Zoom.

Turns the link a user pastes into what the transcript service needs (platform, meeting id,
passcode). Pure and strict: only https links on known hosts are accepted, so the value that
reaches the service is never an arbitrary URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

GOOGLE_MEET = "google_meet"
TEAMS = "teams"
ZOOM = "zoom"
PLATFORMS = (GOOGLE_MEET, TEAMS, ZOOM)

MAX_LINK_CHARS = 2048

_MEET_PATH = re.compile(r"^/(?:meet/)?([a-z]{3}-[a-z]{4}-[a-z]{3})/?$")
_TEAMS_PATH = re.compile(r"^/meet/(\d{10,15})/?$")
_TEAMS_PASSCODE = re.compile(r"^[A-Za-z0-9]{6,20}$")
_ZOOM_PATH = re.compile(r"^/(?:j|wc/join)/(\d{9,11})/?$")
_TEAMS_HOSTS = {"teams.live.com", "teams.microsoft.com"}


class InvalidMeetingLinkError(ValueError):
    """The link is not a supported meeting link. The message says why, for the user."""


@dataclass(frozen=True)
class MeetingLink:
    platform: str  # One of PLATFORMS.
    native_id: str  # The meeting id inside the link, not the whole URL.
    passcode: str | None  # Teams passcode; None for the others.
    url: str  # Normalized https URL. Zoom links keep their query, which can carry the passcode.


def parse_meeting_link(raw: object) -> MeetingLink:
    """Return the meeting a link points to.

    Raises:
        InvalidMeetingLinkError: The link is empty, not https, on an unknown host, or has no
            meeting id.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidMeetingLinkError("Paste the meeting link.")
    text = raw.strip()
    if len(text) > MAX_LINK_CHARS or any(ord(c) < 32 for c in text):
        raise InvalidMeetingLinkError("That does not look like a meeting link.")
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlsplit(text)
        host = (url.hostname or "").lower()
    except ValueError:
        raise InvalidMeetingLinkError("That does not look like a meeting link.") from None
    if url.scheme != "https" or not host or url.username or url.password:
        raise InvalidMeetingLinkError("The meeting link must be a plain https link.")

    if host == "meet.google.com":
        match = _MEET_PATH.match(url.path)
        if not match:
            raise InvalidMeetingLinkError("Google Meet links look like https://meet.google.com/abc-defg-hij.")
        return MeetingLink(GOOGLE_MEET, match.group(1), None, f"https://meet.google.com/{match.group(1)}")

    if host in _TEAMS_HOSTS:
        match = _TEAMS_PATH.match(url.path)
        if not match:
            raise InvalidMeetingLinkError(
                "Use the short Teams link (…/meet/<number>?p=<passcode>). Long 'meetup-join' links are not supported."
            )
        passcode = parse_qs(url.query).get("p", [None])[0]
        if passcode is not None and not _TEAMS_PASSCODE.match(passcode):
            raise InvalidMeetingLinkError("The Teams passcode in the link is not valid.")
        clean = f"https://{host}/meet/{match.group(1)}" + (f"?p={passcode}" if passcode else "")
        return MeetingLink(TEAMS, match.group(1), passcode, clean)

    if host == "zoom.us" or host.endswith((".zoom.us", ".zoom.com")) or host == "zoom.com":
        match = _ZOOM_PATH.match(url.path)
        if not match:
            raise InvalidMeetingLinkError("Zoom links look like https://zoom.us/j/<meeting number>.")
        return MeetingLink(ZOOM, match.group(1), None, url._replace(fragment="").geturl())

    raise InvalidMeetingLinkError("Only Google Meet, Microsoft Teams and Zoom links are supported.")
