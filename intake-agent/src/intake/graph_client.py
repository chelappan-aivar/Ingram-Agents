"""Minimal Microsoft Graph client using the OAuth2 client-credentials flow."""

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import msal
import requests

from intake.config import GraphConfig

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class GraphError(RuntimeError):
    pass


class GraphClient:
    def __init__(self, config: GraphConfig, timeout: int = 30) -> None:
        self._timeout = timeout
        self._app = msal.ConfidentialClientApplication(
            client_id=config.client_id,
            client_credential=config.client_secret,
            authority=config.authority,
        )
        self._session = requests.Session()

    def token(self) -> str:
        # MSAL caches app tokens in memory and refreshes them when they expire.
        result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in result:
            raise GraphError(
                f"Token request failed: {result.get('error')}: {result.get('error_description')}"
            )
        return result["access_token"]

    def mailbox_path(self, mailbox_user: str) -> str:
        return f"/users/{quote(mailbox_user, safe='')}"

    def get(self, url: str, params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None, max_retries: int = 3) -> dict[str, Any]:
        if not url.startswith("http"):
            url = f"{GRAPH_BASE}{url}"
        for attempt in range(max_retries + 1):
            resp = self._session.get(
                url, params=params, timeout=self._timeout,
                headers={"Authorization": f"Bearer {self.token()}", **(headers or {})},
            )
            # Throttling / transient errors: honor Retry-After, then retry.
            if resp.status_code in (429, 503, 504) and attempt < max_retries:
                time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
                continue
            if not resp.ok:
                try:
                    err = resp.json().get("error", {})
                    detail = f"{err.get('code')}: {err.get('message')}"
                except ValueError:
                    detail = resp.text[:500]
                raise GraphError(f"GET {url.split('?')[0]} -> {resp.status_code} {detail}")
            return resp.json()
        raise GraphError(f"GET {url} failed after {max_retries} retries")

    def get_paged(self, url: str, params: dict[str, Any] | None = None,
                  headers: dict[str, str] | None = None) -> Iterator[dict[str, Any]]:
        """Yield every item across @odata.nextLink pages."""
        page = self.get(url, params=params, headers=headers)
        while True:
            yield from page.get("value", [])
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return
            page = self.get(next_link, headers=headers)  # nextLink carries the query string


DELEGATED_SCOPES = ["Mail.Read", "User.Read"]


class DelegatedGraphClient(GraphClient):
    """Signs in as a user with the device-code flow and reads that user's own mailbox (/me).

    Device code is a public-client flow: Entra ignores the client secret for it, so the
    app registration needs Authentication > "Allow public client flows" = Yes, plus
    delegated Graph permissions Mail.Read and User.Read.

    MSAL's token cache (incl. refresh token) is saved to `cache_path` (chmod 600), so you
    sign in once and later runs refresh silently.
    """

    def __init__(self, config: GraphConfig, cache_path: Path, timeout: int = 30) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self._cache.deserialize(cache_path.read_text())
        self._app = msal.PublicClientApplication(
            client_id=config.client_id, authority=config.authority, token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        if self._cache.has_state_changed:
            self._cache_path.write_text(self._cache.serialize())
            os.chmod(self._cache_path, 0o600)  # contains a refresh token

    def token(self) -> str:
        accounts = self._app.get_accounts()
        result = self._app.acquire_token_silent(DELEGATED_SCOPES, account=accounts[0]) if accounts else None
        if not result:
            flow = self._app.initiate_device_flow(scopes=DELEGATED_SCOPES)
            if "user_code" not in flow:
                raise GraphError(f"Device flow failed to start: {flow.get('error')}: {flow.get('error_description')}")
            print(flow["message"], flush=True)
            result = self._app.acquire_token_by_device_flow(flow)
        self._save_cache()
        if "access_token" not in result:
            hint = ""
            if "AADSTS7000218" in str(result.get("error_description")):
                hint = ('\nFix: Entra admin center > App registrations > Ingram Intake > Authentication > '
                        '"Allow public client flows" = Yes > Save, then run again.')
            raise GraphError(f"Sign-in failed: {result.get('error')}: {result.get('error_description')}{hint}")
        return result["access_token"]

    def mailbox_path(self, mailbox_user: str) -> str:
        return "/me"  # delegated tokens can only read the signed-in user's mailbox
