"""Local dev server: the API and the console, for trying Live Assist on your machine.

    python main.py                                   # agent on :8080
    ACCESS_KEY=dev python -m api.local               # console and API on http://127.0.0.1:8090

Open http://127.0.0.1:8090 for the console. The API is also served at the root, as before
(``curl -H 'X-Access-Key: dev' http://127.0.0.1:8090/options``).

Uses the same handlers as Lambda. Events go to ``AGENT_URL`` (default http://localhost:8080);
DynamoDB and S3 use the AWS credentials in the environment. Recording and audio sources run in
a background thread instead of Lambda. ``scripts/dev_up.sh`` starts all of it.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from api.app import ACCESS_HEADER, Api, Request, services_from_env  # noqa: E402
from api.console_server import (  # noqa: E402
    MAX_UPLOAD_BYTES,
    SECURITY_HEADERS,
    allowed_upload_target,
    BoundedBody,
    same_origin,
    static_file,
)

API_PREFIX = "/api"


def build_server(api: Api, *, key: str | None, port: int, bucket: str | None = None,
                 local_s3_hosts: set[str] | None = None) -> ThreadingHTTPServer:
    """The HTTP server for the console and the API. ``key`` is added to console requests."""
    log = logging.getLogger("api.local")
    s3_hosts = local_s3_hosts or set()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, headers: dict[str, str], payload: bytes) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _request(self, path: str, *, authorize: bool) -> Request:
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            headers = {k.lower(): v for k, v in self.headers.items()}
            if authorize:
                headers[ACCESS_HEADER] = key or ""  # Added here; the page never sees the key.
            return Request(self.command, path, headers, dict(parse_qsl(url.query)), self.rfile.read(length) if length else b"")

        def _handle(self) -> None:
            url = urlsplit(self.path)
            headers = {k.lower(): v for k, v in self.headers.items()}
            if url.path.startswith(API_PREFIX + "/") and key:
                if not same_origin(headers, port):
                    return self._reply(403, {"Content-Type": "application/json"},
                                       b'{"error":{"code":"forbidden","message":"Not from the console"}}')
                status, out_headers, payload = api.handle(self._request(url.path[len(API_PREFIX):], authorize=True)).encoded()
            elif self.command == "GET" and not _is_api_path(url.path):
                status, out_headers, payload = static_file(url.path)
            else:  # The API at the root, with the caller's own key.
                status, out_headers, payload = api.handle(self._request(url.path, authorize=False)).encoded()
            self._reply(status, out_headers, payload)

        def do_PUT(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/upload":
                return self._handle()
            self._forward_upload(parse_qs(url.query).get("url", [""])[0])

        def _forward_upload(self, target: str) -> None:
            import requests

            length = int(self.headers.get("Content-Length") or 0)
            headers = {k.lower(): v for k, v in self.headers.items()}
            if not same_origin(headers, port) or not allowed_upload_target(target, bucket, s3_hosts):
                return self._reply(403, {"Content-Type": "text/plain"}, b"Not allowed")
            if not 0 < length <= MAX_UPLOAD_BYTES:
                return self._reply(413, {"Content-Type": "text/plain"}, b"File too large")
            try:
                response = requests.put(target, data=BoundedBody(self.rfile, length), timeout=600)
            except requests.RequestException as e:
                log.warning("upload failed: %s", type(e).__name__)
                return self._reply(502, {"Content-Type": "text/plain"}, b"Upload failed")
            self._reply(response.status_code, {"Content-Type": "text/plain", **SECURITY_HEADERS}, response.content[:2000])

        do_GET = do_POST = do_PATCH = do_DELETE = _handle

        def log_message(self, fmt: str, *values) -> None:
            if "/calls/call-" not in self.path or self.command != "GET":  # Skip the polling noise.
                log.info(fmt, *values)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local dev server for Live Assist.")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = dict(os.environ)
    env.setdefault("AGENT_URL", "http://localhost:8080")
    key = env.get("ACCESS_KEY")
    if not key and not env.get("ACCESS_KEY_SECRET"):
        sys.exit("Set ACCESS_KEY (any value for local use) or ACCESS_KEY_SECRET.")
    api = Api(services_from_env(env))
    s3_hosts = {urlsplit(env["AWS_ENDPOINT_URL_S3"]).netloc} if env.get("AWS_ENDPOINT_URL_S3") else set()
    server = build_server(api, key=key, port=args.port, bucket=env.get("UPLOAD_BUCKET"), local_s3_hosts=s3_hosts)
    print(f"Live Assist console on http://127.0.0.1:{args.port}  (agent: {env['AGENT_URL']})")
    server.serve_forever()


def _is_api_path(path: str) -> bool:
    """Root-level API routes, which stay reachable for curl and the older test page."""
    return path.split("/")[1] in ("health", "options", "uploads", "calls", "accounts", "configs") if path != "/" else False


if __name__ == "__main__":
    main()
