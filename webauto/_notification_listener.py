"""HTTP notification listener.

Stands up a tiny loopback HTTP server. The endpoint URL embeds an 8-hex
path token (the correlation token). The body is treated as opaque bytes —
the listener does not parse it.

v1 ships HTTP only. The `make_notification_listener` factory has a stub
for `sock` raising NotImplementedError.
"""

from __future__ import annotations

import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional


class HttpNotificationListener:
    """One-shot HTTP listener on a loopback port.

    Usage:
        with HttpNotificationListener() as listener:
            print(listener.endpoint)         # http://127.0.0.1:38291/notify/<token>
            data, content_type = listener.wait(deadline=time.time() + 60)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._token = secrets.token_hex(4)  # 8 hex chars
        self._captured: Optional[tuple[bytes, Optional[str]]] = None
        self._received = threading.Event()
        self._fired_once = False

        self._server = HTTPServer((host, port), self._make_handler())
        # Capture the bound port (in case port=0).
        self._bound_port = self._server.server_address[1]

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # --- public API --------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return f"http://{self._host}:{self._bound_port}/notify/{self._token}"

    @property
    def token(self) -> str:
        return self._token

    def wait(self, deadline: float, cancel: Optional[threading.Event] = None
             ) -> tuple[bytes, Optional[str]]:
        """Block until a notification arrives or `deadline` elapses.

        Args:
          deadline: absolute time.time() at which to give up.
          cancel:   optional Event; if set, wait returns early with TimeoutError.

        Returns:
          (body_bytes, content_type)

        Raises:
          TimeoutError if no notification was received before the deadline.
        """
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("HttpNotificationListener.wait: deadline reached")
            if cancel is not None and cancel.is_set():
                raise TimeoutError("HttpNotificationListener.wait: cancelled")
            # Poll in small slices so cancel is responsive.
            timeout = min(remaining, 0.5)
            if self._received.wait(timeout=timeout):
                assert self._captured is not None
                return self._captured

    def close(self) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()

    def __enter__(self) -> "HttpNotificationListener":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- internals ---------------------------------------------------------

    def _make_handler(self):
        listener_self = self

        class Handler(BaseHTTPRequestHandler):
            # Silence the noisy default stderr logging.
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 (http.server convention)
                expected_path = f"/notify/{listener_self._token}"
                if self.path != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                if listener_self._fired_once:
                    # one-shot: already captured a notification
                    self.send_response(409)
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else b""
                content_type = self.headers.get("Content-Type")
                listener_self._captured = (body, content_type)
                listener_self._fired_once = True
                listener_self._received.set()

                self.send_response(204)
                self.end_headers()

        return Handler


def make_notification_listener(transport_type: str) -> HttpNotificationListener:
    """Factory: build the listener for a given transport type.

    v1 only supports "http"; "sock" raises NotImplementedError.
    """
    if transport_type == "http":
        return HttpNotificationListener()
    if transport_type == "sock":
        raise NotImplementedError(
            "sock transport deferred to v2 — see open questions in the plan"
        )
    raise ValueError(f"unsupported transport type: {transport_type!r}")
