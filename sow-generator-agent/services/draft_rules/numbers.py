"""Number and duration matching used by the draft rules."""

import re

# Reuse the intake reconciliation tolerance so pricing matches use one rule.
COST_TOLERANCE_ABS = 1.0


COST_TOLERANCE_PCT = 0.01


# Pricing-presence tolerance: tight on value, generous only for calculator rounding.
PRICING_TOLERANCE_PCT = 0.001


# Suffix multipliers so "6.2k" / "$0.22M" reconcile against raw totals.
MAGNITUDE = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}


# Weeks-per-month used to normalise mixed duration units.
WEEKS_PER_MONTH = 4

# Timeline match tolerance in weeks, absorbing rounding between units.
TIMELINE_TOLERANCE_WEEKS = 2


_WEEKS_RE = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?weeks?", re.IGNORECASE)


# Template-style lines such as "Weeks 2-3: Development ..." (number before the word "weeks").
_WEEKS_LEADING_RE = re.compile(r"weeks?\s*(\d+)\s*(?:[\u2013-]\s*(\d+))?", re.IGNORECASE)


_MONTHS_RE = re.compile(r"~?\s*(\d+)\s*months?", re.IGNORECASE)


def find_labelled_number(text: str, label: str) -> tuple[float | None, str | None]:
    """Extract a number associated with ``label`` from a markdown table cell or
    a ``Label: NN`` / ``Label | NN`` line. Returns (value, evidence_line)."""
    label_re = re.escape(label)
    # Table row: | Annual Fee | 95% | ...  OR  bare line "Annual Fee: 95%".
    pattern = re.compile(
        rf"{label_re}\s*[:|]\s*\$?\s*([0-9][0-9,]*\.?[0-9]*)\s*%?",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.search(line)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                return float(raw), line.strip()
            except ValueError:
                continue
    return None, None


def number_appears(text: str, target: float) -> bool:
    """True if ``target`` appears in text within a tight pricing tolerance.

    Matches thousands separators, decimals, and magnitude suffixes, so a target
    of 6200 matches "6,200", "$6,200.00", "6200", and "6.2k".
    """
    tolerance = max(COST_TOLERANCE_ABS, PRICING_TOLERANCE_PCT * abs(target))
    for m in re.finditer(r"\$?\s*([0-9][0-9,]*\.?[0-9]*)\s*([kKmMbB])?", text):
        raw = m.group(1).replace(",", "")
        if not raw or raw == ".":
            continue
        try:
            found = float(raw)
        except ValueError:
            continue
        suffix = (m.group(2) or "").lower()
        if suffix:
            found *= MAGNITUDE[suffix]
        if abs(found - target) <= tolerance:
            return True
    return False


def normalise_durations(text: str) -> set[int]:
    """Return durations in weeks found in the text, converting months (x4)."""
    weeks: set[int] = set()
    for m in _WEEKS_RE.finditer(text):
        weeks.add(int(m.group(1)))
    for m in _WEEKS_LEADING_RE.finditer(text):
        weeks.add(int(m.group(1)))
    for m in _MONTHS_RE.finditer(text):
        weeks.add(int(m.group(1)) * WEEKS_PER_MONTH)
    return weeks
