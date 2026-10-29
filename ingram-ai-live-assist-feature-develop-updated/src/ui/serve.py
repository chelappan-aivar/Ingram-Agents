"""Local test page for a deployed environment.

    .venv/bin/python testui/serve.py            # environment "dev", http://127.0.0.1:8765
    .venv/bin/python testui/serve.py --env prod --port 9000

Serves this folder and forwards ``/api/*`` to the environment's API and
``PUT /upload?url=...`` to its upload bucket, so the browser needs no CORS. The
access key is read from Secrets Manager and added here; it never reaches the
page. Listens on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FORWARD_REQUEST_HEADERS = ("content-type", "if-none-match")
FORWARD_RESPONSE_HEADERS = ("content-type", "etag")


def load_outputs(env: str) -> dict[str, str]:
    path = ROOT / "deploy" / "outputs" / f"{env}.env"
    if not path.is_file():
        sys.exit(f"{path} not found; deploy first (deploy/deploy.sh {env} up)")
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="dev")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    out = load_outputs(args.env)
    api_url = out["API_URL"].rstrip("/")
    bucket = out["UPLOAD_BUCKET"]
    key = boto3.client("secretsmanager", region_name=out["REGION"]).get_secret_value(
        SecretId=out["ACCESS_KEY_SECRET"])["SecretString"]
    http = requests.Session()

    def bucket_host(host: str) -> bool:
        return host.startswith(f"{bucket}.s3.") and host.endswith(".amazonaws.com")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE), **kw)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _reply(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _proxy_api(self) -> None:
            url = urlsplit(self.path)
            target = api_url + url.path[len("/api"):] + (f"?{url.query}" if url.query else "")
            headers = {k: v for k, v in self.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
            headers["X-Access-Key"] = key
            try:
                r = http.request(self.command, target, data=self._body() or None, headers=headers, timeout=60)
            except requests.RequestException as e:
                self._reply(502, f'{{"error":{{"code":"proxy","message":"{type(e).__name__}"}}}}'.encode(),
                            {"Content-Type": "application/json"})
                return
            self._reply(r.status_code, r.content,
                        {k: v for k, v in r.headers.items() if k.lower() in FORWARD_RESPONSE_HEADERS})

        def do_GET(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
            else:
                super().do_GET()

        def do_POST(self) -> None:
            self._proxy_api() if self.path.startswith("/api/") else self.send_error(404)

        def do_PATCH(self) -> None:
            self._proxy_api() if self.path.startswith("/api/") else self.send_error(404)

        def do_DELETE(self) -> None:
            self._proxy_api() if self.path.startswith("/api/") else self.send_error(404)

        def do_PUT(self) -> None:
            if self.path.startswith("/api/"):
                self._proxy_api()
                return
            url = urlsplit(self.path)
            target = (parse_qs(url.query).get("url") or [""])[0]
            if url.path != "/upload" or urlsplit(target).scheme != "https" or not bucket_host(urlsplit(target).hostname or ""):
                self.send_error(400, "upload URL must be a presigned URL for the upload bucket")
                return
            r = http.put(target, data=self._body(), timeout=600)
            self._reply(r.status_code, r.content, {"Content-Type": r.headers.get("Content-Type", "text/plain")})

        def log_message(self, fmt: str, *values) -> None:
            if not self.path.startswith("/api/calls/") or self.command != "GET":  # Skip the polling noise.
                super().log_message(fmt, *values)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Test UI for '{args.env}' on http://127.0.0.1:{args.port}  ->  {api_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
