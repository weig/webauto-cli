"""Step factories.

Each factory returns a `step(handle) -> None` callable that the runner
executes. Value arguments accept `str | Callable[[], str]` so step values
late-bind to params via `BaseAction.get_parameter(...)`.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Union

from webauto._types import BrowserHandle, Step

StrOrLazy = Union[str, Callable[[], str]]
IntOrLazy = Union[int, Callable[[], int]]


def _resolve(value: Any) -> Any:
    """Call zero-arg callables; pass everything else through.

    Classes are excluded so a passed-in type doesn't get instantiated by
    accident.
    """
    if callable(value) and not isinstance(value, type):
        return value()
    return value


# ----------------------------------------------------------------------------
# Step factories — 11 total per the plan.
# ----------------------------------------------------------------------------


def fill(selector: StrOrLazy, value: StrOrLazy) -> Step:
    """Type `value` into the element matched by `selector`."""

    def step(handle: BrowserHandle) -> None:
        handle.fill(_resolve(selector), _resolve(value))

    step.__name__ = f"fill({selector!r}, <value>)"
    return step


def click(selector: StrOrLazy) -> Step:
    """Click the element matched by `selector`."""

    def step(handle: BrowserHandle) -> None:
        handle.click(_resolve(selector))

    step.__name__ = f"click({selector!r})"
    return step


def check(selector: StrOrLazy) -> Step:
    """Check the (single) checkbox matched by `selector`. Strict — fail-loud
    if multiple match.
    """

    def step(handle: BrowserHandle) -> None:
        handle.check(_resolve(selector))

    step.__name__ = f"check({selector!r})"
    return step


def uncheck(selector: StrOrLazy) -> Step:
    """Uncheck the (single) checkbox matched by `selector`. Strict."""

    def step(handle: BrowserHandle) -> None:
        handle.uncheck(_resolve(selector))

    step.__name__ = f"uncheck({selector!r})"
    return step


def check_all(selector: StrOrLazy) -> Step:
    """Check every checkbox matched by `selector` (batch)."""

    def step(handle: BrowserHandle) -> None:
        handle.check_all(_resolve(selector))

    step.__name__ = f"check_all({selector!r})"
    return step


def wait_visible(selector: StrOrLazy, timeout: float = 30.0) -> Step:
    """Wait until `selector` is visible (or timeout)."""

    def step(handle: BrowserHandle) -> None:
        handle.wait_for_selector(_resolve(selector), timeout=timeout)

    step.__name__ = f"wait_visible({selector!r}, timeout={timeout})"
    return step


def wait_url_contains(substring: StrOrLazy, timeout: float = 30.0) -> Step:
    """Wait until the URL contains `substring` (or timeout)."""

    def step(handle: BrowserHandle) -> None:
        handle.wait_for_url(_resolve(substring), timeout=timeout)

    step.__name__ = f"wait_url_contains({substring!r}, timeout={timeout})"
    return step


def wait_page_change(timeout: float = 30.0, poll_ms: int = 100) -> Step:
    """Snapshot the current URL; wait until it changes (or timeout).

    Useful when a step triggers navigation whose destination depends on
    server-side state (e.g. Schwab MFA may land on /yyy/yyy or skip to
    /aaa/aaa).
    """

    def step(handle: BrowserHandle) -> None:
        start_url = handle.url
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if handle.url != start_url:
                return
            handle.sleep_ms(poll_ms)
        raise TimeoutError(
            f"wait_page_change: URL still {start_url!r} after {timeout}s"
        )

    step.__name__ = f"wait_page_change(timeout={timeout})"
    return step


def press(key: StrOrLazy) -> Step:
    """Press a keyboard key on the page (e.g. "Escape", "Enter", "Tab")."""

    def step(handle: BrowserHandle) -> None:
        handle.press(_resolve(key))

    step.__name__ = f"press({key!r})"
    return step


def sleep(ms: IntOrLazy) -> Step:
    """Sleep `ms` milliseconds. Mostly for debugging — prefer wait_* in real flows."""

    def step(handle: BrowserHandle) -> None:
        handle.sleep_ms(int(_resolve(ms)))

    step.__name__ = f"sleep({ms!r})"
    return step


def screenshot(path: StrOrLazy) -> Step:
    """Save a screenshot to `path`."""

    def step(handle: BrowserHandle) -> None:
        handle.screenshot(_resolve(path))

    step.__name__ = f"screenshot({path!r})"
    return step


def wait_for_hydration(timeout: float = 8.0) -> Step:
    """Wait for the page's framework to finish hydrating its bindings.

    Currently a SeleniumBase-specific signal (polls `document.title` until
    Schwab's `"cag"` placeholder flips to the real title, then floors at
    500ms so click handlers have time to attach).

    No-op against backends that don't expose `wait_for_hydration` on the
    handle — falls back to a 500ms sleep.
    """

    def step(handle: BrowserHandle) -> None:
        fn = getattr(handle, "wait_for_hydration", None)
        if callable(fn):
            fn(timeout=timeout)
        else:
            time.sleep(0.5)

    step.__name__ = f"wait_for_hydration(timeout={timeout})"
    return step


def custom(fn: Callable[[BrowserHandle], None]) -> Step:
    """Wrap an arbitrary callable as a step. `fn` is invoked with `(handle,)`.

    Bound methods like `self._emit_redirect(self, handle, _params=None)` work
    because `_params` has a default; Python fills it in.
    """

    def step(handle: BrowserHandle) -> None:
        fn(handle)

    step.__name__ = getattr(fn, "__name__", "custom") + " (wrapped)"
    return step
