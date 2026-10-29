"""Dev helper: create (or fetch, if it already exists) the Amazon Bedrock AgentCore
Memory resource used to persist conversation history for live-assist sessions --
see req.md -> "Resolved: Session/Memory Persistence".

Only short-term memory (STM) is needed for this PoC (raw, ordered conversation
turns within a session). No long-term memory strategies (summarization, user
preferences, semantic facts) are configured, since there's no need to retain or
derive anything across separate conversations/opportunities yet.

Usage:
    python scripts/create_memory.py [--name NAME] [--region REGION]

Prints the resulting memory id. Set it as AGENTCORE_MEMORY_ID when running the app
(main.py / app/orchestrator.py reads this env var) so conversation history is
persisted outside the AgentCore Runtime microVM instead of falling back to the
Strands Agent's default in-process history.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()

from bedrock_agentcore.memory import MemoryClient  # noqa: E402

DEFAULT_MEMORY_NAME = "IngramLiveAssistMemory"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_MEMORY_NAME)
    parser.add_argument("--region", default=None)
    parser.add_argument(
        "--description",
        default="Short-term conversation memory for the Ingram live-assist agent (PoC).",
    )
    args = parser.parse_args()

    client = MemoryClient(region_name=args.region)

    # No `strategies` passed -> short-term memory only (see module docstring).
    memory = client.create_or_get_memory(
        name=args.name,
        description=args.description,
    )

    memory_id = memory.get("id")
    print(f"Memory ready: id={memory_id}")
    print(f"\nSet this before running the app:\n  export AGENTCORE_MEMORY_ID={memory_id}")


if __name__ == "__main__":
    main()
