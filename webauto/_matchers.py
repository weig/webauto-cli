"""URL matcher factories.

Each factory returns a `Callable[[str], bool]` predicate that the runner
calls against `handle.current_url` to decide which PageAction to dispatch.

Anchor arguments accept either a literal `str` or a zero-arg `Callable`
returning a `str` — supports late-bound values from `get_parameter(...)`.
"""

from __future__ import annotations

import re
from typing import Callable, Pattern, Union

UrlMatcher = Callable[[str], bool]
StrOrLazy = Union[str, Callable[[], str]]


def _resolve(s: StrOrLazy) -> str:
    """Call the callable if applicable, otherwise return the value as-is."""
    if callable(s):
        result = s()
        if not isinstance(result, str):
            raise TypeError(
                f"url matcher anchor callable returned {type(result).__name__}, "
                f"expected str"
            )
        return result
    return s


def any_url() -> UrlMatcher:
    """Predicate that matches every URL.

    Conventionally used in catch-all PageActions with `order=200`+ so they
    fire only when no specific PageAction matched.
    """

    def match(_url: str) -> bool:
        return True

    match.__name__ = "any_url"
    return match


def contains(s: StrOrLazy) -> UrlMatcher:
    """Predicate that matches when `s` is a substring of the URL."""

    def match(url: str) -> bool:
        return _resolve(s) in url

    match.__name__ = f"contains({s!r})"
    return match


def startswith(s: StrOrLazy) -> UrlMatcher:
    """Predicate that matches when the URL starts with `s`."""

    def match(url: str) -> bool:
        return url.startswith(_resolve(s))

    match.__name__ = f"startswith({s!r})"
    return match


def equals(s: StrOrLazy) -> UrlMatcher:
    """Predicate that matches when the URL is exactly `s`."""

    def match(url: str) -> bool:
        return url == _resolve(s)

    match.__name__ = f"equals({s!r})"
    return match


def regex(pattern: str) -> UrlMatcher:
    """Predicate that matches when `pattern` is found in the URL (re.search)."""
    compiled: Pattern[str] = re.compile(pattern)

    def match(url: str) -> bool:
        return bool(compiled.search(url))

    match.__name__ = f"regex({pattern!r})"
    return match
