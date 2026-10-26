"""Verification steps: check a page's structure before acting on it.

`verify_all` evaluates every expectation and reports each one, so a caller
sees the whole picture of a page instead of only the first missing element.
It raises only after all checks ran, which lets a PageAction put a
`verify_all` in front of its fill/click steps as a gate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from webauto._factories import StrOrLazy, _resolve
from webauto._types import BrowserHandle, Step


@dataclass(frozen=True)
class Expectation:
    name: str
    selector: str
    count: int = 1


@dataclass(frozen=True)
class CheckResult:
    name: str
    selector: str
    expected: int
    actual: int

    @property
    def ok(self) -> bool:
        return self.actual == self.expected


class VerificationFailed(AssertionError):
    """At least one expectation did not match. `.results` holds every check."""

    def __init__(self, results: list[CheckResult], url: str) -> None:
        self.results = results
        self.url = url
        lines = [f"verification failed on {url}"]
        for r in results:
            mark = "✓" if r.ok else "✗"
            detail = "" if r.ok else f"  (expected {r.expected}, found {r.actual})"
            lines.append(f"  {mark} {r.name}: {r.selector}{detail}")
        super().__init__("\n".join(lines))


class UnexpectedPage(RuntimeError):
    """The browser stayed on a URL that no declared PageAction handles."""


def expect(name: str, selector: str, count: int = 1) -> Expectation:
    """Expect `selector` to match exactly `count` elements (default: one)."""
    return Expectation(name=name, selector=selector, count=count)


VERIFY_POLL_MS = 250


def verify_all(
    *expectations: Expectation,
    screenshot: Optional[StrOrLazy] = None,
    timeout: float = 0.0,
) -> Step:
    """Check every expectation; raise VerificationFailed listing all of them if any misses.

    With `timeout` > 0 the checks are re-run until they all match or the time
    is up, so a page that is still rendering gets a chance to finish. A page
    that is genuinely different never matches, and the failure reports the
    last evaluation of every check.
    """
    if not expectations:
        raise ValueError("verify_all needs at least one expectation")

    def evaluate(handle: BrowserHandle) -> list[CheckResult]:
        return [CheckResult(e.name, e.selector, e.count, handle.count(e.selector)) for e in expectations]

    def step(handle: BrowserHandle) -> None:
        deadline = time.monotonic() + timeout
        results = evaluate(handle)
        while not all(r.ok for r in results) and time.monotonic() < deadline:
            handle.sleep_ms(VERIFY_POLL_MS)
            results = evaluate(handle)
        if all(r.ok for r in results):
            return
        if screenshot is not None:
            handle.screenshot(_resolve(screenshot))
        raise VerificationFailed(results, handle.url)

    step.__name__ = f"verify_all({', '.join(e.name for e in expectations)})"
    return step


def click_if_unique(selector: StrOrLazy) -> Step:
    """Click only when `selector` matches exactly one element; otherwise do nothing.

    Meant for cleanup (e.g. logging out after a failure) where clicking the
    wrong thing is worse than not clicking at all.
    """

    def step(handle: BrowserHandle) -> None:
        resolved = _resolve(selector)
        if handle.count(resolved) == 1:
            handle.click(resolved)

    step.__name__ = f"click_if_unique({selector!r})"
    return step


def unexpected_page(
    grace_seconds: float = 10.0,
    screenshot: Optional[StrOrLazy] = None,
    poll_ms: int = 100,
) -> Step:
    """Catch-all step: allow `grace_seconds` for a transient URL to move on, else fail.

    Pair with `PageAction(url_any=True, order=200)` so a redirect to a page the
    script does not know (password expired, MFA, error page) stops the run
    instead of idling until the global timeout.
    """

    def step(handle: BrowserHandle) -> None:
        start_url = handle.url
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            handle.sleep_ms(poll_ms)
            if handle.url != start_url:
                return
        if screenshot is not None:
            handle.screenshot(_resolve(screenshot))
        raise UnexpectedPage(f"no PageAction handles {start_url!r} (waited {grace_seconds}s)")

    step.__name__ = f"unexpected_page(grace_seconds={grace_seconds})"
    return step
