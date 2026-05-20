"""Integration tests for HttpNotificationListener."""

from __future__ import annotations

import threading
import time
import urllib.request

import pytest

from webauto._notification_listener import (
    HttpNotificationListener,
    make_notification_listener,
)


def _post(endpoint: str, body: bytes, content_type: str | None = None) -> int:
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class TestListenerEndpoint:
    def test_endpoint_includes_token(self) -> None:
        with HttpNotificationListener() as listener:
            assert "/notify/" in listener.endpoint
            assert len(listener.token) == 8


class TestListenerCapture:
    def test_body_captured_with_content_type(self) -> None:
        with HttpNotificationListener() as listener:
            def post():
                time.sleep(0.05)
                _post(listener.endpoint, b'{"x":1}', "application/json")

            threading.Thread(target=post, daemon=True).start()
            body, ct = listener.wait(deadline=time.time() + 2)
            assert body == b'{"x":1}'
            assert ct == "application/json"

    def test_wrong_path_returns_404(self) -> None:
        with HttpNotificationListener() as listener:
            bad_endpoint = listener.endpoint.replace(listener.token, "deadbeef")
            status = _post(bad_endpoint, b"x")
            assert status == 404

    def test_one_shot(self) -> None:
        with HttpNotificationListener() as listener:
            def post_twice():
                time.sleep(0.05)
                _post(listener.endpoint, b"first")
                time.sleep(0.05)
                # second post should be rejected as already-fired
                status = _post(listener.endpoint, b"second")
                assert status == 409

            t = threading.Thread(target=post_twice, daemon=True)
            t.start()
            body, _ = listener.wait(deadline=time.time() + 2)
            assert body == b"first"
            t.join(timeout=2)


class TestListenerTimeout:
    def test_wait_times_out(self) -> None:
        with HttpNotificationListener() as listener:
            with pytest.raises(TimeoutError):
                listener.wait(deadline=time.time() + 0.1)


class TestListenerFactory:
    def test_http_returns_listener(self) -> None:
        listener = make_notification_listener("http")
        try:
            assert isinstance(listener, HttpNotificationListener)
        finally:
            listener.close()

    def test_sock_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="deferred to v2"):
            make_notification_listener("sock")

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            make_notification_listener("carrier-pigeon")
