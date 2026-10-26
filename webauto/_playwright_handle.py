"""BrowserHandle backed by Playwright (the default backend).

Environment:
  HEADLESS         headless unless set to 0/false/no/off
  WEBAUTO_CDP_URL  attach to an already running Chrome (e.g. one a person is
                   watching) instead of launching; that browser is left open
  WEBAUTO_PW_CHANNEL  browser channel to launch (default "chrome", the
                   system install, so no `playwright install` is needed)

Selectors: XPath when they start with "/" or "(" (or "xpath="), webauto's
`text=` substring form, otherwise CSS. fill/click are strict: a selector
matching more than one element raises instead of acting on the first.
Screenshots always mask password inputs.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from playwright.sync_api import sync_playwright

_FALSY = ("0", "false", "no", "off")


def _headless() -> bool:
    return os.environ.get("HEADLESS", "1").strip().lower() not in _FALSY


def to_playwright_selector(selector: str) -> str:
    if selector.startswith("text="):
        text = selector[len("text="):]
        return f"xpath=//*[contains(normalize-space(.), {text!r})]"
    if selector.startswith(("/", "(")):
        return f"xpath={selector}"
    return selector


class PlaywrightHandle:
    def __init__(self, cdp_url: Optional[str] = None, channel: Optional[str] = None) -> None:
        self._pw = sync_playwright().start()
        cdp_url = cdp_url or os.environ.get("WEBAUTO_CDP_URL")
        try:
            if cdp_url:
                self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
                self._owns_browser = False
                context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
                self._page = context.pages[0] if context.pages else context.new_page()
            else:
                self._browser = self._pw.chromium.launch(
                    channel=channel or os.environ.get("WEBAUTO_PW_CHANNEL", "chrome"),
                    headless=_headless(),
                )
                self._owns_browser = True
                self._page = self._browser.new_context().new_page()
        except Exception:
            self._pw.stop()
            raise

    def _locator(self, selector: str) -> Any:
        return self._page.locator(to_playwright_selector(selector))

    # ------------------------------------------------------------------
    # BrowserHandle Protocol
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        return self._page.url

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="load")

    def fill(self, selector: str, value: str) -> None:
        self._locator(selector).fill(value)

    def click(self, selector: str) -> None:
        self._locator(selector).click()

    def check(self, selector: str) -> None:
        self._locator(selector).check()

    def uncheck(self, selector: str) -> None:
        self._locator(selector).uncheck()

    def check_all(self, selector: str) -> None:
        loc = self._locator(selector)
        for i in range(loc.count()):
            loc.nth(i).check()

    def wait_for_selector(self, selector: str, timeout: float = 30.0) -> None:
        self._locator(selector).first.wait_for(state="attached", timeout=timeout * 1000)

    def wait_for_url(self, substring: str, timeout: float = 30.0) -> None:
        self._page.wait_for_url(lambda u: substring in u, timeout=timeout * 1000)

    def wait_for_load(self, timeout: float = 30.0) -> None:
        self._page.wait_for_load_state("load", timeout=timeout * 1000)

    def sleep_ms(self, ms: int) -> None:
        self._page.wait_for_timeout(ms)

    def press(self, key: str) -> None:
        self._page.keyboard.press(key)

    def evaluate(self, script: str, arg: Any = None) -> Any:
        """Run a JS function source (e.g. "(x) => x * 2") in the page with one argument."""
        return self._page.evaluate(script, arg)

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path, full_page=True, mask=[self._page.locator("input[type=password]")])

    def is_visible(self, selector: str) -> bool:
        # Same contract as SeleniumBaseHandle: "is present in the DOM".
        return self.count(selector) > 0

    def count(self, selector: str) -> int:
        return self._locator(selector).count()

    def close(self) -> None:
        try:
            if self._owns_browser:
                self._browser.close()
        finally:
            self._pw.stop()
