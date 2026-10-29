"""Routes and handlers for the HTTP API. Framework-free: ``Api.handle`` takes a
``Request`` and returns a ``Response``, so the same code runs behind API
Gateway (``api/handler.py``) and the local dev server (``api/local.py``).

Writes go to the AgentCore runtime as ``ila.event.v1`` events; reads come from
DynamoDB through ``app/views.py``. Auth is a shared key (in Secrets Manager) in ``X-Access-Key``
until the org auth service replaces it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from api.http import (  # noqa: F401 - Request/Response/ApiError re-exported for callers
    MAX_BODY_BYTES,
    MAX_CALL_BODY_BYTES,
    ApiError,
    Request,
    Response,
    decode_cursor,
    encode_cursor,
    error_response,
    parse_limit,
)
from app.assist_config import (
    FREQUENCIES,
    MAX_INSTRUCTIONS_CHARS,
    MAX_SUGGESTIONS_LIMIT,
    PRIOR_CONTEXT_MODES,
    SCOPES,
    AssistConfig,
    InvalidAssistConfigError,
)
from app.conversation_import import InvalidConversationFileError, parse_conversation
from app.events import SCHEMA, SESSION_ID_RE, InvalidEventError, parse_description

from app.minutes_export import minutes_markdown
from app.session_store import SessionStore
from app.timeline_store import TimelineStore
from api.accounts import AccountRoutes
from api.intake import IntakeRoutes
from app.intake_feed import IntakeFeed
from app.intake_link_store import IntakeLinkStore
from app.discovery_link import DiscoveryClient
from api.live_sources import LiveSources
from api.recall import RecallSettings, RecallSource
from api.configs import ConfigRoutes
from app.config_store import ConfigCatalog, ConfigStore
from app.account_store import AccountStore
from app.memory import LongTermMemory
from app.views import build_handoff, build_session_view, call_row
from replayer.core import new_session_id
from replayer.transcribe import MANIFEST_FILE, MAX_SPEED, MIN_SPEED, InvalidManifestError, S3Reader, parse_manifest

ACCESS_HEADER = "x-access-key"
UPLOAD_URL_SECONDS = 900
MAX_UPLOAD_FILES = 20
MAX_RECORDING_BYTES = 5 * 1024 * 1024
RECORDING_FILE = "conversation.json"
MAX_TITLE_CHARS = 200
RECORDING_MAX_SPEED = 50.0
MAX_GAP_LIMIT = 60.0

# API source type -> event source. Recording and audio sources own the call's turns.
SOURCE_TYPES = {"manual": "manual", "recording": "recording", "audio": "transcribe",
                "meeting": "meeting", "transcript": "recording"}
UPLOAD_KINDS = ("recording", "audio")

# Ack codes from the runtime -> HTTP status.
_ACK_STATUS = {
    "invalid_event": 400,
    "unknown_session": 404,
    "session_ended": 409,
    "source_mismatch": 409,
    "session_live": 409,
    "invalid_meeting_link": 400,
    "meeting_busy": 409,
    "meeting_limit": 429,
    "meeting_not_authorized": 502,
    "meeting_unavailable": 502,
    "meeting_not_configured": 502,
}

_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_UPLOAD_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,3}$")

logger = logging.getLogger(__name__)


@dataclass
class Services:
    timeline: TimelineStore
    store: SessionStore
    sender: Any  # replayer.core.Sender
    s3: Any
    bucket: str | None
    start_source: Callable[[dict], None]  # Starts a recording/audio job; raises on failure.
    access_key: Callable[[], str | None]
    accounts: AccountStore | None = None
    memory: LongTermMemory | None = None  # Long-term memory; None when not configured.
    configs: ConfigCatalog | None = None  # None: the built-in profile only.
    direct_upload: bool = False  # True: browsers PUT to the presigned URL themselves (the bucket allows their origin).
    meeting_provider: str = "vexa"  # "vexa": the runtime follows the meeting. "recall": Recall pushes webhooks here.
    recall: RecallSource | None = None  # Set when meeting_provider is "recall" and it is configured.
    intake_feed: IntakeFeed | None = None  # Intake Agent funding checks (read-only); None when not configured.
    intake_links: IntakeLinkStore | None = None  # Call history map per Intake opportunity.
    discovery: DiscoveryClient | None = None  # Discovery agent API (reads its runs); None when not configured.
    crm_base_url: str = ""  # Dynamics origin (https) for the CRM link shown with an opportunity; empty: no link.


class Api:
    def __init__(self, services: Services) -> None:
        self.s = services
        cid = r"/calls/(?P<session_id>[^/]+)"
        self._routes: list[tuple[str, re.Pattern, Callable, bool]] = [
            ("GET", re.compile(r"^/health$"), self.health, False),
            ("GET", re.compile(r"^/options$"), self.options, True),
            ("POST", re.compile(r"^/uploads$"), self.create_upload, True),
            ("POST", re.compile(r"^/calls$"), self.create_call, True),
            ("GET", re.compile(r"^/calls$"), self.list_calls, True),
            ("GET", re.compile(rf"^{cid}$"), self.get_call, True),
            ("PATCH", re.compile(rf"^{cid}$"), self.update_call, True),
            ("POST", re.compile(rf"^{cid}/turns$"), self.add_turn, True),
            ("POST", re.compile(rf"^{cid}/end$"), self.end_call, True),
            ("GET", re.compile(rf"^{cid}/handoff$"), self.get_handoff, True),
            ("GET", re.compile(rf"^{cid}/minutes$"), self.get_minutes, True),
            ("POST", re.compile(rf"^{cid}/minutes$"), self.regenerate_minutes, True),
        ]
        recall_missing = services.meeting_provider == "recall" and services.recall is None
        self.live = LiveSources(
            services.meeting_provider, meeting_ready=not recall_missing,
            not_ready_reason="Live meetings are not set up on this server yet (the Recall settings are missing).")
        self.accounts = AccountRoutes(accounts=services.accounts, timeline=services.timeline, memory=services.memory)
        self.configs = ConfigRoutes(services.configs or ConfigCatalog(None))
        self.intake = IntakeRoutes(feed=services.intake_feed, links=services.intake_links, timeline=services.timeline,
                                   view=self._view, discovery=services.discovery, crm_base_url=services.crm_base_url)
        self._routes += [
            (method, re.compile(f"^{path}$"), handler, True)
            for method, path, handler in [*self.accounts.routes(), *self.configs.routes(), *self.intake.routes()]
        ]
        if services.recall is not None:
            # Recall cannot send the access key. These routes check Recall's request signature instead.
            self._routes += [
                (method, re.compile(f"^{path}$"), handler, False) for method, path, handler in services.recall.routes()
            ]

    def handle(self, req: Request) -> Response:
        if req.method == "OPTIONS":
            # A browser's CORS preflight from the console (served from another origin). API Gateway forwards it here
            # because the API has a catch-all route. It carries no data and needs no key: API Gateway adds the CORS
            # headers only for the console origin, and the browser needs just a 2xx.
            return Response(204)
        try:
            matched = [(m, r, h, a) for m, r, h, a in self._routes if r.match(req.path)]
            if not matched:
                raise ApiError(404, "not_found", "No such route")
            route = next((x for x in matched if x[0] == req.method), None)
            if route is None:
                raise ApiError(405, "method_not_allowed", f"Use {', '.join(sorted({x[0] for x in matched}))}")
            _, pattern, handler, needs_auth = route
            if needs_auth:
                self._authorize(req)
            return handler(req, **pattern.match(req.path).groupdict())
        except ApiError as e:
            return error_response(e.status, e.code, e.message, e.details)
        except Exception:  # noqa: BLE001
            logger.exception("unhandled error %s %s", req.method, req.path)
            return error_response(500, "internal_error", "Something went wrong")

    def _authorize(self, req: Request) -> None:
        expected = self.s.access_key()
        if not expected:
            raise ApiError(500, "auth_not_configured", "Access key is not configured")
        provided = req.headers.get(ACCESS_HEADER, "")
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            raise ApiError(401, "unauthorized", f"Missing or wrong {ACCESS_HEADER} header")

    # --- meta -------------------------------------------------------------------

    def health(self, req: Request) -> Response:
        return Response(200, {"status": "ok"})

    def options(self, req: Request) -> Response:
        """Form choices. ``?config_id=`` picks whose topics and opportunity types to list."""
        guidance, entry = self.configs.resolve(req.query.get("config_id"), None, status=404)
        topics = [
            {"field_id": f.id, "label": f.label, "category": f.category, "required": f.required,
             "description": f.description, "memory_scope": f.memory_scope}
            for f in guidance.general.required_fields
        ]
        return Response(200, {
            "assist_config": {
                "defaults": AssistConfig().to_dict(),
                "scope": list(SCOPES),
                "frequency": list(FREQUENCIES),
                "max_suggestions": {"min": 1, "max": MAX_SUGGESTIONS_LIMIT},
                "instructions_max_chars": MAX_INSTRUCTIONS_CHARS,
                "use_prior_context": list(PRIOR_CONTEXT_MODES),
            },
            "memory": {"enabled": self.s.memory is not None},
            "opportunity_types": guidance.list_opportunity_types(),
            "config": {"config_id": entry["config_id"], "name": entry["name"], "version": entry["version"]},
            "sources": {
                "manual": {},
                "recording": {
                    "speed": {"min_exclusive": 0, "max": RECORDING_MAX_SPEED, "default": 10},
                    "max_gap_seconds": {"min_exclusive": 0, "max": MAX_GAP_LIMIT, "default": 8},
                },
                "audio": {
                    "speed": {"min": MIN_SPEED, "max": MAX_SPEED, "default": 1},
                    "limit_seconds": {"min_exclusive": 0, "default": None},
                    "upload": "direct" if self.s.direct_upload else "proxy",
                },
                **self.live.options(),
            },
            "topics": topics,
        })

    # --- uploads ------------------------------------------------------------------

    def create_upload(self, req: Request) -> Response:
        body = req.json()
        kind = body.get("kind")
        if kind not in UPLOAD_KINDS:
            raise ApiError(400, "invalid_request", f"'kind' must be one of {list(UPLOAD_KINDS)}")
        self._require_bucket()
        if kind == "recording":
            files = [RECORDING_FILE]
        else:
            files = body.get("files")
            if not isinstance(files, list) or not files or len(files) > MAX_UPLOAD_FILES:
                raise ApiError(400, "invalid_request", f"'files' must list 1-{MAX_UPLOAD_FILES} paths")
            if MANIFEST_FILE not in files:
                raise ApiError(400, "invalid_request", f"'files' must include {MANIFEST_FILE}")
            if len(set(files)) != len(files) or not all(_valid_upload_path(f) for f in files):
                raise ApiError(400, "invalid_request",
                               "Paths must be unique, relative, and use letters, digits, '.', '_', '-' and '/'")

        upload_id = uuid.uuid4().hex
        prefix = _upload_prefix(kind, upload_id)
        urls = [
            {
                "path": path,
                "method": "PUT",
                "url": self.s.s3.generate_presigned_url(
                    "put_object", Params={"Bucket": self.s.bucket, "Key": prefix + path},
                    ExpiresIn=UPLOAD_URL_SECONDS,
                ),
            }
            for path in files
        ]
        return Response(201, {"upload_id": upload_id, "kind": kind, "expires_in": UPLOAD_URL_SECONDS,
                              "files": urls})

    # --- calls --------------------------------------------------------------------

    def create_call(self, req: Request) -> Response:
        body = req.json(MAX_CALL_BODY_BYTES)
        try:
            config = AssistConfig.from_payload(body.get("assist_config"))
        except InvalidAssistConfigError as e:
            raise ApiError(400, "invalid_request", str(e)) from None

        source = body.get("source") or {"type": "manual"}
        if not isinstance(source, dict) or source.get("type") not in SOURCE_TYPES:
            raise ApiError(400, "invalid_request", f"'source.type' must be one of {list(SOURCE_TYPES)}")
        kind = source["type"]
        title = body.get("title") or {"manual": "Manual call", "recording": "Recording", "audio": "Audio call",
                                      "meeting": "Live meeting", "transcript": "Transcript"}[kind]
        if not isinstance(title, str) or len(title.strip()) == 0 or len(title) > MAX_TITLE_CHARS:
            raise ApiError(400, "invalid_request", f"'title' must be 1-{MAX_TITLE_CHARS} characters")
        title_given = bool(body.get("title"))
        title = title.strip()
        try:
            description = parse_description(body.get("description"))
        except InvalidEventError as e:
            raise ApiError(400, "invalid_request", str(e)) from None
        # Only a live meeting gets pre-questions, and only when the BA gave a title or a description to go on.
        prepare_questions = kind == "meeting" and (title_given or description is not None)

        scope = self.accounts.call_scope(body)
        guidance, entry = self.configs.resolve(body.get("config_id"), body.get("config_version"))
        preset = scope.get("opportunity_type")
        if preset and preset not in guidance.specialised:
            raise ApiError(400, "opportunity_type_not_in_config",
                           f"The opportunity's type '{preset}' is not in config '{entry['config_id']}'")
        job = self._prepare_job(kind, source) if kind not in ("manual", "meeting") else None
        plan = self.live.meeting_plan(source) if kind == "meeting" else None
        session_id = new_session_id()
        logger.info(
            "call start session=%r source=%s speed=%s limit_seconds=%s scope=%s frequency=%s max_suggestions=%s",
            session_id, kind, (job or {}).get("speed"), (job or {}).get("limit_seconds"),
            config.scope, config.frequency, config.max_suggestions,
        )
        start_event = {
            "schema": SCHEMA, "type": "session.start", "session_id": session_id,
            "source": SOURCE_TYPES[kind], "title": title, "assist_config": config.to_dict(), **scope,
            "config_id": guidance.profile_id, "config_version": guidance.version,
        }
        if description is not None:
            start_event["description"] = description
        if prepare_questions:
            start_event["prepare_questions"] = True
        self._send(start_event)

        if plan is not None:
            try:
                if self.s.recall is not None:
                    # Recall: this API sends the bot. Its webhooks bring the turns back to /recall/transcript.
                    self.s.recall.start(meeting_url=plan.meeting_url, bot_name=plan.bot_name,
                                        session_id=session_id, ba_names=plan.ba_names)
                elif self.s.meeting_provider == "recall":
                    raise ApiError(502, "meeting_not_configured",
                                   "The meeting service is not configured on the server.")
                else:
                    # Vexa: the runtime session that owns the call sends the bot and follows the meeting.
                    # The event carries the Vexa keys: it goes to the runtime only and is never logged.
                    self._send(self.live.join_event(plan, session_id))
            except ApiError:
                self._send_quietly({"schema": SCHEMA, "type": "session.end", "session_id": session_id,
                                    "reason": "the meeting could not be joined"})
                raise

        if job is not None:
            job.update(session_id=session_id, title=title, assist_config=config.to_dict(), send_start=False)
            try:
                self.s.start_source(job)
            except Exception:  # noqa: BLE001
                logger.exception("could not start source session=%r", session_id)
                self._send_quietly({"schema": SCHEMA, "type": "session.end", "session_id": session_id,
                                    "reason": "source failed to start"})
                raise ApiError(502, "source_start_failed", "The call's source could not be started") from None

        return Response(201, {"session_id": session_id, "status": "live", "source": kind, "title": title,
                              "account_id": scope.get("account_id"), "opportunity_id": scope.get("opportunity_id"),
                              "config_id": guidance.profile_id, "config_version": guidance.version})

    def list_calls(self, req: Request) -> Response:
        limit = parse_limit(req.query)
        cursor = decode_cursor(req.query.get("cursor"))
        items, next_key = self.s.timeline.list_calls(limit=limit, cursor=cursor)
        return Response(200, {"calls": [call_row(m) for m in items], "next_cursor": encode_cursor(next_key)})

    def get_call(self, req: Request, session_id: str) -> Response:
        view = self._view(session_id)
        digest = hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode()).hexdigest()
        etag = f'"{digest[:32]}"'
        if req.headers.get("if-none-match") == etag:
            return Response(304, None, {"ETag": etag})
        return Response(200, view, {"ETag": etag})

    def get_handoff(self, req: Request, session_id: str) -> Response:
        return Response(200, build_handoff(self._view(session_id)))

    def get_minutes(self, req: Request, session_id: str) -> Response:
        fmt = req.query.get("format", "json")
        if fmt not in ("json", "md"):
            raise ApiError(400, "invalid_request", "'format' must be 'json' or 'md'")
        view = self._view(session_id)
        if fmt == "json":
            return Response(200, {"session_id": session_id, "call_status": view["status"], **view["minutes"]})
        if view["minutes"]["status"] != "ok":
            raise ApiError(409, "minutes_not_ready", f"Minutes are '{view['minutes']['status']}'")
        return Response(200, minutes_markdown(view), {
            "Content-Type": "text/markdown; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{session_id}-minutes.md"',
        })

    def regenerate_minutes(self, req: Request, session_id: str) -> Response:
        _check_session_id(session_id)
        ack = self._send({"schema": SCHEMA, "type": "session.summarize", "session_id": session_id})
        return Response(202, {"minutes_status": ack.get("minutes_status")})

    def add_turn(self, req: Request, session_id: str) -> Response:
        _check_session_id(session_id)
        body = req.json()
        speaker = body.get("speaker")
        event = {
            "schema": SCHEMA, "type": "turn", "session_id": session_id, "source": "manual",
            "speaker": speaker, "text": body.get("text"), "is_final": True,
            "speaker_name": body.get("speaker_name") or {"ba": "Partner BA", "cx": "Customer"}.get(speaker),
            "spoken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event_id": f"manual-{uuid.uuid4().hex}",
        }
        ack = self._send({k: v for k, v in event.items() if v is not None})
        return Response(202, {"seq": ack.get("seq")})

    def update_call(self, req: Request, session_id: str) -> Response:
        _check_session_id(session_id)
        body = req.json()
        event = {"schema": SCHEMA, "type": "session.update", "session_id": session_id}
        if "status" in body:
            event["status"] = body["status"]
        if "assist_config" in body:
            event["assist_config"] = body["assist_config"]
        ack = self._send(event)
        return Response(200, {"status": ack.get("status"), "assist_config": ack.get("assist_config")})

    def end_call(self, req: Request, session_id: str) -> Response:
        _check_session_id(session_id)
        reason = req.json().get("reason") or "ended by user"
        ack = self._send({"schema": SCHEMA, "type": "session.end", "session_id": session_id, "reason": reason})
        return Response(200, {"status": ack.get("status"), "ended_now": ack.get("ended_now")})

    # --- helpers ------------------------------------------------------------------

    def _view(self, session_id: str) -> dict:
        _check_session_id(session_id)
        view = build_session_view(session_id, timeline=self.s.timeline, store=self.s.store)
        if view is None:
            raise ApiError(404, "call_not_found", "No call with this session_id")
        return view

    def _send(self, event: dict) -> dict:
        try:
            ack = self.s.sender.send(event)
        except Exception:  # noqa: BLE001
            logger.exception("agent unavailable type=%s", event.get("type"))
            raise ApiError(502, "agent_unavailable", "The agent did not accept the request") from None
        if not ack.get("accepted"):
            code = ack.get("code") or "rejected"
            raise ApiError(_ACK_STATUS.get(code, 502), code, ack.get("error") or "Rejected by the agent")
        return ack

    def _send_quietly(self, event: dict) -> None:
        try:
            self.s.sender.send(event)
        except Exception:  # noqa: BLE001
            logger.exception("could not send %s", event.get("type"))

    def _require_bucket(self) -> None:
        if not self.s.bucket:
            raise ApiError(500, "uploads_not_configured", "Upload bucket is not configured")

    def _prepare_job(self, kind: str, source: dict) -> dict:
        """Validate the upload and settings for a recording, transcript or audio call."""
        if kind == "transcript":
            return self.live.transcript_job(source)
        self._require_bucket()
        upload_id = source.get("upload_id")
        if not isinstance(upload_id, str) or not _UPLOAD_ID_RE.match(upload_id):
            raise ApiError(400, "invalid_request", "'source.upload_id' is missing or malformed")
        prefix = _upload_prefix(kind, upload_id)

        if kind == "recording":
            speed = _number(source, "speed", 10, low=0, high=RECORDING_MAX_SPEED, low_inclusive=False)
            max_gap = _number(source, "max_gap_seconds", 8, low=0, high=MAX_GAP_LIMIT, low_inclusive=False)
            key = prefix + RECORDING_FILE
            try:
                parse_conversation(self._read_upload_json(key))
            except InvalidConversationFileError as e:
                raise ApiError(400, "invalid_upload", str(e)) from None
            return {"mode": "recording", "bucket": self.s.bucket, "key": key,
                    "speed": speed, "max_gap_seconds": max_gap}

        speed = _number(source, "speed", 1, low=MIN_SPEED, high=MAX_SPEED)
        limit = source.get("limit_seconds")
        if limit is not None:
            limit = _number(source, "limit_seconds", None, low=0, high=None, low_inclusive=False)
        try:
            parse_manifest(self._read_upload_json(prefix + MANIFEST_FILE),
                           S3Reader(self.s.bucket, prefix, client=self.s.s3))
        except InvalidManifestError as e:
            raise ApiError(400, "invalid_upload", str(e)) from None
        return {"mode": "audio", "bucket": self.s.bucket, "prefix": prefix,
                "speed": speed, "limit_seconds": limit}

    def _read_upload_json(self, key: str) -> Any:
        from botocore.exceptions import ClientError

        try:
            obj = self.s.s3.get_object(Bucket=self.s.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise ApiError(404, "upload_not_found", f"Upload file not found: {key.rsplit('/', 1)[-1]}") from None
            raise
        if obj.get("ContentLength", 0) > MAX_RECORDING_BYTES:
            raise ApiError(400, "invalid_upload", f"File must be at most {MAX_RECORDING_BYTES} bytes")
        try:
            return json.loads(obj["Body"].read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "invalid_upload", "Uploaded file is not valid JSON") from None


# --- module helpers -------------------------------------------------------------

def _check_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.match(session_id):
        raise ApiError(404, "call_not_found", "No call with this session_id")


def _upload_prefix(kind: str, upload_id: str) -> str:
    return f"uploads/{kind}/{upload_id}/"


def _valid_upload_path(path: object) -> bool:
    return isinstance(path, str) and len(path) <= 200 and bool(_UPLOAD_PATH_RE.match(path)) \
        and ".." not in path.split("/")


def _number(source: dict, key: str, default: float | None, *, low: float, high: float | None,
            low_inclusive: bool = True) -> float | None:
    value = source.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, "invalid_request", f"'source.{key}' must be a number")
    if (value < low if low_inclusive else value <= low) or (high is not None and value > high):
        bound = f"{'at least' if low_inclusive else 'above'} {low}" + (f" and at most {high}" if high else "")
        raise ApiError(400, "invalid_request", f"'source.{key}' must be {bound}")
    return float(value)


# --- wiring -------------------------------------------------------------------

class CachedSecret:
    """A Secrets Manager string value, re-read at most every ``ttl`` seconds."""

    def __init__(self, client: Any, secret_id: str, ttl: float = 300) -> None:
        self._client, self._secret_id, self._ttl = client, secret_id, ttl
        self._value: str | None = None
        self._read_at = 0.0

    def __call__(self) -> str | None:
        if self._value is None or time.monotonic() - self._read_at > self._ttl:
            self._value = self._client.get_secret_value(SecretId=self._secret_id)["SecretString"]
            self._read_at = time.monotonic()
        return self._value


def services_from_env(env: dict[str, str]) -> Services:
    """Build services from environment variables (see docs/api.md).

    Local development: set ``AGENT_URL`` to send events to ``python main.py``,
    ``ACCESS_KEY`` to skip Secrets Manager, and leave ``REPLAYER_FUNCTION`` unset to run
    sources in a background thread.
    """
    import boto3
    from botocore.config import Config

    from replayer.clients import DEFAULT_QUALIFIER, LocalSender, RuntimeSender, TimelineStatus

    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    qualifier = env.get("AGENTCORE_QUALIFIER") or DEFAULT_QUALIFIER
    if env.get("AGENT_URL"):
        sender = LocalSender(env["AGENT_URL"])
    else:
        sender = RuntimeSender(env["AGENTCORE_RUNTIME_ARN"], region=region, qualifier=qualifier)

    s3 = boto3.client("s3", region_name=region,
                      config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))

    status = TimelineStatus(env.get("TIMELINE_TABLE_NAME", "ingram-live-assist-timeline"), region=region)

    if env.get("REPLAYER_FUNCTION"):
        lambda_client = boto3.client("lambda", region_name=region)
        runtime_fields = {"runtime_arn": env.get("AGENTCORE_RUNTIME_ARN"), "qualifier": qualifier}

        def start_source(job: dict) -> None:
            lambda_client.invoke(
                FunctionName=env["REPLAYER_FUNCTION"], InvocationType="Event",
                Payload=json.dumps({**job, **runtime_fields}).encode("utf-8"),
            )
    else:
        def start_source(job: dict) -> None:
            import threading

            from replayer.lambda_handler import run_safely

            threading.Thread(
                target=run_safely, args=(job,), kwargs={"sender": sender, "s3": s3, "get_status": status},
                daemon=True, name=f"source-{job['session_id']}",
            ).start()

    if env.get("ACCESS_KEY"):
        key = env["ACCESS_KEY"]
        access_key: Callable[[], str | None] = lambda: key  # noqa: E731
    else:
        access_key = CachedSecret(boto3.client("secretsmanager", region_name=region), env["ACCESS_KEY_SECRET"])

    provider = (env.get("MEETING_PROVIDER") or "vexa").strip().lower()
    if provider not in ("vexa", "recall"):
        raise ValueError("MEETING_PROVIDER must be 'vexa' or 'recall'")
    recall = _recall_from_env(env, region, sender) if provider == "recall" else None

    memory_id = env.get("LONG_TERM_MEMORY_ID")
    accounts = AccountStore(region_name=region)
    intake_table = (env.get("INTAKE_TABLE_NAME") or "").strip()
    return Services(
        accounts=accounts,
        intake_feed=IntakeFeed(intake_table, region_name=region) if intake_table else None,
        intake_links=IntakeLinkStore(accounts, region_name=region) if intake_table else None,
        discovery=DiscoveryClient(env["DISCOVERY_API_URL"].strip(), (env.get("DISCOVERY_UI_URL") or "").strip())
        if (env.get("DISCOVERY_API_URL") or "").strip() else None,
        crm_base_url=(env.get("CRM_BASE_URL") or "").strip(),
        configs=ConfigCatalog(ConfigStore(region_name=region)),
        memory=LongTermMemory(memory_id, region_name=region) if memory_id else None,
        timeline=TimelineStore(region_name=region),
        store=SessionStore(region_name=region),
        sender=sender,
        s3=s3,
        bucket=env.get("UPLOAD_BUCKET"),
        start_source=start_source,
        access_key=access_key,
        direct_upload=env.get("DIRECT_UPLOAD", "").lower() == "true",
        meeting_provider=provider,
        recall=recall,
    )


def _recall_from_env(env: dict[str, str], region: str | None, sender: Any) -> RecallSource | None:
    """The Recall source, or None (logged) when it is not configured, so the rest of the API still works.

    Each value is read from the environment (local development) or from the Secrets Manager secret
    named by ``<NAME>_SECRET`` / ``RECALL_VERIFY_SECRET_NAME`` (deployed).
    """
    import boto3

    def getter(value_var: str, secret_var: str) -> Callable[[], str | None] | None:
        if env.get(value_var):
            value = env[value_var]
            return lambda: value
        if env.get(secret_var):
            return CachedSecret(boto3.client("secretsmanager", region_name=region), env[secret_var])
        return None

    api_key = getter("RECALL_API_KEY", "RECALL_API_KEY_SECRET")
    verify = getter("RECALL_VERIFY_SECRET", "RECALL_VERIFY_SECRET_NAME")
    if api_key is None or verify is None:
        logger.error("MEETING_PROVIDER=recall but the Recall key or verification secret is not set")
        return None
    try:
        settings = RecallSettings.from_env(env, api_key=api_key, webhook_secret=verify)
    except ValueError as e:
        logger.error("MEETING_PROVIDER=recall but the settings are invalid: %s", e)
        return None
    return RecallSource(settings, sender=sender)
