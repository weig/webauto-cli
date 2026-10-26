"""Pick a browser backend. Playwright is the default; SeleniumBase is opt-in.

Selection order: explicit `browser=` argument, then `WEBAUTO_BROWSER`, then
"playwright". Backends are imported lazily so an unused one is never loaded.
"""

from __future__ import annotations

import os
from typing import Any, Optional

BROWSERS = ("playwright", "seleniumbase")
DEFAULT_BROWSER = "playwright"


def _playwright() -> Any:
    from webauto._playwright_handle import PlaywrightHandle

    return PlaywrightHandle()


def _seleniumbase() -> Any:
    from webauto._seleniumbase_handle import SeleniumBaseHandle

    return SeleniumBaseHandle()


def resolve_browser(browser: Optional[str] = None) -> str:
    chosen = (browser or os.environ.get("WEBAUTO_BROWSER") or DEFAULT_BROWSER).strip().lower()
    if chosen not in BROWSERS:
        raise ValueError(f"unknown browser {chosen!r}; choose one of {', '.join(BROWSERS)}")
    return chosen


def make_handle(browser: Optional[str] = None) -> Any:
    return _seleniumbase() if resolve_browser(browser) == "seleniumbase" else _playwright()
