import re


def clean_transcript(raw: str) -> str:
    """Drop the auto-generated summary header (everything up to the 'Transcript' line), if present."""
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "Transcript":
            rest = lines[i + 1 :]
            if rest and rest[0].strip() == "Speakers":
                rest = rest[1:]
            return "\n".join(rest).strip()
    return raw.strip()


_FILLER = re.compile(r"\b(?:uh+|um+|uhm|er|erm|ah|hmm+|mm+)\b")
_REPEAT = re.compile(r"\b(\w+(?: \w+)?)(?: \1\b)+")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, spoken fillers (uh, um) and immediate word repeats, so a quote still matches
    when the model tidied disfluencies. Applied to both the quote and the source."""
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9']+", " ", text.lower())
    text = _FILLER.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _REPEAT.sub(r"\1", text)


def speakers(text: str) -> set[str]:
    """Speaker names: a non-empty line directly followed by an m:ss / h:mm:ss timestamp line."""
    lines = text.splitlines()
    ts = re.compile(r"^\d+:\d{2}(:\d{2})?$")
    return {
        lines[i].strip()
        for i in range(len(lines) - 1)
        if lines[i].strip() and not ts.match(lines[i].strip()) and ts.match(lines[i + 1].strip())
    }
