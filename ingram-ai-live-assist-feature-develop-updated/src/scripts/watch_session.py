"""Follow a call live from DynamoDB: new turns, agent runs and suggestions.

    python scripts/watch_session.py SESSION_ID [--interval 1.5] [--handoff out.json]

Uses the same read model a console would poll (app/views.py). Exits when the
call has ended and every turn is processed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session_store import SessionStore  # noqa: E402
from app.timeline_store import TimelineStore  # noqa: E402
from app.views import build_handoff, build_session_view  # noqa: E402

PREVIEW_CHARS = 90


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--handoff", type=Path, help="Write the handoff JSON here when the call ends")
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    timeline = TimelineStore(region_name=args.region)
    store = SessionStore(region_name=args.region)
    shown_turns, shown_runs = set(), {}

    while True:
        view = build_session_view(args.session_id, timeline=timeline, store=store)
        if view is None:
            print("Waiting for session.start...")
            time.sleep(args.interval)
            continue

        for t in view["turns"]:
            if t["seq"] not in shown_turns:
                shown_turns.add(t["seq"])
                text = t["text"] if len(t["text"]) <= PREVIEW_CHARS else t["text"][:PREVIEW_CHARS] + "..."
                print(f"[{t['seq']:>4}] {t['speaker']} {t.get('speaker_name') or ''}: {text}")

        for r in view["runs"]:
            key = r["first_seq"]
            if shown_runs.get(key) == r["status"]:
                continue
            shown_runs[key] = r["status"]
            _print_run(r)

        processing = view["processing"]
        if view["status"] == "ended" and not processing["busy"]:
            _print_state(view)
            if args.handoff:
                args.handoff.write_text(json.dumps(build_handoff(view), indent=2), encoding="utf-8")
                print(f"Handoff written to {args.handoff}")
            return
        time.sleep(args.interval)


def _print_run(run: dict) -> None:
    head = f"  -- run #{run.get('run_number') or '?'} turns {run['first_seq']}-{run['last_seq']}"
    if run["status"] == "error":
        print(f"{head} FAILED: {run.get('error')}")
        return
    print(f"{head} ({run.get('duration_ms')} ms)")
    for line in run.get("narration") or []:
        print(f"     agent: {line}")
    for fid in run.get("captured_fields") or []:
        print(f"     captured: {fid}")
    for s in run.get("suggestions") or []:
        topic = s.get("target_label") or "follow-up"
        print(f"     >> ASK: {s['question']}  [{topic}]")


def _print_state(view: dict) -> None:
    state = view["state"]
    print("\n=== Call ended ===")
    print(f"Problem: {state['problem_summary']}")
    print("Captured: " + (", ".join(c["label"] for c in state["captured"]) or "none"))
    print("Open: " + (", ".join(g["label"] for g in state["open_gaps"]) or "none"))


if __name__ == "__main__":
    main()
