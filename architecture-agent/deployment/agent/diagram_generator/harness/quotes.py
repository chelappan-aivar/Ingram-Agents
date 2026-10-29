"""Quote verification for the harnesses.

quote_found(quote, text) is True when the quote appears in the text after normalising both sides:
case, whitespace (including newlines from PDF extraction), curly quotes/apostrophes, dash variants,
bullets, soft hyphens, and hyphenation line-break artefacts. A quote containing "..." (or the
ellipsis character) is split, and every part must appear in order.
"""
from __future__ import annotations

import re
import unicodedata

_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
}
_DASHES = "‐‑‒–—―−﹘﹣－"
_BULLETS = "•▪▫●◦○‣⁃·■□∙"
_ELLIPSIS_SPLIT = re.compile(r"\.{3,}|…")
_HYPHEN_BREAK = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")


def normalise(s: str) -> str:
    """Canonical form used for comparing quotes with document text."""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("­", "")                       # soft hyphen
    s = _HYPHEN_BREAK.sub("", s)                      # "deploy-\nment" -> "deployment"
    for k, v in _QUOTES.items():
        s = s.replace(k, v)
    s = re.sub("[" + re.escape(_DASHES) + "]", "-", s)
    s = re.sub("[" + re.escape(_BULLETS) + "]", " ", s)
    s = re.sub(r"(?m)^\s*[-*]\s+", " ", s)            # markdown / text bullets at line start
    s = re.sub(r"\s+", " ", s)
    return s.strip().casefold()


def _squash(s: str) -> str:
    """Looser form: drop hyphens and spaces around them, so 'de- ployment' still matches."""
    return re.sub(r"\s*-\s*", "", s)


def _parts(quote: str) -> list[str]:
    return [p for p in (normalise(x) for x in _ELLIPSIS_SPLIT.split(quote)) if p]


def _in_order(parts: list[str], haystack: str) -> bool:
    pos = 0
    for p in parts:
        i = haystack.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def quote_found(quote: str, text: str) -> bool:
    if not quote or not quote.strip() or not text:
        return False
    parts = _parts(quote)
    if not parts:
        return False
    hay = normalise(text)
    if _in_order(parts, hay):
        return True
    # second pass: tolerate stray hyphen/space artefacts from PDF line breaks
    hay2 = _squash(hay)
    parts2 = [_squash(p) for p in parts]
    return all(parts2) and _in_order(parts2, hay2)
