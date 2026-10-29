"""One Intake run: consolidate Outlook, CRM records and seller notes into matched / unmatched opportunities.

    1. read      Outlook threads (if Microsoft is connected), CRM rows and seller notes -> candidates
    2. triage    one short model call per candidate: opportunity? customer, project, contacts
    3. match     one model call over the triage profiles: which candidates are the same opportunity
                 (across sources, and duplicates within a source); falls back to customer-name matching
    4. build     one full intake record per opportunity from all of its documents (fields with source
                 passages, gaps, conflicts between sources, MAP / MPE fit), stored as soon as it is ready

An opportunity is "matched" when its documents come from two or more sources (e.g. an email thread
and a CRM row), "unmatched" when only one source has it; `missing_sources` says where it was not found.
Model calls run in parallel; every DynamoDB write stays on the run's own thread.
"""

import hashlib
import logging
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
from botocore.config import Config

from intake import dynamics_sync
from intake.extractor import extract, match, norm, triage
from intake.mail import fetch_messages
from intake.store import RunStore, now

logger = logging.getLogger("intake.pipeline")

# Calendar responses never carry a requirement; skip them without calling the model.
CALENDAR_REPLY = re.compile(r"^\s*(accepted|declined|tentative|canceled|cancelled)\s*:", re.IGNORECASE)
MODEL_WORKERS = 4
MAX_MATCH_PROFILES = 250
SOURCE_ORDER = {"crm": 0, "email": 1, "notes": 2}   # order of documents inside a combined record
_LEGAL = re.compile(r"\b(inc|ltd|llc|plc|gmbh|ag|sa|pvt|private|limited|corp|corporation|co|company|group|holdings)\b")


def _key(value: str, length: int) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _msg_key(email: dict[str, Any]) -> str:
    return _key(email.get("internet_message_id") or email["id"], 24)


