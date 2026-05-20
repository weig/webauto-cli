"""URL parsing helper — generic, not OAuth-specific.

`parse_url(url=None, *, expected_url, expected_query_string, include_base_url,
strip_url)` splits a URL into `{base_url, **query_params}` and optionally
validates / trims it.

Typical usage inside an action's `custom` step:

    from webauto import notify, parse_url, custom, startswith

    PageAction(url_startswith=REDIRECT_URI, steps=[
        custom(lambda h: notify(parse_url(
            expected_url=startswith(REDIRECT_URI),
            expected_query_string={"state": EXPECTED_STATE},
        ))),
    ])

If `url` is omitted, `parse_url` reads `handle.url` from the runner's
ContextVar — set automatically before each step runs.

Validation is opt-in and raises `ValueError` on mismatch:
  - `expected_url=<matcher>` must return True for the input URL.
  - `expected_query_string={...}` must be a subset of the parsed params.

Shape controls:
  - `include_base_url=False` → omit the `base_url` key from the result.
  - `strip_url=<prefix>`     → strip this prefix from the URL *before* parsing;
                               handy when the relay path is dynamic and you
                               want a stable `base_url` like `/callback`.

State validation (OAuth-flavored) is one use of `expected_query_string`;
nothing about this module is OAuth-specific.
"""

from __future__ import annotations

import contextvars
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit


# Bound by the runner before each step call; lets `parse_url()` (no args)
# read the current page's URL without it having to be passed through the
# step's closure.
_current_handle_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "webauto_current_handle"
)


def parse_url(
    url: Optional[str] = None,
    *,
    expected_url: Optional[Callable[[str], bool]] = None,
    expected_query_string: Optional[dict[str, str]] = None,
    include_base_url: bool = True,
    strip_url: Optional[str] = None,
) -> dict[str, Any]:
    """Split a URL into `{"base_url": ..., **query_params}`. Optionally validate
    and/or trim.

    Args:
      url: URL to parse. If None, reads `handle.url` from the runner's
           current-step ContextVar. Outside a step (e.g. unit tests), pass
           explicitly.
      expected_url: optional matcher callable (any factory from
           `webauto._matchers` — `startswith`, `equals`, `regex`,
           `any_url`, etc.). Receives the URL string (after `strip_url` is
           applied) and must return True; raises `ValueError` if False.
      expected_query_string: optional dict; each key/value pair must match
           the corresponding parsed query parameter. Raises `ValueError` on
           missing key or value mismatch. Extra params in the URL are fine.
      include_base_url: include the `base_url` key in the output (default).
           Set False if you only want the query params.
      strip_url: optional prefix to strip from `url` *before* parsing. If the
           URL starts with `strip_url`, the prefix is removed; `base_url` in
           the result is then the remaining path (e.g. `/callback`). Useful
           when relay URLs contain dynamic IDs you want to normalize away.
           If the URL doesn't start with `strip_url`, raises `ValueError`.

    Returns:
      Dict with `base_url` (unless `include_base_url=False`) plus every query
      parameter as a top-level key. Repeated keys (`?x=1&x=2`) become list
      values; single values are bare strings; blank values are preserved as
      empty strings.

    Raises:
      RuntimeError: `url` is None and no handle is bound in context.
      ValueError:   any validation or strip mismatch.

    Examples:
      >>> parse_url("https://h/cb?code=abc&state=s1")
      {'base_url': 'https://h/cb', 'code': 'abc', 'state': 's1'}

      >>> parse_url("https://h/cb?x=1&x=2&y=3")
      {'base_url': 'https://h/cb', 'x': ['1', '2'], 'y': '3'}

      >>> parse_url("https://h/cb?code=abc&state=s1", include_base_url=False)
      {'code': 'abc', 'state': 's1'}

      >>> parse_url("https://relay/abc123/callback?code=x", strip_url="https://relay/abc123")
      {'base_url': '/callback', 'code': 'x'}
    """
    if url is None:
        try:
            handle = _current_handle_ctx.get()
        except LookupError as exc:
            raise RuntimeError(
                "parse_url() called without a url argument and no handle is "
                "bound in context (call this from inside an action step, or "
                "pass url= explicitly)"
            ) from exc
        url = handle.url

    if strip_url is not None:
        if not url.startswith(strip_url):
            raise ValueError(
                f"parse_url: strip_url={strip_url!r} is not a prefix of "
                f"URL {url!r}"
            )
        url = url[len(strip_url):]

    if expected_url is not None and not expected_url(url):
        raise ValueError(
            f"parse_url: URL {url!r} did not match the expected_url predicate"
        )

    split = urlsplit(url)
    if split.scheme or split.netloc:
        base_url = f"{split.scheme}://{split.netloc}{split.path}"
    else:
        base_url = split.path

    params_multi = parse_qs(split.query, keep_blank_values=True)
    flat: dict[str, Any] = {
        k: (v[0] if len(v) == 1 else v) for k, v in params_multi.items()
    }

    if expected_query_string is not None:
        for key, expected in expected_query_string.items():
            actual = flat.get(key)
            if actual != expected:
                raise ValueError(
                    f"parse_url: query param {key}={actual!r} did not match "
                    f"expected {expected!r}"
                )

    result: dict[str, Any] = {}
    if include_base_url:
        result["base_url"] = base_url
    result.update(flat)
    return result
