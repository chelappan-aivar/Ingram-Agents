"""Live Assist side of the Discovery agent integration.

Two directions, both keyed by the Intake opportunity id:

* To Discovery: ``export_documents`` turns an opportunity into documents the Discovery agent reads as-is:
  the Intake funding record (Markdown) and, per call, a transcript in Discovery's format (``Name`` line, ``m:ss`` line,
  text, so Discovery recognises speakers and verifies quotes) plus the call minutes. Live Assist only builds them; the
  Discovery API fetches them with the caller's access key and puts them in its own input folder.
* From Discovery: a Discovery run started for the opportunity is linked here (``intake#<id>`` / ``discovery#<run>``),
  and ``DiscoveryClient`` reads its status and ``discovery.json`` through the Discovery API (open, no key) to show a
  short summary and use its open questions as the next call's agenda. Summaries of finished runs are cached on the
  link item, so the report is downloaded once.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT = 8
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_ITEMS = 8
MAX_SUMMARY_PARAGRAPHS = 3
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,100}$")   # Discovery's engagement name / run id rule
TERMINAL = {"succeeded", "failed"}


class DiscoveryUnavailable(Exception):
    pass


def engagement_name(name: str, customer: str) -> str:
    """A Discovery engagement name for an opportunity (letters, digits, space, . _ -; up to 101 characters)."""
    base = re.sub(r"[^A-Za-z0-9._ -]+", " ", f"{customer} {name}" if customer and customer not in name else name)
    base = re.sub(r"\s+", " ", base).strip(" .-_") or "Opportunity"
    return base[:101].rstrip(" .-_")


def _mmss(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")) if iso else None
    except ValueError:
        return None


def transcript_text(view: dict[str, Any]) -> str:
    """The call's turns in Discovery's transcript format, with times relative to the first turn."""
    turns = view.get("turns") or []
    start = next((t for t in (_parse(x.get("spoken_at") or x.get("received_at")) for x in turns) if t), None)
    lines = []
    for i, turn in enumerate(turns):
        name = (turn.get("speaker_name") or ("Partner" if turn.get("speaker") == "ba" else "Customer")).strip()
        at = _parse(turn.get("spoken_at") or turn.get("received_at"))
        offset = (at - start).total_seconds() if at and start else i * 15
        text = " ".join(str(turn.get("text") or "").split())
        if text:
            lines += [name, _mmss(offset), text]
    return "\n".join(lines)


def minutes_text(view: dict[str, Any]) -> str:
    minutes = (view.get("minutes") or {}).get("minutes") or {}
    if not minutes:
        return ""
    out = [f"# Minutes: {view.get('title') or 'Call'}", f"Date: {(view.get('created_at') or '')[:10]}", ""]
    if minutes.get("summary"):
        out += ["## Summary", str(minutes["summary"]), ""]
    for key, label in (("key_points", "Key points"), ("decisions", "Decisions"), ("action_items", "Action items"),
                       ("risks", "Risks"), ("open_questions", "Open questions"), ("next_steps", "Next steps")):
        items = minutes.get(key) or []
        if items:
            out.append(f"## {label}")
            for item in items:
                text = item.get("text") or item.get("item") or item.get("description") if isinstance(item, dict) else item
                owner = f" (owner: {item['owner']})" if isinstance(item, dict) and item.get("owner") else ""
                if text:
                    out.append(f"- {text}{owner}")
            out.append("")
    return "\n".join(out).strip()


def intake_record_text(row: dict[str, Any]) -> str:
    d = row["decision"]
    out = [f"# Intake record: {row['name']}", f"Customer: {row.get('customer') or 'unknown'}",
           f"Funding check: {row.get('checked_at') or ''}", "",
           "## AWS funding decision", f"Status: {d.get('status')}", f"Program: {d.get('program') or 'none'}",
           f"Funding: {d.get('funding') or ''}"]
    if d.get("total_usd"):
        out.append(f"Estimated funding: {round(d['total_usd']):,} USD")
    if d.get("rationale"):
        out.append(f"Reason: {d['rationale']}")
    if d.get("path"):
        out += ["", "## Decision path"] + [f"- {p['step']}: {p['value']}" for p in d["path"]]
    if row.get("facts"):
        out += ["", "## Facts used"] + [f"- {f['label']}: {f['value']}" for f in row["facts"]]
    if d.get("missing"):
        out += ["", "## Still to confirm with the customer"] + [f"- {q}" for q in d["missing"]]
    if row.get("summary"):
        out += ["", "## Summary", row["summary"]]
    return "\n".join(out)


def export_documents(row: dict[str, Any], views: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Documents for the Discovery agent, oldest first (it reads them in file-name order)."""
    docs = [{"name": "00_intake_record.md", "kind": "intake", "text": intake_record_text(row)}]
    for n, view in enumerate(sorted(views, key=lambda v: v.get("created_at") or ""), start=1):
        day = (view.get("created_at") or "")[:10] or "call"
        slug = re.sub(r"[^a-z0-9]+", "_", str(view.get("title") or "call").lower()).strip("_")[:40] or "call"
        transcript = transcript_text(view)
        if transcript:
            docs.append({"name": f"{n:02d}_{day}_{slug}_transcript.txt", "kind": "transcript", "text": transcript,
                         "session_id": view["session_id"]})
        minutes = minutes_text(view)
        if minutes:
            docs.append({"name": f"{n:02d}_{day}_{slug}_minutes.md", "kind": "minutes", "text": minutes,
                         "session_id": view["session_id"]})
    return docs


