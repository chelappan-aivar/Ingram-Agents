"""Fetch recent Outlook messages for a mailbox via Microsoft Graph."""

from datetime import datetime, timedelta, timezone
from itertools import islice
from typing import Any

from intake.graph_client import GraphClient

SELECT_FIELDS = [
    "id", "conversationId", "internetMessageId", "receivedDateTime", "subject",
    "from", "toRecipients", "ccRecipients", "hasAttachments", "importance",
    "isRead", "bodyPreview", "body", "webLink",
]


def _addr(recipient: dict[str, Any] | None) -> str:
    email = (recipient or {}).get("emailAddress", {})
    name, address = email.get("name", ""), email.get("address", "")
    return f"{name} <{address}>" if name and name != address else address


def normalize(msg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Graph message into the shape the Intake agent consumes."""
    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversationId"),
        "internet_message_id": msg.get("internetMessageId"),
        "received_at": msg.get("receivedDateTime"),
        "subject": msg.get("subject") or "",
        "from": _addr(msg.get("from")),
        "to": [_addr(r) for r in msg.get("toRecipients", [])],
        "cc": [_addr(r) for r in msg.get("ccRecipients", [])],
        "has_attachments": msg.get("hasAttachments", False),
        "importance": msg.get("importance"),
        "is_read": msg.get("isRead"),
        "preview": msg.get("bodyPreview", ""),
        "body": (msg.get("body") or {}).get("content", ""),
        "web_link": msg.get("webLink"),
    }


def fetch_recent_messages(client: GraphClient, mailbox_user: str, hours: int = 24,
                          folder: str | None = "inbox", limit: int | None = None) -> list[dict[str, Any]]:
    """Return messages received in the last `hours` hours, newest first.

    folder: well-known folder name ("inbox", "sentitems", ...) or None for all folders.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = client.mailbox_path(mailbox_user)
    path = f"{base}/mailFolders/{folder}/messages" if folder else f"{base}/messages"
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$select": ",".join(SELECT_FIELDS),
        "$top": "50",
    }
    # Plain-text bodies are easier for the LLM downstream than HTML.
    headers = {"Prefer": 'outlook.body-content-type="text"'}
    pages = client.get_paged(path, params=params, headers=headers)
    return [normalize(m) for m in islice(pages, limit)]
