"""Guidance config profile routes (see docs/api.md).

TODO(auth): any holder of the shared access key can create, edit and archive
profiles, and profile text goes into the agent's prompt. Restrict writes to an
admin role once the org auth service lands.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from api.http import ApiError, Request, Response
from app.config_store import BuiltinConfigError, ConfigCatalog, ConfigConflictError, ConfigNotFoundError
from app.events import valid_entity_id

# app.config_schema (pydantic) is imported inside the handlers that need it; see
# app/config_store.py for why.
CONFIG_ID = r"(?P<config_id>[a-z0-9-]{1,40})"

logger = logging.getLogger(__name__)


class ConfigRoutes:
    def __init__(self, catalog: ConfigCatalog) -> None:
        self.catalog = catalog

    def routes(self) -> list[tuple[str, str, Callable]]:
        base = rf"/configs/{CONFIG_ID}"
        # Literal paths first: the router takes the first route that matches.
        return [
            ("GET", r"/configs/schema", self.schema),
            ("POST", r"/configs/validate", self.validate),
            ("GET", r"/configs", self.list),
            ("POST", r"/configs", self.create),
            ("GET", base, self.get),
            ("PUT", base, self.update),
            ("DELETE", base, self.archive),
            ("GET", base + "/versions", self.versions),
        ]

    def schema(self, req: Request) -> Response:
        from app.config_schema import profile_json_schema

        return Response(200, profile_json_schema())

    def validate(self, req: Request) -> Response:
        from pydantic import ValidationError

        from app.config_schema import ProfileBody, validation_details

        try:
            ProfileBody.model_validate(req.json())
        except ValidationError as e:
            return Response(200, {"valid": False, "errors": validation_details(e)})
        return Response(200, {"valid": True, "errors": []})

    def list(self, req: Request) -> Response:
        include_archived = req.query.get("include_archived") == "true"
        return Response(200, {"configs": self.catalog.list(include_archived)})

    def get(self, req: Request, config_id: str) -> Response:
        return Response(200, self._get(config_id, _version_param(req)))

    def versions(self, req: Request, config_id: str) -> Response:
        self._get(config_id)
        return Response(200, {"config_id": config_id, "versions": self.catalog.versions(config_id)})

    def create(self, req: Request) -> Response:
        """Body: a full profile, or ``{"from_id": "...", "name": "..."}`` to clone one."""
        data = req.json()
        if "from_id" in data:
            source = self._get(_config_id(data.pop("from_id")))
            unknown = set(data) - {"name", "description"}
            if unknown:
                raise ApiError(400, "invalid_request",
                               f"A clone takes only 'name' and 'description'; got {sorted(unknown)}")
            data = {**source["body"], "name": data.get("name") or f"{source['name']} (copy)",
                    "description": data.get("description", source["body"].get("description", ""))}
        entry = self.catalog.create(_body(data))
        logger.info("config created id=%r", entry["config_id"])  # Audit; no user identity yet.
        return Response(201, entry)

    def update(self, req: Request, config_id: str) -> Response:
        data = req.json()
        expected = data.pop("expected_version", None)
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise ApiError(400, "invalid_request", "'expected_version' (the version you edited) is required")
        try:
            entry = self.catalog.update(config_id, _body(data), expected)
        except BuiltinConfigError:
            raise ApiError(403, "builtin_config", "The built-in profile is read-only; clone it") from None
        except ConfigNotFoundError:
            raise ApiError(404, "config_not_found", "No such config profile") from None
        except ConfigConflictError:
            raise ApiError(409, "version_conflict",
                           "The profile changed since you loaded it, or is archived. Reload and reapply.") from None
        logger.info("config updated id=%r version=%d", config_id, entry["version"])
        return Response(200, entry)

    def archive(self, req: Request, config_id: str) -> Response:
        try:
            self.catalog.archive(config_id)
        except BuiltinConfigError:
            raise ApiError(403, "builtin_config", "The built-in profile can't be archived") from None
        except ConfigNotFoundError:
            raise ApiError(404, "config_not_found", "No such config profile") from None
        logger.info("config archived id=%r", config_id)
        return Response(200, {"config_id": config_id, "archived": True})

    # --- used by POST /calls and GET /options ------------------------------------------

    def resolve(self, config_id: object, version: object, *, status: int = 400):
        """The checklists and entry a call would use; 400 for unknown or archived profiles."""
        if config_id is not None:
            config_id = _config_id(config_id)
        if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 0):
            raise ApiError(400, "invalid_request", "'config_version' must be a non-negative integer")
        try:
            return self.catalog.resolve(config_id, version)
        except ConfigNotFoundError:
            raise ApiError(status, "unknown_config", "Unknown or archived config profile") from None

    def _get(self, config_id: str, version: int | None = None) -> dict:
        try:
            return self.catalog.get(config_id, version)
        except ConfigNotFoundError:
            raise ApiError(404, "config_not_found", "No such config profile or version") from None


def _body(data: dict):
    from pydantic import ValidationError

    from app.config_schema import ProfileBody, validation_details

    try:
        return ProfileBody.model_validate(data)
    except ValidationError as e:
        raise ApiError(400, "invalid_config", "The profile doesn't match the schema",
                       validation_details(e)) from None


def _config_id(value: object) -> str:
    if not valid_entity_id(value):
        raise ApiError(400, "invalid_request", "'config_id' is malformed")
    return value  # type: ignore[return-value]


def _version_param(req: Request) -> int | None:
    raw = req.query.get("version")
    if raw is None:
        return None
    if not raw.isdigit():
        raise ApiError(400, "invalid_request", "'version' must be a non-negative integer")
    return int(raw)