def summarise_report(report: dict[str, Any], narrative: dict[str, Any] | None = None) -> dict[str, Any]:
    """The parts of the Discovery report Live Assist shows and uses (no quotes, nothing large)."""
    use_cases = sorted(report.get("use_cases") or [], key=lambda u: u.get("rank") or 99)
    questions = [q for q in report.get("gaps_and_open_questions") or [] if (q.get("status") or "open") == "open"]
    pain_points = [p for p in (report.get("what_we_heard") or {}).get("pain_points") or [] if p.get("text")]
    paragraphs = [str(p) for p in (narrative or {}).get("executive_summary") or [] if p][:MAX_SUMMARY_PARAGRAPHS]
    return {
        "generated_at": (report.get("meta") or {}).get("generated_at"),
        "executive_summary": paragraphs,
        "use_case_count": len(use_cases),
        "use_cases": [{"id": u.get("id"), "title": u.get("title"), "category": u.get("category"),
                       "confidence": u.get("confidence")} for u in use_cases[:MAX_SUMMARY_ITEMS]],
        "hidden_opportunities": [{"title": h.get("title"), "why_it_matters": h.get("why_it_matters")}
                                 for h in (report.get("top_hidden_opportunities") or [])[:MAX_SUMMARY_ITEMS]],
        "pain_points": [p["text"] for p in pain_points[:MAX_SUMMARY_ITEMS]],
        "open_question_count": len(questions),
        "open_questions": [q.get("question") for q in questions[:MAX_SUMMARY_ITEMS] if q.get("question")],
    }


class DiscoveryClient:
    """Reads Discovery runs through its HTTP API (``DISCOVERY_API_URL``; the API is open)."""

    def __init__(self, api_url: str, ui_url: str = "") -> None:
        if not api_url.startswith("https://") and not api_url.startswith("http://127.0.0.1"):
            raise ValueError("DISCOVERY_API_URL must be https")
        self.api_url, self.ui_url = api_url.rstrip("/"), ui_url.rstrip("/")

    def _get(self, path: str) -> Any:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.api_url + path), timeout=TIMEOUT) as resp:
                return json.loads(resp.read(MAX_REPORT_BYTES))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DiscoveryUnavailable(str(exc)[:200]) from None

    def status(self, engagement: str, run_id: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"engagement": engagement})
        doc = self._get(f"/runs/{urllib.parse.quote(run_id)}?{q}")
        return {"state": doc.get("state"), "steps_done": len(doc.get("completed_steps") or []),
                "error": doc.get("error"), "updated_at": doc.get("updated_at")}

    def report(self, engagement: str, run_id: str) -> dict[str, Any]:
        """The summary of a finished run. One call to GET /runs/{id}/report; older Discovery APIs without that
        route are read through the presigned discovery.json link instead (no executive summary then)."""
        q = urllib.parse.urlencode({"engagement": engagement})
        rid = urllib.parse.quote(run_id)
        try:
            data = self._get(f"/runs/{rid}/report?{q}") or {}
            if isinstance(data.get("report"), dict):
                return summarise_report(data["report"], data.get("narrative"))
        except DiscoveryUnavailable:
            logger.info("report route unavailable for run=%s; using the results link", run_id)
        urls = (self._get(f"/runs/{rid}/results?{q}") or {}).get("urls") or {}
        url = urls.get("discovery.json")
        if not url or not url.startswith("https://"):
            raise DiscoveryUnavailable("discovery.json is not available")
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:   # presigned S3 GET, 5 minutes
                return summarise_report(json.loads(resp.read(MAX_REPORT_BYTES)))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DiscoveryUnavailable(str(exc)[:200]) from None

    def run_url(self, engagement: str, run_id: str) -> str | None:
        if not self.ui_url:
            return None
        return f"{self.ui_url}/#run={urllib.parse.quote(engagement)}/{urllib.parse.quote(run_id)}"


def valid_run(engagement: object, run_id: object) -> bool:
    return isinstance(engagement, str) and isinstance(run_id, str) and bool(SAFE_NAME.fullmatch(engagement)) \
        and bool(SAFE_NAME.fullmatch(run_id))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
