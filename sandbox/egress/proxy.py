"""Allowlist HTTP proxy. Every host not on the task list is refused.

The proxy reads its allowlist once, at startup, so a sandbox cannot widen it
by editing the mounted file later.
"""

from __future__ import annotations

import select
import socket
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 8888
DEFAULT_ALLOWLIST = "/config/allowlist.txt"
MAX_HEADER = 65536


def load_allowlist(path: str) -> set[str]:
    """Return the lowercased hostnames from ``path``."""
    raw = Path(path).read_text(encoding="utf-8")
    hosts: set[str] = set()
    for line in raw.splitlines():
        item = line.strip().lower().rstrip(".")
        if item and not item.startswith("#"):
            hosts.add(item.split(":")[0])
    return hosts


def host_allowed(host: str, allow: set[str]) -> bool:
    """True when ``host`` (with an optional port) is on the allowlist."""
    name = host.split(":")[0].strip().lower().rstrip(".")
    return bool(name) and name in allow


def serve(port: int, allow: set[str]) -> None:
    """Serve the proxy until the process is killed."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(64)
    while True:
        conn, _addr = server.accept()
        threading.Thread(target=_handle, args=(conn, allow), daemon=True).start()


def _handle(conn: socket.socket, allow: set[str]) -> None:
    try:
        data = _read_headers(conn)
        if not data:
            return
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1", "replace").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]
        if method == "CONNECT":
            if not host_allowed(target, allow):
                _deny(conn)
                return
            _tunnel(conn, target)
            return
        split = urlsplit(target)
        if split.scheme not in {"http", ""} or not split.hostname:
            _deny(conn)
            return
        if not host_allowed(split.hostname, allow):
            _deny(conn)
            return
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        payload = _rewrite(method, path, lines[1:], rest)
        _forward(conn, split.hostname, split.port or 80, payload)
    except Exception:
        try:
            _deny(conn)
        except Exception:
            pass
    finally:
        conn.close()


def _read_headers(conn: socket.socket) -> bytes:
    data = b""
    conn.settimeout(10)
    while b"\r\n\r\n" not in data and len(data) < MAX_HEADER:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _deny(conn: socket.socket) -> None:
    body = b"blocked by allowlist"
    head = (
        b"HTTP/1.1 403 Forbidden\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n\r\n"
    )
    conn.sendall(head + body)


def _rewrite(method: str, path: str, headers: list[str], body: bytes) -> bytes:
    kept = ["Connection: close"]
    for line in headers:
        lower = line.lower()
        if lower.startswith("proxy-") or lower.startswith("connection:"):
            continue
        kept.append(line)
    text = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(kept) + "\r\n\r\n"
    return text.encode("iso-8859-1", "replace") + body


def _forward(conn: socket.socket, host: str, port: int, payload: bytes) -> None:
    with socket.create_connection((host, port), timeout=8) as upstream:
        upstream.sendall(payload)
        _pump(conn, upstream)


def _tunnel(conn: socket.socket, hostport: str) -> None:
    host, _, port_text = hostport.partition(":")
    port = int(port_text or "443")
    with socket.create_connection((host, port), timeout=8) as upstream:
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        _pump(conn, upstream)


def _pump(left: socket.socket, right: socket.socket) -> None:
    pair = [left, right]
    while True:
        readable, _, _ = select.select(pair, [], [], 8)
        if not readable:
            return
        for sock in readable:
            other = right if sock is left else left
            chunk = sock.recv(65536)
            if not chunk:
                return
            other.sendall(chunk)


def main(argv: list[str] | None = None) -> None:
    """Load the allowlist and listen. Args: ``[port] [allowlist_path]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    port = int(args[0]) if args else DEFAULT_PORT
    path = args[1] if len(args) > 1 else DEFAULT_ALLOWLIST
    serve(port, load_allowlist(path))


if __name__ == "__main__":
    main()
