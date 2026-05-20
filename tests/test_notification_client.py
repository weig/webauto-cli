"""Integration tests for _post_notification — http transport only.

Uses a throwaway http.server bound to 127.0.0.1:0.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import pytest

from webauto._notification_client import _post_notification


class _CapturingHandler(BaseHTTPRequestHandler):
    captured: tuple[bytes, Optional[str], int] | None = None

    def log_message(self, *args, **kwargs) -> None:  # silence stderr
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        ct = self.headers.get("Content-Type")
        type(self).captured = (body, ct, length)
        self.send_response(204)
        self.end_headers()


@pytest.fixture
def http_server():
    _CapturingHandler.captured = None
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


class TestHttpTransport:
    def test_raw_bytes_default_octet_stream(self, http_server) -> None:
        endpoint = f"http://127.0.0.1:{http_server}/notify/abc"
        _post_notification(
            {"type": "http", "endpoint": endpoint},
            b"raw payload",
        )
        body, ct, _ = _CapturingHandler.captured  # type: ignore[misc]
        assert body == b"raw payload"
        assert ct == "application/octet-stream"

    def test_json_with_explicit_content_type(self, http_server) -> None:
        endpoint = f"http://127.0.0.1:{http_server}/notify/abc"
        _post_notification(
            {"type": "http", "endpoint": endpoint},
            b'{"a":1}',
            content_type="application/json",
        )
        body, ct, _ = _CapturingHandler.captured  # type: ignore[misc]
        assert body == b'{"a":1}'
        assert ct == "application/json"


class TestSockTransportDeferred:
    def test_sock_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="deferred to v2"):
            _post_notification(
                {"type": "sock", "endpoint": "/tmp/x.sock"},
                b"raw",
            )


class TestUnknownTransport:
    def test_unknown_type_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unsupported notification type"):
            _post_notification(
                {"type": "carrier-pigeon", "endpoint": "?"},
                b"raw",
            )
