"""Who is the partner BA and who is the customer.

The agent needs every line labeled ``ba`` or ``cx``. Meeting platforms only give display
names, so the person running the assist tells us their own name(s) and everyone else is the
customer. Pure, so it is shared by the live-meeting source and the transcript import.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.transcript import MAX_NAME_LENGTH, SPEAKER_BA, SPEAKER_CX, parse_name

MAX_BA_NAMES = 10

_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)


def parse_name_list(value: object) -> list[str]:
    """Names from a list or from comma, semicolon or newline separated text.

    Raises:
        ValueError: There are too many names or one is not text.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;\n]", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError("names must be a list or text")
    names: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            raise ValueError("names must be text")
        name = parse_name(part)  # One line, no characters that could forge a prompt header.
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name[:MAX_NAME_LENGTH])
    if len(names) > MAX_BA_NAMES:
        raise ValueError(f"give at most {MAX_BA_NAMES} names")
    return names


def _tokens(name: str) -> frozenset[str]:
    cleaned = _NOT_WORD.sub(" ", _PARENTHETICAL.sub(" ", name).casefold())
    return frozenset(cleaned.split())


class RoleResolver:
    """Maps a speaker's display name to ``ba`` or ``cx``.

    A name matches when its words equal one of the partner's names, or when one contains all
    the words of the other and has at least two words ("Ravi Menon (Partner)" matches "Ravi Menon").
    A single word only matches itself, so "Ravi" does not claim a customer called "Ravi Kumar".
    Speakers without a name are customers.
    """

    def __init__(self, ba_names: Iterable[str]) -> None:
        self._ba = [t for t in (_tokens(n) for n in ba_names) if t]

    def role_for(self, speaker_name: str | None) -> str:
        spoken = _tokens(speaker_name or "")
        if not spoken:
            return SPEAKER_CX
        for ba in self._ba:
            if spoken == ba:
                return SPEAKER_BA
            smaller, larger = (ba, spoken) if len(ba) <= len(spoken) else (spoken, ba)
            if len(smaller) >= 2 and smaller <= larger:
                return SPEAKER_BA
        return SPEAKER_CX
