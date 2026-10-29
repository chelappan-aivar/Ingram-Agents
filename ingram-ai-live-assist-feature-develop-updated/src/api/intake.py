"""Intake Agent opportunities in Live Assist (see docs/api.md, "Intake opportunities").

    GET  /intake/opportunities                    funding-checked opportunities from the Intake Agent, with call counts
    GET  /intake/opportunities/{id}               one: decision path, open questions, facts, and its call history
    POST /intake/opportunities/{id}/link          the Live Assist account/opportunity to start a call with
    POST /intake/opportunities/{id}/calls         {"session_id"}: add a started call to the call history map
    GET  /intake/opportunities/{id}/calls         the call history map, with each call's current status
    GET  /intake/opportunities/{id}/export        documents for the Discovery agent: Intake record + ended calls'
                                                  transcripts and minutes (the Discovery API calls this with the key)
    POST /intake/opportunities/{id}/discovery     {"engagement", "run_id", "call_ids"?}: a Discovery run for it
    GET  /intake/opportunities/{id}/discovery     its Discovery runs: live status, report summary, open questions

The Intake data is read-only here. Everything is off (``enabled: false``) when INTAKE_TABLE_NAME is not set.
TODO(auth): like the account routes, any holder of the shared access key sees every opportunity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from api.http import ApiError, Request, Response
from app.discovery_link import (
    TERMINAL,
    DiscoveryClient,
    DiscoveryUnavailable,
    engagement_name,
    export_documents,
    now_iso,
    valid_run,
)
from app.discovery_context import build_context
from app.events import SESSION_ID_RE
from app.intake_feed import IntakeFeed, valid_intake_id
from app.intake_link_store import IntakeLinkStore
from app.timeline_store import TimelineStore

INTAKE_ID = r"(?P<intake_id>[0-9a-fA-F-]{36})"
logger = logging.getLogger(__name__)


class IntakeRoutes:
    def __init__(self, *, feed: IntakeFeed | None, links: IntakeLinkStore | None, timeline: TimelineStore,
                 view: Callable[[str], dict] | None = None, discovery: DiscoveryClient | None = None,
                 crm_base_url: str = "") -> None:
        self.feed, self.links, self.timeline = feed, links, timeline
        self.view, self.discovery, self.crm_base_url = view, discovery, crm_base_url

    def routes(self) -> list[tuple[str, str, Callable]]:
        base = rf"/intake/opportunities/{INTAKE_ID}"
        return [
            ("GET", r"/intake/opportunities", self.list_opportunities),
            ("GET", base, self.get_opportunity),
            ("POST", base + "/link", self.link),
            ("POST", base + "/calls", self.record_call),
            ("GET", base + "/calls", self.list_calls),
            ("GET", base + "/export", self.export),
            ("POST", base + "/discovery", self.add_discovery),
            ("GET", base + "/discovery", self.list_discovery),
        ]

    def list_opportunities(self, req: Request) -> Response:
        if self.feed is None or self.links is None:
            return Response(200, {"enabled": False, "opportunities": []})
        rows = self._read(self.feed.list_checked)
        links = self.links.get_links([r["intake_opportunity_id"] for r in rows])
        for r in rows:
            link = links.get(r["intake_opportunity_id"]) or {}
            r.update(call_count=link.get("call_count", 0), last_call_at=link.get("last_call_at"))
        return Response(200, {"enabled": True, "discovery_ui_url": self.discovery.ui_url if self.discovery else None,
                              "opportunities": rows})

    def get_opportunity(self, req: Request, intake_id: str) -> Response:
        row = self._require(intake_id)
        link = self.links.get_link(intake_id)
        return Response(200, {**row, "link": link, "calls": self._calls(intake_id)})

    def link(self, req: Request, intake_id: str) -> Response:
        row = self._require(intake_id)
        link = self.links.ensure_link(intake_id, row["name"], row["customer"])
        return Response(200, {"account_id": link["account_id"], "opportunity_id": link["opportunity_id"],
                              "intake_opportunity_id": intake_id})

    def record_call(self, req: Request, intake_id: str) -> Response:
        row = self._require(intake_id)
        session_id = req.json().get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
            raise ApiError(400, "invalid_request", "'session_id' must be a call id")
        link = self.links.get_link(intake_id)
        meta = self.timeline.get_meta(session_id)
        if meta is None:
            raise ApiError(404, "call_not_found", "No call with this id")
        # Only a call started for this opportunity's Live Assist account/opportunity can join its history.
        if link is None or meta.get("opportunity_id") != link["opportunity_id"]:
            raise ApiError(409, "call_not_linked", "Start the call from this opportunity first (link it)")
        entry = self.links.record_call(intake_id, meta, row["decision"])
        logger.info("intake call recorded intake=%s session=%r", intake_id, session_id)
        return Response(201, entry)

    def list_calls(self, req: Request, intake_id: str) -> Response:
        self._require(intake_id)
        return Response(200, {"calls": self._calls(intake_id)})

    # --- Discovery agent ------------------------------------------------------------------

    def export(self, req: Request, intake_id: str) -> Response:
        """What the Discovery agent reads for this opportunity. Only ended calls: a live call is still changing."""
        row = self._require(intake_id)
        views = []
        for c in self.links.list_calls(intake_id):
            try:
                view = self.view(c["session_id"]) if self.view else None
            except ApiError:
                view = None   # expired (past the retention period)
            if view and view.get("status") == "ended" and view.get("turns"):
                views.append(view)
        docs = export_documents(row, views)
        return Response(200, {"intake_opportunity_id": intake_id,
                              "engagement": engagement_name(row["name"], row["customer"]),
                              "name": row["name"], "customer": row["customer"],
                              "call_ids": [v["session_id"] for v in views], "documents": docs,
                              "context": build_context(row, views, docs, self.crm_base_url)})

    def add_discovery(self, req: Request, intake_id: str) -> Response:
        self._require(intake_id)
        body = req.json()
        engagement, run_id = body.get("engagement"), body.get("run_id")
        if not valid_run(engagement, run_id):
            raise ApiError(400, "invalid_request", "'engagement' and 'run_id' must be a Discovery engagement and run id")
        call_ids = [c for c in body.get("call_ids") or [] if isinstance(c, str) and SESSION_ID_RE.match(c)]
        run = self.links.add_discovery_run(intake_id, engagement, run_id, now_iso(), call_ids)
        logger.info("discovery run linked intake=%s run=%s calls=%d", intake_id, run_id, len(call_ids))
        return Response(201, run)

    def list_discovery(self, req: Request, intake_id: str) -> Response:
        self._require(intake_id)
        runs = self.links.list_discovery_runs(intake_id)
        for run in runs[:3]:   # refresh only the newest few; older finished runs are cached
            self._refresh(intake_id, run)
        for run in runs:
            run.pop("sk", None)
            run["url"] = self.discovery.run_url(run["engagement"], run["run_id"]) if self.discovery else None
        return Response(200, {"enabled": self.discovery is not None, "runs": runs})

    def _refresh(self, intake_id: str, run: dict) -> None:
        if self.discovery is None or (run.get("state") in TERMINAL and (run.get("summary") or run["state"] == "failed")):
            return
        try:
            status = self.discovery.status(run["engagement"], run["run_id"])
            fields = {"state": status["state"], "steps_done": status["steps_done"], "error": status["error"]}
            if status["state"] == "succeeded":
                fields.update(summary=self.discovery.report(run["engagement"], run["run_id"]),
                              finished_at=status["updated_at"])
            elif status["state"] == "failed":
                fields["finished_at"] = status["updated_at"]
        except DiscoveryUnavailable as exc:
            logger.warning("discovery read failed run=%s: %s", run["run_id"], exc)
            run["stale"] = True
            return
        self.links.update_discovery_run(intake_id, run["sk"], **fields)
        run.update(fields)

    # --- helpers ------------------------------------------------------------------------

    def _calls(self, intake_id: str) -> list[dict]:
        calls = self.links.list_calls(intake_id)
        for c in calls:   # current status from the call itself (it may have ended since)
            meta = self.timeline.get_meta(c["session_id"]) or {}
            c.update(status=meta.get("status", "expired"), ended_at=meta.get("ended_at"),
                     turn_count=int(meta.get("next_seq") or 0))
        return calls

    def _require(self, intake_id: str) -> dict:
        if self.feed is None or self.links is None:
            raise ApiError(404, "intake_not_configured", "Intake opportunities are not set up on this server")
        if not valid_intake_id(intake_id):
            raise ApiError(404, "intake_opportunity_not_found", "No such Intake opportunity")
        row = self._read(lambda: self.feed.get(intake_id))
        if row is None:
            raise ApiError(404, "intake_opportunity_not_found", "No such Intake opportunity, or it has no funding check")
        return row

    @staticmethod
    def _read(fn):
        try:
            return fn()
        except ApiError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("intake read failed")
            raise ApiError(502, "intake_unavailable", "The Intake Agent data could not be read") from None
