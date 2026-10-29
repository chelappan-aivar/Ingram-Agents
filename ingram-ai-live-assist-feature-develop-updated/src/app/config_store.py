"""Guidance config profiles saved from the UI, versioned, in the timeline table.

    session_id = "config#<config_id>", sk = "meta"       name, current_version, archived
    session_id = "config#<config_id>", sk = "v#000003"   one immutable version (the profile body)

No TTL: profiles are configuration, not call data. Listed through the
``calls-by-created`` index with ``gsi1pk = "config"``. The built-in profile
(``config/guidance``) is not stored here; ``ConfigCatalog`` adds it.

Saving a version is a conditional put of ``v#<n+1>``, so two concurrent saves
from the same ``expected_version`` can't both succeed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app.account_store import new_entity_id
from app.guidance import BUILTIN_PROFILE_ID, BUILTIN_PROFILE_NAME, GuidanceSet, builtin_guidance
from app.timeline_store import CALLS_INDEX, CALLS_INDEX_PK, DEFAULT_TIMELINE_TABLE, plain

if TYPE_CHECKING:
    # Imported lazily below: pydantic is compiled code, and the Lambda build's import
    # check runs on the build machine, which can't load the linux-arm64 binaries.
    from app.config_schema import ProfileBody

CONFIG_PK_PREFIX = "config#"
VERSION_PREFIX = "v#"
_INDEX_VALUE = "config"
MAX_VERSIONS_LISTED = 100


class ConfigNotFoundError(KeyError):
    """No such profile, or no such version of it."""


class ConfigConflictError(Exception):
    """The profile changed since ``expected_version`` (or is archived)."""


class BuiltinConfigError(Exception):
    """The built-in profile is read-only."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pk(config_id: str) -> str:
    return f"{CONFIG_PK_PREFIX}{config_id}"


def _vsk(version: int) -> str:
    return f"{VERSION_PREFIX}{version:06d}"


