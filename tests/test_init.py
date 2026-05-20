"""Integration test for init() — runs an action against a mock handle and
captures the notification at a real HttpNotificationListener.
"""

from __future__ import annotations

import json
import threading
import time
from typing import ClassVar

from webauto import init, done, notify, BaseAction, PageAction
from webauto._notification_listener import HttpNotificationListener


class _NotifyOnFirstPage(BaseAction):
    timeout_seconds: ClassVar[float] = 5.0
    actions = [
        PageAction(url_any=True, steps=[
            (lambda _h: notify({"hello": "world"})),
        ]),
    ]


def test_init_drives_action_and_listener_captures_payload(handle):
    """End-to-end: init() runs the action, notify() POSTs, listener captures."""
    handle._url = "https://h/start"

    with HttpNotificationListener() as listener:
        results: dict = {}

        def runner():
            init(
                url="https://h/start",
                notification={"type": "http", "endpoint": listener.endpoint},
                action=_NotifyOnFirstPage(),
                handle=handle,
            )

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        body, ct = listener.wait(deadline=time.time() + 3)
        t.join(timeout=2)

    assert json.loads(body) == {"hello": "world"}
    assert ct == "application/json"
    # The mock handle was navigated and closed.
    assert ("goto", "https://h/start") in handle.calls
    assert ("close",) in handle.calls


class _NeverNotifies(BaseAction):
    timeout_seconds: ClassVar[float] = 0.3
    actions = [PageAction(url_any=True, steps=[(lambda _h: None)])]


def test_init_propagates_timeout(handle):
    """If no step calls notify(), init() propagates the runner's TimeoutError."""
    import pytest as _pytest

    with HttpNotificationListener() as listener:
        with _pytest.raises(TimeoutError):
            init(
                url="https://h/start",
                notification={"type": "http", "endpoint": listener.endpoint},
                action=_NeverNotifies(),
                handle=handle,
            )
    # Browser still closed despite timeout.
    assert ("close",) in handle.calls


class _DoneOnFirstPage(BaseAction):
    timeout_seconds: ClassVar[float] = 5.0
    actions = [
        PageAction(url_any=True, steps=[
            (lambda _h: done()),
        ]),
    ]


def test_init_no_channel_with_done(handle, monkeypatch):
    """init() with no `notification=` and no env vars: done() works, browser closes."""
    monkeypatch.delenv("AUTOMATION_NOTIFICATION_TYPE", raising=False)
    monkeypatch.delenv("AUTOMATION_NOTIFICATION_ENDPOINT", raising=False)
    handle._url = "https://h/start"

    init(
        url="https://h/start",
        action=_DoneOnFirstPage(),
        handle=handle,
    )
    assert ("close",) in handle.calls


class _NotifyOnFirstPageBad(BaseAction):
    timeout_seconds: ClassVar[float] = 1.0
    actions = [
        PageAction(url_any=True, steps=[
            (lambda _h: notify({"x": 1})),  # raises in no-channel mode
        ]),
    ]


def test_init_no_channel_notify_errors(handle, monkeypatch):
    """init() with no channel + notify() → RuntimeError pointing at done()."""
    import pytest as _pytest
    monkeypatch.delenv("AUTOMATION_NOTIFICATION_TYPE", raising=False)
    monkeypatch.delenv("AUTOMATION_NOTIFICATION_ENDPOINT", raising=False)
    handle._url = "https://h/start"
    with _pytest.raises(RuntimeError, match="done"):
        init(
            url="https://h/start",
            action=_NotifyOnFirstPageBad(),
            handle=handle,
        )
    assert ("close",) in handle.calls


def test_init_reads_notification_from_env(handle, monkeypatch):
    """init() without explicit notification arg picks up AUTOMATION_* env vars."""
    with HttpNotificationListener() as listener:
        monkeypatch.setenv("AUTOMATION_NOTIFICATION_TYPE", "http")
        monkeypatch.setenv("AUTOMATION_NOTIFICATION_ENDPOINT", listener.endpoint)

        def runner():
            init(
                url="https://h/start",
                action=_NotifyOnFirstPage(),
                handle=handle,
            )

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        body, _ = listener.wait(deadline=time.time() + 3)
        t.join(timeout=2)

    assert json.loads(body) == {"hello": "world"}
