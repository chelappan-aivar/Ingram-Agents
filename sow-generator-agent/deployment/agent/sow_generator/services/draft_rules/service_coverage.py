"""Matching upstream architecture services against the draft by name and key terms."""

import re

# Generic vendor/filler words dropped before matching service names by key terms.
SERVICE_STOPWORDS = frozenset(
    {
        "aws",
        "amazon",
        "azure",
        "google",
        "gcp",
        "microsoft",
        "service",
        "services",
        "and",
        "or",
        "the",
        "for",
        "with",
        "of",
        "a",
        "an",
        "plus",
        "on",
        "to",
    }
)


# Fraction of a service name's significant tokens that must appear to count as covered.
SERVICE_TOKEN_MATCH_RATIO = 0.6


# Headings (case-insensitive substrings) whose bodies may describe a service.
SERVICE_COVERAGE_SECTIONS = [
    "Scope",
    "Scope of Work",
    "Scope of Services",
    "Out of Scope",
    "Out-of-Scope",
    "Exclusions",
    "Solution",
    "Architecture",
    "Deliverables",
    "Components",
]


def tokens_matched(tokens: set[str], haystack: str) -> bool:
    """True when a majority (>= ratio) of the given tokens appear in the haystack."""
    if not tokens:
        return False
    hits = sum(1 for t in tokens if t in haystack)
    return hits / len(tokens) >= SERVICE_TOKEN_MATCH_RATIO


def service_covered(service_name: str, haystack: str) -> bool:
    """True if the full name, a majority of its main tokens, or its parenthetical tokens appear."""
    if service_name.lower() in haystack:
        return True
    main_tokens, paren_tokens = significant_tokens(service_name)
    return tokens_matched(main_tokens, haystack) or tokens_matched(paren_tokens, haystack)


def significant_tokens(name: str) -> tuple[set[str], set[str]]:
    """Return (main-name tokens, parenthetical tokens), both minus vendor/stopwords."""
    paren_text = " ".join(re.findall(r"\(([^)]*)\)", name))
    bare = re.sub(r"\([^)]*\)", " ", name)

    def _filtered(text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {w for w in words if w not in SERVICE_STOPWORDS and len(w) > 1}

    return _filtered(bare), _filtered(paren_text)
