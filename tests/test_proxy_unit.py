"""Allowlist proxy behavior without Docker."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import ProxyHandler, build_opener

from sandbox.egress.proxy import host_allowed, load_allowlist, serve


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok-allowed"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_host_allowed(tmp_path):
    path = tmp_path / "allowlist.txt"
    path.write_text("web\n# comment\nExample.com\n", encoding="utf-8")
    allow = load_allowlist(str(path))
    assert host_allowed("web:8080", allow)
    assert host_allowed("example.com", allow)
    assert not host_allowed("evil.example", allow)


def test_proxy_allows_one_host_and_blocks_the_other(tmp_path):
    origin_port = _free_port()
    proxy_port = _free_port()
    server = HTTPServer(("127.0.0.1", origin_port), _Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    allow_path = tmp_path / "allow.txt"
    allow_path.write_text("127.0.0.1\n", encoding="utf-8")
    threading.Thread(
        target=serve,
        args=(proxy_port, load_allowlist(str(allow_path))),
        daemon=True,
    ).start()
    opener = build_opener(ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"}))
    with opener.open(f"http://127.0.0.1:{origin_port}/hi", timeout=5) as response:
        assert response.status == 200
        assert response.read() == b"ok-allowed"
    try:
        opener.open("http://evil.example/collect", timeout=5)
        raise AssertionError("evil host should have been refused")
    except Exception as exc:
        assert "403" in str(exc) or "blocked" in str(exc).lower()
    server.shutdown()
