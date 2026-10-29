"""Graph configuration loaded from environment variables (or a local .env file)."""

import os
from dataclasses import dataclass

REQUIRED_KEYS = ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "MAILBOX_USER"]


@dataclass(frozen=True)
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox_user: str  # UPN or object ID of the mailbox to read (app-only auth can't use /me)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


def load_config() -> GraphConfig:
    from dotenv import load_dotenv

    load_dotenv()
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return GraphConfig(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        mailbox_user=os.environ["MAILBOX_USER"],
    )
