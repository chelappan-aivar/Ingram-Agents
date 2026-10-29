"""Shared pytest fixtures: moto-mocked DynamoDB tables and stores."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.account_store import AccountStore
from app.memory import LongTermMemory
from app.session_store import SessionStore
from app.timeline_store import TimelineStore
from tests.fake_memory import FakeMemoryClient

TEST_TABLE_NAME = "test-ingram-live-assist-sessions"
TEST_TIMELINE_TABLE = "test-ingram-live-assist-timeline"


@pytest.fixture(autouse=True)
def prompt_log_dir(tmp_path, monkeypatch):
    """Keep tests from writing prompt files into the project's ./tmp."""
    path = tmp_path / "prompts"
    monkeypatch.setattr("app.orchestrator.PROMPT_LOG_DIR", str(path))
    return path


@pytest.fixture
def started(monkeypatch):
    """session.start events the entry point received."""
    events = []
    import main

    real = main.handle_event

    def spy(event, **kw):
        events.append(event)
        return real(event, **kw)

    monkeypatch.setattr(main, "handle_event", spy)
    return events


@pytest.fixture
def dynamodb_resource():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName=TEST_TABLE_NAME,
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        resource.create_table(
            TableName=TEST_TIMELINE_TABLE,
            KeySchema=[
                {"AttributeName": "session_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "session_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "gsi1pk", "AttributeType": "S"},
                {"AttributeName": "gsi2pk", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": index,
                    "KeySchema": [
                        {"AttributeName": pk, "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
                for index, pk in (("calls-by-created", "gsi1pk"), ("calls-by-account", "gsi2pk"))
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield resource


@pytest.fixture
def session_store(dynamodb_resource) -> SessionStore:
    return SessionStore(table_name=TEST_TABLE_NAME, resource=dynamodb_resource)


@pytest.fixture
def timeline(dynamodb_resource) -> TimelineStore:
    return TimelineStore(table_name=TEST_TIMELINE_TABLE, resource=dynamodb_resource)


@pytest.fixture
def accounts(dynamodb_resource) -> AccountStore:
    return AccountStore(table_name=TEST_TIMELINE_TABLE, resource=dynamodb_resource)


@pytest.fixture
def memory_client() -> FakeMemoryClient:
    return FakeMemoryClient()


@pytest.fixture
def memory(memory_client) -> LongTermMemory:
    return LongTermMemory("test_memory-0123456789", client=memory_client)


SESSION_ID = "test-call-0001-abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def session_id() -> str:
    return SESSION_ID
