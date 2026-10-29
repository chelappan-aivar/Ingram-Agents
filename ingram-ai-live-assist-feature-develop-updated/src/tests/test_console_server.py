"""The console server: static files, the /api proxy, and the checks that stop other web pages using it."""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
from types import SimpleNamespace

import pytest

from api.console_server import (
    CONSOLE_DIR,
    allowed_upload_target,
    BoundedBody,
    same_origin,
    static_file,
)
from api.local import build_server

PORT_HEADERS = {"host": "127.0.0.1:8090", "x-console": "1"}


@pytest.mark.parametrize("headers, ok", [
    (PORT_HEADERS, True),
    ({**PORT_HEADERS, "host": "localhost:8090"}, True),
    ({**PORT_HEADERS, "origin": "http://127.0.0.1:8090"}, True),
    ({**PORT_HEADERS, "origin": "http://evil.example.com"}, False),  # another site's page
    ({**PORT_HEADERS, "origin": "null"}, False),
    ({**PORT_HEADERS, "host": "evil.example.com:8090"}, False),  # DNS rebinding
    ({**PORT_HEADERS, "host": "127.0.0.1:9999"}, False),
    ({"host": "127.0.0.1:8090"}, False),  # no console header (e.g. an <img> or form from another site)
    ({**PORT_HEADERS, "x-console": "0"}, False),
])
def test_same_origin(headers, ok):
    assert same_origin(headers, 8090) is ok


@pytest.mark.parametrize("path", ["/", "/index.html", "/js/main.js", "/styles/base.css"])
def test_static_files_are_served_with_security_headers(path):
    status, headers, body = static_file(path)
    assert status == 200 and body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src" not in headers["Content-Security-Policy"] or "unsafe-inline" not in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/../app/events.py", "/..%2Fapp/events.py", "/js/../../main.py", "/nope.js",
                                  "/js", "/../.env", "//etc/passwd"])
def test_static_files_cannot_escape_the_console_folder(path):
    assert static_file(path)[0] == 404


def test_the_page_uses_no_inline_scripts_or_remote_assets():
    html = (CONSOLE_DIR / "index.html").read_text()
    assert "<script>" not in html and not re.search(r"\son\w+=", html)  # no inline code or inline handlers
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html)  # nothing is loaded from another site
    for css in (CONSOLE_DIR / "styles").glob("*.css"):
        assert "@import" not in css.read_text() and "url(http" not in css.read_text()


def test_upload_targets_are_limited_to_the_bucket():
    ok = "https://my-bucket.s3.us-east-1.amazonaws.com/uploads/audio/x/manifest.json?X-Amz-Signature=1"
    assert allowed_upload_target(ok, "my-bucket", set())
    for bad in [
        "https://other-bucket.s3.us-east-1.amazonaws.com/x", "https://my-bucket.s3.evil.com/x",
        "https://evil.com/my-bucket.s3.us-east-1.amazonaws.com", "http://my-bucket.s3.us-east-1.amazonaws.com/x",
        "file:///etc/passwd", "https://user:pw@my-bucket.s3.us-east-1.amazonaws.com/x", "", "not a url",
        "http://169.254.169.254/latest/meta-data/",
    ]:
        assert not allowed_upload_target(bad, "my-bucket", set()), bad
    assert not allowed_upload_target(ok, None, set())
    assert allowed_upload_target("http://127.0.0.1:5000/b/k?x=1", "b", {"127.0.0.1:5000"})  # local S3 stand-in


def test_bounded_body_reads_exactly_the_declared_length():
    body = BoundedBody(io.BytesIO(b"abcdefghij"), 6)
    assert len(body) == 6 and body.read(4) == b"abcd" and body.read() == b"ef" and body.read(5) == b""
    short = BoundedBody(io.BytesIO(b"abc"), 10)  # the client stopped early: ends, no hang
    assert short.read() == b"abc" and short.read(1) == b"" and len(short) == 10


def test_bounded_body_is_sent_with_a_content_length_not_chunked():
    import requests
    prepared = requests.Request("PUT", "https://b.s3.amazonaws.com/k", data=BoundedBody(io.BytesIO(b"x" * 50), 50)).prepare()
    assert prepared.headers.get("Content-Length") == "50" and "Transfer-Encoding" not in prepared.headers


def fetch(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_api_proxy_adds_the_key_only_for_the_console():
    seen = []

    class Recorder:
        def handle(self, request):
            seen.append(request)
            return SimpleNamespace(encoded=lambda: (200, {"Content-Type": "application/json"}, b'{"ok":true}'))

    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = build_server(Recorder(), key="secret-key", port=port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, data = fetch(port, "GET", "/api/options", {"X-Console": "1"})
        assert status == 200 and json.loads(data) == {"ok": True}
        assert seen[0].path == "/options" and seen[0].headers["x-access-key"] == "secret-key"
        # A page on another site: wrong Origin, or no console header. Never reaches the API.
        assert fetch(port, "GET", "/api/options", {"X-Console": "1", "Origin": "http://evil.example.com"})[0] == 403
        assert fetch(port, "GET", "/api/options")[0] == 403
        assert fetch(port, "POST", "/api/calls", {"Content-Type": "text/plain"}, b"{}")[0] == 403
        assert fetch(port, "PUT", "/upload?url=https://x.s3.us-east-1.amazonaws.com/a", {"X-Console": "1"}, b"x")[0] == 403
        assert len(seen) == 1
        # The caller's own key is never replaced for root-level API use (curl).
        fetch(port, "GET", "/options", {"X-Access-Key": "mine"})
        assert seen[-1].headers["x-access-key"] == "mine"
        # The page itself.
        status, page = fetch(port, "GET", "/")
        assert status == 200 and b"Live Assist" in page
    finally:
        server.shutdown()
