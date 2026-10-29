"""Create the DynamoDB timeline table (call meta, turns, runs) with a TTL on ``expires_at``
and the calls-by-created index.

    python scripts/create_timeline_table.py [--table-name NAME] [--region REGION]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeline_store import (  # noqa: E402
    ACCOUNT_CALLS_INDEX,
    ACCOUNT_CALLS_INDEX_PK,
    CALLS_INDEX,
    CALLS_INDEX_PK,
    DEFAULT_TIMELINE_TABLE,
)

CALLS_INDEX_SPEC = {
    "IndexName": CALLS_INDEX,
    "KeySchema": [
        {"AttributeName": CALLS_INDEX_PK, "KeyType": "HASH"},
        {"AttributeName": "created_at", "KeyType": "RANGE"},
    ],
    "Projection": {"ProjectionType": "ALL"},
}
ACCOUNT_CALLS_INDEX_SPEC = {
    "IndexName": ACCOUNT_CALLS_INDEX,
    "KeySchema": [
        {"AttributeName": ACCOUNT_CALLS_INDEX_PK, "KeyType": "HASH"},
        {"AttributeName": "created_at", "KeyType": "RANGE"},
    ],
    "Projection": {"ProjectionType": "ALL"},
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default=DEFAULT_TIMELINE_TABLE)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    dynamodb = boto3.client("dynamodb", region_name=args.region)
    try:
        dynamodb.create_table(
            TableName=args.table_name,
            KeySchema=[
                {"AttributeName": "session_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "session_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": CALLS_INDEX_PK, "AttributeType": "S"},
                {"AttributeName": ACCOUNT_CALLS_INDEX_PK, "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[CALLS_INDEX_SPEC, ACCOUNT_CALLS_INDEX_SPEC],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"Creating table '{args.table_name}'...")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"Table '{args.table_name}' already exists.")

    dynamodb.get_waiter("table_exists").wait(TableName=args.table_name)
    try:
        dynamodb.update_time_to_live(
            TableName=args.table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        print("TTL enabled on 'expires_at'.")
    except ClientError as e:
        if "already enabled" not in str(e):
            raise
        print("TTL already enabled.")


if __name__ == "__main__":
    main()
