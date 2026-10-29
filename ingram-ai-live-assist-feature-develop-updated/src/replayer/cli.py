"""Run a recorded call against the agent as a live stream.

FILE is either a conversation JSON (text turns, replayed at their recorded
pace) or an audio manifest from make_audio.py (streamed through Amazon
Transcribe; partial results dropped, final segments sent as turns).

    # Text recording against a local agent (python main.py):
    python -m replayer.cli ui/regulatory_conv.json --speed 20

    # Audio through Transcribe, first two minutes, against a deployed runtime:
    python -m replayer.cli temp_code/audio/manifest.json --runtime-arn ARN --limit-seconds 120

    # Text recording, replayed from the replayer Lambda:
    python -m replayer.cli ui/regulatory_conv.json --runtime-arn ARN --lambda <prefix>-replayer

Follow the call with ``python scripts/watch_session.py SESSION_ID``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app.conversation_import import parse_conversation  # noqa: E402
from replayer.clients import DEFAULT_QUALIFIER, LocalSender, RuntimeSender, TimelineStatus  # noqa: E402
from replayer.core import new_session_id, replay  # noqa: E402
from replayer.transcribe import AmazonTranscriber, is_audio_manifest, load_manifest, stream_call  # noqa: E402

PREVIEW_CHARS = 70


def main() -> None:
    args = _parse_args()
    path = Path(args.file)
    data = json.loads(path.read_text(encoding="utf-8"))
    session_id = args.session_id or new_session_id()
    assist_config = _assist_config(args)
    title = args.title or (path.parent.name if is_audio_manifest(data) else path.stem)

    if args.lambda_name:
        _start_in_lambda(args, data, session_id, title, assist_config)
        return

    sender = (
        RuntimeSender(args.runtime_arn, region=args.region, qualifier=args.qualifier)
        if args.runtime_arn
        else LocalSender(args.local)
    )
    get_status = None if args.no_status else TimelineStatus(args.timeline_table, region=args.region)
    print(f"session_id: {session_id}")

    if is_audio_manifest(data):
        call = load_manifest(path)
        region = args.transcribe_region or args.region or "us-east-1"
        print(f"{len(call.tracks)} speaker tracks, {call.duration_sec or '?'} s of audio, "
              f"speed x{args.speed}, Transcribe in {region}")
        result = asyncio.run(stream_call(
            session_id=session_id,
            call=call,
            sender=sender,
            transcriber_factory=lambda: AmazonTranscriber(region, args.language),
            speed=args.speed,
            title=title,
            assist_config=assist_config,
            get_status=get_status,
            on_event=_print_event,
            limit_seconds=args.limit_seconds,
        ))
    else:
        turns = parse_conversation(data)
        print(f"{len(turns)} turns, speed x{args.speed}")
        result = replay(
            session_id=session_id,
            turns=turns,
            sender=sender,
            speed=args.speed,
            max_gap_seconds=args.max_gap,
            title=title,
            assist_config=assist_config,
            get_status=get_status,
            on_event=_print_event,
        )
    print(f"Stream {result.stopped}: {result.turns_sent} turns sent.")


def _start_in_lambda(args, data: dict, session_id: str, title: str, assist_config: dict) -> None:
    if is_audio_manifest(data):
        sys.exit("--lambda supports conversation JSON only; stream audio from this machine.")
    if not args.runtime_arn:
        sys.exit("--lambda needs --runtime-arn")
    parse_conversation(data)  # Validate before handing off.
    payload = {
        "conversation": data, "runtime_arn": args.runtime_arn, "session_id": session_id,
        "speed": args.speed, "max_gap_seconds": args.max_gap, "title": title,
        "assist_config": assist_config, "qualifier": args.qualifier,
    }
    boto3.client("lambda", region_name=args.region).invoke(
        FunctionName=args.lambda_name, InvocationType="Event", Payload=json.dumps(payload).encode()
    )
    print(f"Replay started in Lambda '{args.lambda_name}'.\nsession_id: {session_id}")


def _print_event(event: dict, ack: dict) -> None:
    if not ack.get("accepted"):
        print(f"  REJECTED {event['type']}: {ack.get('error')}")
    elif event["type"] == "turn":
        text = event["text"]
        preview = text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."
        print(f"  [{ack.get('seq'):>4}] {event['speaker']} {event.get('speaker_name') or ''}: {preview}")
    else:
        print(f"  {event['type']}: {ack.get('status')}")


def _assist_config(args: argparse.Namespace) -> dict:
    config = {"scope": args.scope, "frequency": args.frequency, "max_suggestions": args.max_suggestions}
    if args.instructions:
        config["instructions"] = args.instructions
    return config


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a recorded call against the agent as a live stream.")
    p.add_argument("file", help="Conversation JSON or audio manifest.json")
    p.add_argument("--session-id", help="Default: a new id")
    p.add_argument("--title")
    p.add_argument("--speed", type=float, default=1.0, help="1 = real time (default)")
    p.add_argument("--max-gap", type=float, default=8.0, help="Text only: longest pause in seconds, before speed")

    audio = p.add_argument_group("audio (Transcribe)")
    audio.add_argument("--transcribe-region", help="Default: --region, then us-east-1")
    audio.add_argument("--language", default="en-US")
    audio.add_argument("--limit-seconds", type=float, help="Stop after this much of the recording")

    target = p.add_argument_group("target")
    target.add_argument("--local", default="http://localhost:8080", help="Local agent URL")
    target.add_argument("--runtime-arn", default=os.environ.get("AGENTCORE_RUNTIME_ARN"))
    target.add_argument("--qualifier", default=DEFAULT_QUALIFIER)
    target.add_argument("--region", default=os.environ.get("AWS_REGION"))
    target.add_argument("--lambda", dest="lambda_name", help="Run a text replay in this Lambda function")

    status = p.add_argument_group("pause support")
    status.add_argument(
        "--timeline-table", default=os.environ.get("TIMELINE_TABLE_NAME", "ingram-live-assist-timeline")
    )
    status.add_argument("--no-status", action="store_true", help="Don't check for pause or end")

    settings = p.add_argument_group("suggestion settings")
    settings.add_argument("--scope", default="guided", choices=("checklist", "guided", "open"))
    settings.add_argument("--frequency", default="balanced", choices=("conservative", "balanced", "proactive"))
    settings.add_argument("--max-suggestions", type=int, default=2, choices=(1, 2, 3))
    settings.add_argument("--instructions", default="")
    return p.parse_args()


if __name__ == "__main__":
    main()
