"""Dev helper: create the DynamoDB table used by app.session_store.SessionStore.

Not part of the request-handling path -- run this once against your target
AWS account/region (or a local DynamoDB) before invoking the agent for real.

Usage:
    python scripts/create_session_table.py [--table-name NAME] [--region REGION]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

# Allow running this script directly (python scripts/create_session_table.py)
# without needing the project installed as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session_store import DEFAULT_TABLE_NAME  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default=DEFAULT_TABLE_NAME)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    dynamodb = boto3.client("dynamodb", region_name=args.region)
    try:
        dynamodb.create_table(
            TableName=args.table_name,
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"Created table '{args.table_name}'.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"Table '{args.table_name}' already exists.")
        else:
            raise


if __name__ == "__main__":
    main()
