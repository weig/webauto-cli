"""Unit tests for step factories.

Each factory is a thin shim over the BrowserHandle; the tests verify that
the right BrowserHandle method is invoked with resolved (lazy-or-literal)
arguments.
"""

from __future__ import annotations

import pytest

from webauto._factories import (
    check,
    check_all,
    click,
    custom,
    fill,
    screenshot,
    sleep,
    uncheck,
    wait_page_change,
    wait_url_contains,
    wait_visible,
)


class TestFill:
    def test_literals(self, handle) -> None:
        step = fill("#id", "alice")
        step(handle)
        assert handle.calls == [("fill", "#id", "alice")]

    def test_lazy_value(self, handle) -> None:
        password = "secret"
        step = fill("#pw", lambda: password)
        step(handle)
        assert handle.calls == [("fill", "#pw", "secret")]

        password = "rotated"
        handle.calls.clear()
        step(handle)
        assert handle.calls == [("fill", "#pw", "rotated")]


class TestClick:
    def test_basic(self, handle) -> None:
        step = click("#submit")
        step(handle)
        assert handle.calls == [("click", "#submit")]


class TestCheckUncheck:
    def test_check(self, handle) -> None:
        check("#cb")(handle)
        assert handle.calls == [("check", "#cb")]

    def test_uncheck(self, handle) -> None:
        uncheck("#cb")(handle)
        assert handle.calls == [("uncheck", "#cb")]

    def test_check_all(self, handle) -> None:
        check_all("input[type=checkbox]")(handle)
        assert handle.calls == [("check_all", "input[type=checkbox]")]


class TestWaits:
    def test_wait_visible(self, handle) -> None:
        wait_visible(".price", timeout=5.0)(handle)
        assert handle.calls == [("wait_for_selector", ".price", 5.0)]

    def test_wait_url_contains(self, handle) -> None:
        wait_url_contains("/aaa", timeout=10.0)(handle)
        assert handle.calls == [("wait_for_url", "/aaa", 10.0)]


class TestWaitPageChange:
    def test_returns_when_url_changes(self, handle) -> None:
        handle._url = "/before"
        # Pre-script: after first sleep_ms call, URL flips.
        original_sleep = handle.sleep_ms

        def flipping_sleep(ms):
            original_sleep(ms)
            handle._url = "/after"

        handle.sleep_ms = flipping_sleep  # type: ignore[method-assign]
        wait_page_change(timeout=5.0, poll_ms=10)(handle)
        assert handle.url == "/after"

    def test_timeout_raises(self, handle) -> None:
        handle._url = "/static"
        with pytest.raises(TimeoutError):
            wait_page_change(timeout=0.05, poll_ms=10)(handle)


class TestSleep:
    def test_literal(self, handle) -> None:
        sleep(250)(handle)
        assert handle.calls == [("sleep_ms", 250)]

    def test_lazy(self, handle) -> None:
        sleep(lambda: 100)(handle)
        assert handle.calls == [("sleep_ms", 100)]


class TestScreenshot:
    def test_basic(self, handle) -> None:
        screenshot("/tmp/x.png")(handle)
        assert handle.calls == [("screenshot", "/tmp/x.png")]


class TestCustom:
    def test_invokes_with_handle(self, handle) -> None:
        seen: list = []

        def fn(h):
            seen.append(h)

        custom(fn)(handle)
        assert seen == [handle]

    def test_method_with_default_extra_arg(self, handle) -> None:
        class Action:
            def step(self, h, _params=None):
                self.h = h

        a = Action()
        custom(a.step)(handle)
        assert a.h is handle  # type: ignore[attr-defined]


class TestPress:
    def test_sends_key_to_handle(self, handle) -> None:
        from webauto._factories import press

        press("Escape")(handle)

        assert handle.calls == [("press", "Escape")]
