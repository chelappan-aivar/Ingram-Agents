"""Discovery side of the pipeline integration: Intake Agent opportunities and their Live Assist calls as engagements.

    GET  /pipeline/opportunities                   funding-checked opportunities (through Live Assist), call counts
    POST /pipeline/opportunities/{id}/import       writes the opportunity's documents into input/<engagement>/:
                                                   00_intake_record.md plus each ended call's transcript and minutes
                                                   -> {"engagement", "documents": [{name, kind, bytes}], "call_ids"}
    POST /runs {..., "pipeline_opportunity_id"}    (handler.start_run) also registers the run in Live Assist, so the
                                                   report comes back to the opportunity there

Live Assist checks a shared access key. The users do not type it: this Lambda reads it from AWS Secrets Manager
(LIVE_ASSIST_KEY_SECRET) and sends it to Live Assist itself, so anyone who can open the Discovery page sees the
Intake and Live Assist data. The key is cached in memory for a few minutes and never logged or returned. Imported
documents are ordinary input files, so a run reads them together with anything uploaded for the engagement.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import boto3

from overview import clean_context

logger = logging.getLogger()

LIVE_ASSIST_API_URL = os.environ.get("LIVE_ASSIST_API_URL", "").rstrip("/")
LIVE_ASSIST_KEY_SECRET = os.environ.get("LIVE_ASSIST_KEY_SECRET", "")
TIMEOUT = 10
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
KEY_RE = re.compile(r"^[\x21-\x7e]{8,200}$")
DOC_NAME = re.compile(r"^\d{2}_[A-Za-z0-9._-]{1,120}\.(md|txt)$")
MAX_DOC_BYTES = 2 * 1024 * 1024


class PipelineError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def enabled() -> bool:
    # https only; http://127.0.0.1 is allowed for the local test server.
    return LIVE_ASSIST_API_URL.startswith(("https://", "http://127.0.0.1:"))


_key_cache: dict[str, Any] = {"value": "", "at": 0.0}
KEY_TTL_SECONDS = 300
_secrets_client = None


def _secrets():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION") or None)
    return _secrets_client


def server_key() -> str:
    """The Live Assist access key from Secrets Manager (cached for KEY_TTL_SECONDS)."""
    if not enabled() or not LIVE_ASSIST_KEY_SECRET:
        raise PipelineError(404, "The pipeline is not connected (LIVE_ASSIST_API_URL or LIVE_ASSIST_KEY_SECRET is not set).")
    if _key_cache["value"] and time.time() - _key_cache["at"] < KEY_TTL_SECONDS:
        return _key_cache["value"]
    try:
        value = str(_secrets().get_secret_value(SecretId=LIVE_ASSIST_KEY_SECRET).get("SecretString") or "").strip()
    except Exception:   # noqa: BLE001  (never leak the secret id or AWS details to the page)
        logger.error("live assist key could not be read from Secrets Manager")
        raise PipelineError(502, "The Live Assist access key could not be read.") from None
    if not KEY_RE.fullmatch(value):
        raise PipelineError(502, "The Live Assist access key in Secrets Manager is not valid.")
    _key_cache.update(value=value, at=time.time())
    return value


def opportunity_id(value: str) -> str:
    if not GUID.fullmatch(value or ""):
        raise PipelineError(404, "Opportunity not found.")
    return value


def call_live_assist(method: str, path: str, key: str, body: dict | None = None) -> dict[str, Any]:
    """One request to the Live Assist API with the caller's key. Its error messages are passed on, nothing else."""
    if not enabled():
        raise PipelineError(404, "The pipeline is not connected (LIVE_ASSIST_API_URL is not set).")
    req = urllib.request.Request(LIVE_ASSIST_API_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Access-Key": key, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read(MAX_DOC_BYTES * 8) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = (json.loads(exc.read()).get("error") or {}).get("message") or ""
        except (ValueError, AttributeError):
            message = ""
        if exc.code == 401:
            raise PipelineError(401, "Live Assist refused the access key.") from None
        if exc.code == 404:
            raise PipelineError(404, message or "Not found in Live Assist.") from None
        raise PipelineError(502, f"Live Assist answered {exc.code}{': ' + message if message else ''}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise PipelineError(502, "Live Assist could not be reached.") from None


def engagement_name(name: str, customer: str) -> str:
    """The engagement name Live Assist gives an opportunity (its export uses the same rule), so a run is found by it."""
    base = re.sub(r"[^A-Za-z0-9._ -]+", " ", f"{customer} {name}" if customer and customer not in name else name)
    base = re.sub(r"\s+", " ", base).strip(" .-_") or "Opportunity"
    return base[:101].rstrip(" .-_")


def list_opportunities(key: str) -> dict[str, Any]:
    out = call_live_assist("GET", "/intake/opportunities", key)
    fields = ("status", "program", "funding", "total_usd", "arr_usd", "rationale")
    rows = [{"intake_opportunity_id": o["intake_opportunity_id"], "name": o.get("name"), "customer": o.get("customer"),
             "summary": o.get("summary") or "", "seller": o.get("seller"),
             "estimated_value_usd": o.get("estimated_value_usd"), "crm_url": o.get("crm_url"),
             "decision": {k: (o.get("decision") or {}).get(k) for k in fields},
             "checked_at": o.get("checked_at"), "call_count": o.get("call_count", 0), "last_call_at": o.get("last_call_at"),
             "engagement": engagement_name(o.get("name") or "", o.get("customer") or "")}
            for o in out.get("opportunities") or []]
    return {"enabled": bool(out.get("enabled")), "opportunities": rows}


LATEST_RUN_WINDOW = timedelta(minutes=15)   # one Intake run checks its opportunities within seconds of each other


def _checked(opp: dict[str, Any]) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(opp.get("checked_at")).replace("Z", "+00:00"))
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def latest_run_only(pairs: list[tuple[dict[str, Any], Any]]) -> list[tuple[dict[str, Any], Any]]:
    """Opportunities from the newest Intake run, plus older ones that already have a Discovery run or Live Assist
    calls (their work must stay visible). Everything else from earlier runs is a repeat of the latest run."""
    stamps = [t for t in (_checked(o) for o, _ in pairs) if t]
    if not stamps:
        return pairs
    newest = max(stamps)
    return [(o, r) for o, r in pairs
            if r or (o.get("call_count") or 0) or ((t := _checked(o)) is not None and newest - t <= LATEST_RUN_WINDOW)]


def _same_opportunity_key(o: dict[str, Any]) -> str:
    """Customer and name without case or punctuation: 'Tailwind - VMware exit' equals 'Tailwind VMware exit'."""
    text = f"{o.get('customer') or ''} {o.get('name') or ''}".lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def dedupe(pairs: list[tuple[dict[str, Any], Any]]) -> list[tuple[dict[str, Any], Any]]:
    """One entry per opportunity. The same opportunity can be checked again under a new id; keep the one that already
    has a Discovery run, then the one with calls, then the most recent check. Order of first appearance is kept."""
    best: dict[str, tuple[dict[str, Any], Any]] = {}
    for opp, run in pairs:
        key = _same_opportunity_key(opp)
        rank = lambda p: (bool(p[1] and p[1].get("status") == "succeeded"), bool(p[1]), p[0].get("call_count") or 0,  # noqa: E731
                          p[0].get("checked_at") or "")
        if key not in best or rank((opp, run)) > rank(best[key]):
            best[key] = (opp, run)
    return list(best.values())


def export(key: str, intake_id: str) -> dict[str, Any]:
    out = call_live_assist("GET", f"/intake/opportunities/{urllib.parse.quote(opportunity_id(intake_id))}/export", key)
    docs = []
    for d in out.get("documents") or []:
        name, text = str(d.get("name") or ""), str(d.get("text") or "")
        if DOC_NAME.fullmatch(name) and text and len(text.encode()) <= MAX_DOC_BYTES:
            docs.append({"name": name, "kind": d.get("kind"), "text": text})
    return {"engagement": out.get("engagement"), "call_ids": out.get("call_ids") or [], "documents": docs,
            "context": clean_context(out.get("context"))}


def register_run(key: str, intake_id: str, engagement: str, run_id: str, call_ids: list[str]) -> bool:
    """Tell Live Assist about the run. A failure is logged, not raised: the run itself has started."""
    try:
        call_live_assist("POST", f"/intake/opportunities/{urllib.parse.quote(opportunity_id(intake_id))}/discovery", key,
                         {"engagement": engagement, "run_id": run_id, "call_ids": call_ids})
        return True
    except PipelineError as exc:
        logger.warning("pipeline register failed opportunity=%s run=%s status=%s", intake_id, run_id, exc.status)
        return False
