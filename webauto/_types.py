"""Internal type definitions, sentinels, and core classes.

Public re-exports live in webauto/__init__.py.
"""

from __future__ import annotations

from dataclasses import fields
from typing import (
    Any,
    Callable,
    ClassVar,
    Optional,
    Protocol,
    runtime_checkable,
)


# ----------------------------------------------------------------------------
# Internal sentinel exception
# ----------------------------------------------------------------------------


class _Done(Exception):
    """Raised by notify() after the payload has been sent. Caught by init().

    Not part of the public API. Callers should never see this exception.
    """


# ----------------------------------------------------------------------------
# BrowserHandle Protocol
# ----------------------------------------------------------------------------


@runtime_checkable
class BrowserHandle(Protocol):
    """Minimal browser surface that step factories operate against.

    Concrete impls (e.g. SeleniumBaseHandle) provide these methods on top of
    a real browser driver. Tests use a MockBrowserHandle.
    """

    @property
    def url(self) -> str: ...

    def goto(self, url: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def check(self, selector: str) -> None: ...
    def uncheck(self, selector: str) -> None: ...
    def check_all(self, selector: str) -> None: ...
    def wait_for_selector(self, selector: str, timeout: float = 30.0) -> None: ...
    def wait_for_url(self, substring: str, timeout: float = 30.0) -> None: ...
    def wait_for_load(self, timeout: float = 30.0) -> None: ...
    def sleep_ms(self, ms: int) -> None: ...
    def press(self, key: str) -> None: ...
    def screenshot(self, path: str) -> None: ...
    def is_visible(self, selector: str) -> bool: ...
    def count(self, selector: str) -> int: ...
    def close(self) -> None: ...


# ----------------------------------------------------------------------------
# PageAction
# ----------------------------------------------------------------------------


UrlMatcher = Callable[[str], bool]
Step = Callable[[BrowserHandle], None]


class PageAction:
    """Routing-table entry: URL predicate + steps to run when matched.

    The runner picks the matching PageAction with lowest `order`; ties broken
    by declaration index. Catch-alls (`url_any=True`) conventionally set
    `order=200`+.
    """

    __slots__ = ("url", "steps", "order")

    def __init__(
        self,
        *,
        url: Optional[UrlMatcher] = None,
        url_contains: Optional[Any] = None,
        url_equals: Optional[Any] = None,
        url_startswith: Optional[Any] = None,
        url_regex: Optional[str] = None,
        url_any: bool = False,
        steps: Optional[list[Step]] = None,
        order: int = 100,
    ) -> None:
        # Import here to avoid a circular import at module load time
        # (matchers don't depend on _types, but a cleaner separation helps).
        from webauto._matchers import (
            any_url,
            contains,
            equals,
            regex,
            startswith,
        )

        provided = {
            "url": url is not None,
            "url_contains": url_contains is not None,
            "url_equals": url_equals is not None,
            "url_startswith": url_startswith is not None,
            "url_regex": url_regex is not None,
            "url_any": bool(url_any),
        }
        count = sum(provided.values())
        if count == 0:
            raise TypeError("PageAction requires a url matcher")
        if count > 1:
            chosen = [k for k, v in provided.items() if v]
            raise TypeError(
                f"PageAction accepts exactly one URL matcher, got {chosen}"
            )

        if url is not None:
            matcher: UrlMatcher = url
        elif url_contains is not None:
            matcher = contains(url_contains)
        elif url_equals is not None:
            matcher = equals(url_equals)
        elif url_startswith is not None:
            matcher = startswith(url_startswith)
        elif url_regex is not None:
            matcher = regex(url_regex)
        else:  # url_any
            matcher = any_url()

        self.url = matcher
        self.steps = list(steps) if steps is not None else []
        self.order = int(order)

    def __repr__(self) -> str:
        return (
            f"PageAction(url=<predicate>, steps={len(self.steps)}, "
            f"order={self.order})"
        )


# ----------------------------------------------------------------------------
# BaseAction
# ----------------------------------------------------------------------------


class BaseAction:
    """Base class for action scripts.

    Subclasses set:
      - `Params: ClassVar[type] = SomeDataclass` (optional) — declares inputs
        that are auto-populated from os.environ.
      - `actions: list[PageAction]` — either as a class attribute (static)
        or assigned in __init__ (when referencing params).
      - `timeout_seconds: float = 300.0` (optional) — inner action timeout.
      - `on_failure: list[Step]` (optional) — best-effort cleanup steps run
        by init() when a step raises (e.g. click a logout link). Errors in
        these steps are logged and never mask the original failure.
    """

    Params: ClassVar[Optional[type]] = None
    timeout_seconds: ClassVar[float] = 300.0
    actions: list[PageAction] = []
    on_failure: list[Step] = []

    def __init__(self, params: Any = None) -> None:
        if params is None and self.Params is not None:
            # Local import to avoid pulling params machinery into _types' import path
            # when actions don't declare Params.
            from webauto._params import build_params_from_env

            params = build_params_from_env(self.Params)
        self.params = params

    def get_parameter(self, name: str) -> Callable[[], Any]:
        """Return a zero-arg lambda that pulls `name` from self.params at call time.

        Validates `name` against the Params dataclass fields at definition time
        so typos surface immediately, not at step-execution time.
        """
        if self.Params is None:
            raise AttributeError(
                f"{type(self).__name__} has no Params declared; "
                f"cannot get_parameter({name!r})"
            )
        valid = {f.name for f in fields(self.Params)}
        if name not in valid:
            raise AttributeError(
                f"{self.Params.__name__} has no field {name!r}; "
                f"valid fields: {sorted(valid)}"
            )

        def extractor() -> Any:
            return getattr(self.params, name)

        extractor.__name__ = f"get_parameter({name!r})"
        return extractor
