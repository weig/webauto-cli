"""Shared test fixtures."""

from __future__ import annotations

from typing import Any

import pytest


class MockBrowserHandle:
    """In-memory BrowserHandle for tests.

    Records every method call on `.calls`, exposes `url` as a settable
    property, and lets tests pre-load visibility / selection state.
    """

    def __init__(self, url: str = "https://example.com/") -> None:
        self._url = url
        self.calls: list[tuple[Any, ...]] = []
        # Selectors that is_visible() should return True for.
        self.visible: set[str] = set()
        # Selectors considered "selected" (for check/uncheck idempotency).
        self.selected: set[str] = set()
        # Pre-scripted URL transitions: when this step index runs (0-based),
        # set self._url to the corresponding entry. None = no change.
        self.url_after_step: list[str | None] = []
        self._step_counter = 0

    # --- BrowserHandle Protocol --------------------------------------------

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        self._url = value

    def goto(self, url: str) -> None:
        self.calls.append(("goto", url))
        self._url = url

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))
        self._advance()

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))
        self._advance()

    def check(self, selector: str) -> None:
        self.calls.append(("check", selector))
        self.selected.add(selector)
        self._advance()

    def uncheck(self, selector: str) -> None:
        self.calls.append(("uncheck", selector))
        self.selected.discard(selector)
        self._advance()

    def check_all(self, selector: str) -> None:
        self.calls.append(("check_all", selector))
        self._advance()

    def wait_for_selector(self, selector: str, timeout: float = 30.0) -> None:
        self.calls.append(("wait_for_selector", selector, timeout))
        self._advance()

    def wait_for_url(self, substring: str, timeout: float = 30.0) -> None:
        self.calls.append(("wait_for_url", substring, timeout))
        self._advance()

    def wait_for_load(self, timeout: float = 30.0) -> None:
        self.calls.append(("wait_for_load", timeout))
        self._advance()

    def sleep_ms(self, ms: int) -> None:
        self.calls.append(("sleep_ms", ms))

    def screenshot(self, path: str) -> None:
        self.calls.append(("screenshot", path))
        self._advance()

    def is_visible(self, selector: str) -> bool:
        return selector in self.visible

    def close(self) -> None:
        self.calls.append(("close",))

    # --- test helpers ------------------------------------------------------

    def _advance(self) -> None:
        idx = self._step_counter
        self._step_counter += 1
        if idx < len(self.url_after_step):
            new_url = self.url_after_step[idx]
            if new_url is not None:
                self._url = new_url


@pytest.fixture
def handle() -> MockBrowserHandle:
    return MockBrowserHandle()
