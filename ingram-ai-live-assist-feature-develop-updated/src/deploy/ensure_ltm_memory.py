"""Create or update the long-term AgentCore Memory used across calls (see app/memory.py).

    python deploy/ensure_ltm_memory.py --name ila_dev_ltm --region us-east-1 \
        [--event-expiry-days 180] [--record-expiry-days 180]

Idempotent: finds the memory by name, creates it if missing, adds the fact strategy
if it is missing, and brings both expiries to the requested values. Prints the
memory id on stdout; progress goes to stderr.
Uses boto3 rather than the AWS CLI because older CLI versions lack
``namespaceTemplates`` and ``recordExpirySpecification``.
"""

from __future__ import annotations

import argparse
import sys
import time

import boto3

STRATEGY_NAME = "CallFacts"
NAMESPACE_TEMPLATE = "/facts/{actorId}/"  # Must match app.memory.facts_namespace.
MIN_RECORD_EXPIRY_DAYS = 31  # AgentCore's minimum.


def strategy(record_expiry_days: int) -> dict:
    return {
        "semanticMemoryStrategy": {
            "name": STRATEGY_NAME,
            "description": "Facts about the customer and the opportunity from curated call notes",
            "namespaceTemplates": [NAMESPACE_TEMPLATE],
            "recordExpirySpecification": {"recordExpiryDurationDays": record_expiry_days},
        }
    }


def log(msg: str) -> None:
    print(f"    {msg}", file=sys.stderr)


def find(client, name: str) -> str | None:
    kwargs: dict = {}
    while True:
        page = client.list_memories(**kwargs)
        for memory in page.get("memories", []):
            if memory["id"].startswith(f"{name}-"):
                return memory["id"]
        if not page.get("nextToken"):
            return None
        kwargs["nextToken"] = page["nextToken"]


def wait_active(client, memory_id: str, tries: int = 60, delay: int = 10) -> dict:
    for _ in range(tries):
        memory = client.get_memory(memoryId=memory_id)["memory"]
        if memory["status"] == "ACTIVE":
            return memory
        if memory["status"] == "FAILED":
            sys.exit(f"memory {memory_id} failed: {memory.get('failureReason')}")
        time.sleep(delay)
    sys.exit(f"timed out waiting for memory {memory_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--event-expiry-days", type=int, default=180)
    parser.add_argument("--record-expiry-days", type=int, default=180)
    args = parser.parse_args()
    if args.record_expiry_days < MIN_RECORD_EXPIRY_DAYS:
        sys.exit(f"--record-expiry-days must be at least {MIN_RECORD_EXPIRY_DAYS}")
    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    memory_id = find(client, args.name)
    if memory_id is None:
        memory_id = client.create_memory(
            name=args.name,
            description="Live Assist long-term memory across calls",
            eventExpiryDuration=args.event_expiry_days,
            memoryStrategies=[strategy(args.record_expiry_days)],
        )["memory"]["id"]
        log(f"created: {memory_id}")
    else:
        log(f"exists: {memory_id}")

    memory = wait_active(client, memory_id)
    if memory.get("eventExpiryDuration") != args.event_expiry_days:
        client.update_memory(memoryId=memory_id, eventExpiryDuration=args.event_expiry_days)
        log(f"event expiry set to {args.event_expiry_days} days")
        memory = wait_active(client, memory_id)

    current = next((s for s in memory.get("strategies", []) if s.get("name") == STRATEGY_NAME), None)
    if current is None:
        client.update_memory(memoryId=memory_id,
                             memoryStrategies={"addMemoryStrategies": [strategy(args.record_expiry_days)]})
        log(f"added strategy {STRATEGY_NAME}")
        wait_active(client, memory_id)
    elif current.get("recordExpiryDurationDays") != args.record_expiry_days:
        # Applies to records created from now on; existing records keep their expiry.
        client.update_memory(memoryId=memory_id, memoryStrategies={"modifyMemoryStrategies": [{
            "memoryStrategyId": current["strategyId"],
            "recordExpirySpecification": {"recordExpiryDurationDays": args.record_expiry_days},
        }]})
        log(f"record expiry set to {args.record_expiry_days} days")
        wait_active(client, memory_id)
    print(memory_id)


if __name__ == "__main__":
    main()