class ConfigStore:
    def __init__(
        self,
        table_name: str = DEFAULT_TIMELINE_TABLE,
        region_name: str | None = None,
        resource: Any | None = None,
    ) -> None:
        dynamodb = resource if resource is not None else boto3.resource("dynamodb", region_name=region_name)
        self._table = dynamodb.Table(table_name)

    def create(self, body: ProfileBody) -> dict:
        config_id = new_entity_id(body.name)
        now = _now_iso()
        self._put_version(config_id, 1, body, now)
        self._table.put_item(Item={
            "session_id": _pk(config_id), "sk": "meta", "kind": "config", "config_id": config_id,
            "name": body.name, "description": body.description, "current_version": 1, "archived": False,
            "created_at": now, "updated_at": now, CALLS_INDEX_PK: _INDEX_VALUE,
        }, ConditionExpression="attribute_not_exists(sk)")
        return self.get(config_id)

    def update(self, config_id: str, body: ProfileBody, expected_version: int) -> dict:
        meta = self._meta(config_id)
        if meta["archived"] or meta["current_version"] != expected_version:
            raise ConfigConflictError(config_id)
        now = _now_iso()
        self._put_version(config_id, expected_version + 1, body, now)  # The lock.
        try:
            self._table.update_item(
                Key={"session_id": _pk(config_id), "sk": "meta"},
                UpdateExpression="SET current_version = :new, #n = :name, description = :desc, updated_at = :now",
                ConditionExpression="current_version = :expected AND archived = :false",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={
                    ":new": expected_version + 1, ":expected": expected_version, ":false": False,
                    ":name": body.name, ":desc": body.description, ":now": now,
                },
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConfigConflictError(config_id) from None
            raise
        return self.get(config_id)

    def archive(self, config_id: str) -> None:
        self._meta(config_id)
        self._table.update_item(
            Key={"session_id": _pk(config_id), "sk": "meta"},
            UpdateExpression="SET archived = :true, updated_at = :now",
            ExpressionAttributeValues={":true": True, ":now": _now_iso()},
        )

    def get(self, config_id: str, version: int | None = None) -> dict:
        """``{"config_id", "name", ..., "version", "body"}``; the current version unless one is given."""
        meta = self._meta(config_id)
        version = version or meta["current_version"]
        item = self._table.get_item(Key={"session_id": _pk(config_id), "sk": _vsk(version)}).get("Item")
        if item is None:
            raise ConfigNotFoundError(f"{config_id} v{version}")
        return {**meta, "version": version, "version_created_at": item["created_at"], "body": plain(item["body"])}

    def list(self, include_archived: bool = False) -> list[dict]:
        kwargs: dict[str, Any] = {
            "IndexName": CALLS_INDEX,
            "KeyConditionExpression": Key(CALLS_INDEX_PK).eq(_INDEX_VALUE),
            "ScanIndexForward": False,
        }
        if not include_archived:
            kwargs["FilterExpression"] = Attr("archived").eq(False)
        items: list[dict] = []
        while True:
            response = self._table.query(**kwargs)
            items += [_meta(plain(i)) for i in response.get("Items", [])]
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def versions(self, config_id: str) -> list[dict]:
        self._meta(config_id)
        response = self._table.query(
            KeyConditionExpression=Key("session_id").eq(_pk(config_id)) & Key("sk").begins_with(VERSION_PREFIX),
            ScanIndexForward=False,
            Limit=MAX_VERSIONS_LISTED,
            ProjectionExpression="version, created_at",
        )
        return [plain(i) for i in response.get("Items", [])]

    def _meta(self, config_id: str) -> dict:
        item = self._table.get_item(Key={"session_id": _pk(config_id), "sk": "meta"}).get("Item")
        if item is None:
            raise ConfigNotFoundError(config_id)
        return _meta(plain(item))

    def _put_version(self, config_id: str, version: int, body: ProfileBody, now: str) -> None:
        try:
            self._table.put_item(Item={
                "session_id": _pk(config_id), "sk": _vsk(version), "kind": "config_version",
                "version": version, "body": body.model_dump(), "created_at": now,
            }, ConditionExpression="attribute_not_exists(sk)")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConfigConflictError(config_id) from None
            raise


def _meta(item: dict) -> dict:
    return {
        "config_id": item["config_id"], "name": item.get("name"), "description": item.get("description", ""),
        "builtin": False, "current_version": int(item["current_version"]), "archived": bool(item.get("archived")),
        "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
    }


class ConfigCatalog:
    """Built-in profile plus stored ones, the one place that turns an id into checklists."""

    def __init__(self, store: ConfigStore | None) -> None:
        self.store = store

    @staticmethod
    def builtin_entry() -> dict:
        return {
            "config_id": BUILTIN_PROFILE_ID, "name": BUILTIN_PROFILE_NAME,
            "description": "Read-only checklists shipped with the app. Clone it to make your own.",
            "builtin": True, "current_version": 0, "archived": False, "created_at": None, "updated_at": None,
        }

    def builtin_body(self) -> ProfileBody:
        from app.config_schema import ProfileBody

        return ProfileBody.from_guidance(builtin_guidance(), BUILTIN_PROFILE_NAME, self.builtin_entry()["description"])

    def list(self, include_archived: bool = False) -> list[dict]:
        return [self.builtin_entry(), *(self.store.list(include_archived) if self.store else [])]

    def get(self, config_id: str, version: int | None = None) -> dict:
        if config_id == BUILTIN_PROFILE_ID:
            if version not in (None, 0):
                raise ConfigNotFoundError(f"{config_id} v{version}")
            return {**self.builtin_entry(), "version": 0, "version_created_at": None,
                    "body": self.builtin_body().model_dump()}
        if self.store is None:
            raise ConfigNotFoundError(config_id)
        return self.store.get(config_id, version)

    def create(self, body: ProfileBody) -> dict:
        return self._writable().create(body)

    def update(self, config_id: str, body: ProfileBody, expected_version: int) -> dict:
        self._not_builtin(config_id)
        return self._writable().update(config_id, body, expected_version)

    def archive(self, config_id: str) -> None:
        self._not_builtin(config_id)
        self._writable().archive(config_id)

    def versions(self, config_id: str) -> list[dict]:
        if config_id == BUILTIN_PROFILE_ID:
            return [{"version": 0, "created_at": None}]
        return self._writable().versions(config_id)

    def _not_builtin(self, config_id: str) -> None:
        if config_id == BUILTIN_PROFILE_ID:
            raise BuiltinConfigError(config_id)

    def _writable(self) -> ConfigStore:
        if self.store is None:
            raise ConfigNotFoundError("config storage is not configured")
        return self.store

    def resolve(self, config_id: str | None, version: int | None = None) -> tuple[GuidanceSet, dict]:
        """Checklists for a new call, and the entry they came from. Archived profiles can't start calls."""
        if not config_id or config_id == BUILTIN_PROFILE_ID:
            return builtin_guidance(), self.builtin_entry() | {"version": 0}
        entry = self.get(config_id, version)
        if entry["archived"]:
            raise ConfigNotFoundError(f"{config_id} is archived")
        from app.config_schema import ProfileBody

        body = ProfileBody.model_validate(entry["body"])
        return body.to_guidance(config_id, entry["version"]), entry
