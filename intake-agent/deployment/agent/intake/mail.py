"""Read the signed-in user's Inbox through Microsoft Graph."""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

GRAPH_MESSAGES = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
SELECT_FIELDS = (
    "id,conversationId,internetMessageId,receivedDateTime,subject,from,"
    "toRecipients,ccRecipients,hasAttachments,importance,body,webLink"
)


class GraphError(RuntimeError):
    pass


def _addr(recipient: dict[str, Any] | None) -> str:
    email = (recipient or {}).get("emailAddress", {})
    name, address = email.get("name", ""), email.get("address", "")
    return f"{name} <{address}>" if name and name != address else address


def _normalize(msg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversationId") or msg["id"],
        "internet_message_id": msg.get("internetMessageId"),
        "received_at": msg.get("receivedDateTime"),
        "subject": msg.get("subject") or "",
        "from": _addr(msg.get("from")),
        "to": [_addr(r) for r in msg.get("toRecipients", [])],
        "cc": [_addr(r) for r in msg.get("ccRecipients", [])],
        "has_attachments": msg.get("hasAttachments", False),
        "importance": msg.get("importance"),
        "body": (msg.get("body") or {}).get("content", ""),
        "web_link": msg.get("webLink"),
    }


def _get(session: requests.Session, url: str, params: dict | None, max_retries: int = 4) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code in (429, 503, 504) and attempt < max_retries:
            time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
            continue
        if not resp.ok:
            try:
                err = resp.json().get("error", {})
                detail = f"{err.get('code')}: {err.get('message')}"
            except ValueError:
                detail = resp.text[:300]
            raise GraphError(f"Graph {resp.status_code} {detail}")
        return resp.json()
    raise GraphError("Graph request kept being throttled")


def fetch_messages(graph_token: str, days: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Return (messages newest first, truncated) for the last `days` days, capped at `limit`."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {graph_token}",
        # Plain text is cheaper and easier for the model than HTML.
        "Prefer": 'outlook.body-content-type="text"',
    })
    params: dict | None = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$select": SELECT_FIELDS,
        "$top": "50",
    }
    url, messages = GRAPH_MESSAGES, []
    while url:
        page = _get(session, url, params)
        for msg in page.get("value", []):
            if len(messages) >= limit:
                return messages, True
            messages.append(_normalize(msg))
        url, params = page.get("@odata.nextLink"), None  # nextLink already has the query string
    return messages, False