def group_threads(emails: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """thread_key -> emails, newest first (Graph already returns newest first)."""
    threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for email in emails:
        threads[_key(email["conversation_id"], 16)].append(email)
    return dict(threads)


def _bedrock():
    return boto3.client("bedrock-runtime", config=Config(retries={"mode": "adaptive", "max_attempts": 8},
                                                         read_timeout=180))


def _company(name: str) -> str:
    return " ".join(_LEGAL.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", norm(name))).split())


def fallback_groups(profiles: list[dict[str, Any]]) -> list[list[int]]:
    """Used when the match call fails: same normalised customer name -> same group."""
    by_name: dict[str, list[int]] = defaultdict(list)
    groups = []
    for p in profiles:
        name = _company(p.get("customer") or "")
        if name:
            by_name[name].append(p["id"])
        else:
            groups.append([p["id"]])
    return list(by_name.values()) + groups


def _source_entry(c: dict[str, Any]) -> dict[str, Any]:
    d = c["doc"]
    return {"type": c["source"], "subject": d.get("subject"), "from": d.get("from"),
            "received_at": d.get("received_at"), "web_link": d.get("web_link"),
            "file_name": d.get("file_name"), "emails_in_thread": c["thread_size"]}


def run_consolidated(store: RunStore, documents: list[dict[str, str]], submitted_by: str,
                     graph_token: str | None = None, days: int | None = None) -> None:
    model_id = os.environ["BEDROCK_MODEL_ID"]
    bedrock = _bedrock()
    counts = Counter(emails=0, threads=0, crm_rows=0, notes=0, candidates=0, triaged=0, groups=0, built=0,
                     opportunities=0, matched=0, unmatched=0, skipped=0, failed=0)
    store.mark_running()
    store.update_counts(counts, phase="reading")

    # ------------------------------------------------------------ 1. read
    candidates: list[dict[str, Any]] = []

    def decide(c: dict[str, Any], decision: str, reason: str, group_key: str = "") -> None:
        if c["source"] == "email":
            for email in c["thread"]:
                store.put_email(email, _msg_key(email), group_key or c["key"], decision, reason)
        else:
            store.put_document(c["key"], c["doc"], decision, reason, group_key)

    included = []
    if graph_token:
        max_emails = int(os.environ.get("MAX_EMAILS", "300"))
        try:
            emails, truncated = fetch_messages(graph_token, days, max_emails)
        except Exception as exc:
            if not documents:
                raise
            # Keep going with the CRM records and seller notes; the UI shows the Outlook error.
            logger.exception("Outlook read failed")
            store.log("read", f"Could not read Outlook, continuing with the other sources: {exc}", level="ERROR")
            store.update_counts(counts, outlook_error=str(exc)[:300])
            emails, truncated = None, False
        threads = group_threads(emails or [])
        if emails is not None:
            counts.update(emails=len(emails), threads=len(threads))
            store.update_counts(counts, truncated=truncated)
            store.log("read", f"Outlook: {len(emails)} emails ({len(threads)} threads) from the last {days} day(s)"
                              + (f"; stopped at the {max_emails}-email limit" if truncated else ""))
            included.append("email")
        for thread_key, thread in threads.items():
            c = {"key": thread_key, "source": "email", "doc": thread[0], "thread_size": len(thread), "thread": thread}
            if CALENDAR_REPLY.match(thread[0]["subject"]):
                decide(c, "skipped", "Calendar response")
                counts["skipped"] += 1
            else:
                candidates.append(c)

    received = now()   # one timestamp for uploads, so they keep their upload order
    for i, d in enumerate(documents):
        key = f"{d['source']}-{i:03d}"
        candidates.append({"key": key, "source": d["source"], "thread_size": 1, "doc": {
            "id": f"{key}-{store.run_id}", "subject": d["title"] or d["file_name"] or f"Record {i + 1}",
            "from": submitted_by, "to": [], "cc": [], "received_at": received, "file_name": d["file_name"],
            "has_attachments": False, "body": d["text"], "web_link": None}})
        counts["crm_rows" if d["source"] == "crm" else "notes"] += 1
    for source, label in (("crm", "CRM records"), ("notes", "seller notes")):
        n = counts["crm_rows" if source == "crm" else "notes"]
        if n:
            included.append(source)
            store.log("read", f"{n} {label} from {len({d['file_name'] for d in documents if d['source'] == source})} input(s)")
    counts["candidates"] = len(candidates)
    store.update_counts(counts, phase="triage", sources_included=included)

    # ------------------------------------------------------------ 2. triage
    opps: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        futures = {pool.submit(triage, c["doc"], bedrock, model_id, c["source"], c["thread_size"]): c
                   for c in candidates}
        for future in as_completed(futures):
            c = futures[future]
            counts["triaged"] += 1
            try:
                t = future.result()
            except Exception as exc:
                logger.exception("triage %s failed", c["key"])
                store.log("triage", f"'{c['doc']['subject'][:120]}' failed: {exc}", level="ERROR")
                decide(c, "failed", str(exc))
                counts["failed"] += 1
            else:
                if t.get("is_opportunity"):
                    c["triage"] = t
                    opps.append(c)
                else:
                    decide(c, "skipped", t.get("reason", ""))
                    counts["skipped"] += 1
            if counts["triaged"] % 4 == 0 or counts["triaged"] == len(candidates):
                store.update_counts(counts)
    opps.sort(key=lambda c: (SOURCE_ORDER[c["source"]], c["key"]))
    store.log("triage", f"{len(opps)} of {len(candidates)} records look like opportunities")

    # ------------------------------------------------------------ 3. match
    store.update_counts(counts, phase="matching")
    profiles = [{"id": i, "source": c["source"], "customer": c["triage"].get("customer_name", ""),
                 "opportunity": c["triage"].get("opportunity", ""), "need": c["triage"].get("need", ""),
                 "contacts": c["triage"].get("contacts", []), "date": (c["doc"].get("received_at") or "")[:10]
                 if c["source"] == "email" else ""} for i, c in enumerate(opps)]
    if len(opps) < 2:
        groups = [[0]] if opps else []
    elif len(opps) > MAX_MATCH_PROFILES:
        groups = fallback_groups(profiles)
        store.log("match", f"{len(opps)} opportunities: matched by customer name only", level="WARN")
    else:
        try:
            groups = match(profiles, bedrock, model_id)
        except Exception as exc:
            logger.exception("match failed")
            store.log("match", f"Matching call failed ({exc}); matched by customer name instead", level="WARN")
            groups = fallback_groups(profiles)
    groups = [sorted(g) for g in groups]
    counts["groups"] = len(groups)
    linked = sum(1 for g in groups if len(g) > 1)
    store.log("match", f"{len(opps)} records -> {len(groups)} opportunities ({linked} combine several records)")
    store.update_counts(counts, phase="building")

    # ------------------------------------------------------------ 4. build one record per opportunity
    with ThreadPoolExecutor(max_workers=MODEL_WORKERS) as pool:
        futures = {}
        for g in groups:
            members = [opps[i] for i in g]
            futures[pool.submit(extract, [{"source": m["source"], "doc": m["doc"], "thread_size": m["thread_size"]}
                                          for m in members], bedrock, model_id)] = members
        for future in as_completed(futures):
            members = futures[future]
            group_key = _key("|".join(m["key"] for m in members), 16)
            counts["built"] += 1
            try:
                intake, texts = future.result()
            except Exception as exc:
                logger.exception("build %s failed", group_key)
                store.log("build", f"'{members[0]['doc']['subject'][:120]}' failed: {exc}", level="ERROR")
                for m in members:
                    decide(m, "failed", str(exc), group_key)
                counts["failed"] += 1
                store.update_counts(counts)
                continue
            if not intake.get("is_opportunity"):
                for m in members:
                    decide(m, "skipped", intake.get("summary", ""), group_key)
                counts["skipped"] += len(members)
                store.update_counts(counts)
                continue

            # Which document each passage came from (the passage was verified against the combined text).
            normed = [norm(t) for t in texts]
            for f in intake["fields"]:
                p = norm(f["source"]).strip(" .\"'")
                hit = next((i for i, t in enumerate(normed) if len(p) >= 3 and p in t), None)
                if hit is not None:
                    f["doc"], f["from"] = hit, members[hit]["source"]
            source_types = sorted({m["source"] for m in members}, key=SOURCE_ORDER.get)
            is_matched = len(source_types) >= 2
            sender = next((m["doc"].get("from", "") for m in members if m["source"] == "email"), "")
            crm = dynamics_sync.sync(intake, sender)   # a Dynamics record, so the funding check can run on it
            store.log("dynamics", f"'{intake.get('opportunity_title') or group_key}': {crm['action']}"
                      + (f" ({crm['reason']})" if crm.get("reason") else ""),
                      level="WARN" if crm["action"] == "failed" else "INFO")
            store.put_consolidated(
                group_key, intake, sources=[_source_entry(m) for m in members], source_types=source_types,
                matched=is_matched, missing_sources=[s for s in included if s not in source_types], dynamics=crm)
            for m in members:
                decide(m, "opportunity", intake.get("summary", ""), group_key)
            counts["opportunities"] += 1
            counts["matched" if is_matched else "unmatched"] += 1
            store.update_counts(counts)

    store.finish("COMPLETED_WITH_ERRORS" if counts["failed"] else "COMPLETED", counts)
