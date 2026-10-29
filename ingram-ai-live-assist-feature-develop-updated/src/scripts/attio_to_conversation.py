"""Convert an Attio call transcript (copied as plain text) into the conversation
JSON that the Streamlit debug UI can import and replay.

Usage:
    python scripts/attio_to_conversation.py transcript.txt out.json \
        --customer "Lucas Barbosa" --start 2026-10-01T10:00:00Z

--customer can be repeated; anyone not listed is treated as the partner side
("ba"). --start is the call start time; transcript offsets are added to it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation_import import (  # noqa: E402
    attio_transcript_to_conversation,
    parse_conversation,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--customer", action="append", required=True,
                        help="speaker name to treat as the customer (cx); repeatable")
    parser.add_argument("--start", default="2026-10-01T00:00:00Z",
                        help="call start time, ISO-8601 (default: %(default)s)")
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    data = attio_transcript_to_conversation(
        args.input.read_text(encoding="utf-8"),
        customer_names=args.customer,
        start=start,
    )
    turns = parse_conversation(data)  # validate against the UI's import schema

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    speakers: dict[str, str] = {}
    for t in turns:
        speakers[t.name] = t.speaker
    print(f"Wrote {len(turns)} turns to {args.output}")
    for name, role in speakers.items():
        print(f"  {role:4s}  {name}")


if __name__ == "__main__":
    main()
