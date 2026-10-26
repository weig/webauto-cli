"""SeleniumBase UC `BrowserHandle` implementation.

Ported from schwab_cli/browser/_seleniumbase_flow.py — incorporates the
Akamai-bypass tricks and Angular-reactive-form interaction patterns that
were proven necessary against Schwab. The original module is the source
of truth for *why* each trick is here; the comments below summarize.
"""

from __future__ import annotations

import functools
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

from webauto._chrome_bootstrap import resolve_chrome_version
from webauto._chrome_clone import sweep_chrome_code_sign_clones


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _debug() -> bool:
    return _truthy(os.environ.get("DEBUG"))


def _dlog(msg: str) -> None:
    if _debug():
        print(f"[ba.handle] {msg}", file=sys.stderr, flush=True)


def _user_data_dir() -> Path:
    """Return the persistent Chromium profile dir, migrating from the old name.

    Before the project was renamed `browser_automation` → `webauto`, the
    profile lived at `~/.cache/browser_automation/chromium-uc/`. Move it
    in-place on first access so the trust-device cookie (and any other
    Schwab session state) survives the upgrade without forcing the user
    to re-MFA.
    """
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    new_root = Path(base) / "webauto"
    new_path = new_root / "chromium-uc"
    old_path = Path(base) / "browser_automation" / "chromium-uc"

    if old_path.exists():
        # If a previous run created `new_path` but it's empty (no real profile
        # state), clear it out so we can move the old one into place.
        if new_path.exists():
            try:
                is_empty = not any(new_path.iterdir())
            except OSError:
                is_empty = False
            if is_empty:
                try:
                    new_path.rmdir()
                except OSError:
                    pass
        if not new_path.exists():
            new_root.mkdir(parents=True, exist_ok=True)
            try:
                old_path.rename(new_path)
                _dlog(f"migrated profile dir: {old_path} -> {new_path}")
                try:
                    old_path.parent.rmdir()
                except OSError:
                    pass
            except OSError as e:
                _dlog(f"could not migrate profile dir ({e}); falling back to fresh")

    new_path.mkdir(parents=True, exist_ok=True)
    try:
        new_path.chmod(0o700)
    except OSError:
        pass
    return new_path


def _pick_free_port() -> int:
    """Return a free 127.0.0.1 port. SB UC's default 9222 collides with
    chrome-devtools-mcp and other tools squatting on it; pick ephemeral
    ourselves and pass via chromium_arg.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Driver-level navigation reconnect window (UC mode). 4s is the value
# schwab_cli proved out on Schwab. Less = sometimes Akamai blocks; more =
# wasted wall-clock on every nav.
_RECONNECT_SECONDS = 4


# URL prefixes for windows that are clearly "blank" leftovers from UC
# launch. When we find a real http(s) window alongside one of these we
# close the blank — otherwise the driver sometimes lands on it and
# handle.url reports chrome://new-tab-page/ forever, which never matches
# any PageAction (the runner spins until timeout).
_BLANK_URL_PREFIXES = (
    "chrome://newtab",
    "chrome://new-tab-page",
    "chrome-search://",
)
_BLANK_URLS = ("about:blank", "data:,", "")


def _score_window_url(url: str, target_url: Optional[str] = None) -> int:
    """Score a window's URL — higher means more likely to be a real tab.

    5: starts with the URL we just navigated to (origin + path prefix).
    3: any other http(s) page.
    2: a real but blank tab (about:blank / chrome://new-tab-page / ...).
    1: some other chrome:// page (settings/history) — usually not what we want.
    0: chrome-internal UI overlays (omnibox-popup, print preview, devtools).

    Why omnibox-popup gets 0: when Chrome runs with --remote-debugging-port,
    CDP exposes Chrome's own UI surfaces (the address-bar suggestions
    dropdown, devtools, etc.) as window handles. They look like real targets
    but navigating them renders the page *on top of* the real tab as an
    overlay — never as a normal page — and closing the host tab tears them
    down with it. They must never be picked as the "main" window.
    """
    # Chrome-internal UI overlays exposed by CDP — never a real tab.
    if url.startswith("chrome://") and ("popup" in url or "dialog" in url):
        return 0
    if url.startswith("devtools://"):
        return 0

    if target_url:
        base = target_url.split("?")[0].rstrip("/")
        if base and url.startswith(base):
            return 5
    if url.startswith(("http://", "https://")):
        return 3
    if url.startswith(_BLANK_URL_PREFIXES) or url in _BLANK_URLS:
        return 2
    if url.startswith("chrome://"):
        return 1
    return 0


def _frame_aware(method):
    """Run a selector method inside the iframe named by a "<frame> >> <element>" selector.

    Switches into the single matching iframe for the duration of the call and
    always switches back. A missing frame makes count() 0 and is_visible()
    False; other methods raise LookupError. More than one matching frame raises.
    """

    @functools.wraps(method)
    def wrapper(self, selector, *args, **kwargs):
        from webauto._selectors import split_frame

        frame, inner = split_frame(selector)
        if frame is None:
            return method(self, selector, *args, **kwargs)
        frames = self._find_elements(frame)
        if len(frames) > 1:
            raise ValueError(f"{len(frames)} frames match {frame!r}; expected one")
        if not frames:
            if method.__name__ == "count":
                return 0
            if method.__name__ == "is_visible":
                return False
            raise LookupError(f"no frame matches {frame!r}")
        self._driver.switch_to.frame(frames[0])
        try:
            return method(self, inner, *args, **kwargs)
        finally:
            self._driver.switch_to.default_content()

    return wrapper


def selenium_key(key: str) -> str:
    """Map a Playwright key name ("Escape", "ArrowDown", "a") to what Selenium sends.

    Named keys become Selenium Keys constants (ArrowDown -> ARROW_DOWN); a
    single character is sent as-is. Anything else raises rather than being
    typed into the page as literal text. Combinations ("Control+A") are not
    supported on this backend.
    """
    from selenium.webdriver.common.keys import Keys
    if len(key) == 1:
        return key
    if "+" in key:
        raise ValueError(f"key combination {key!r} is not supported by the SeleniumBase backend")
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", key).upper()
    if hasattr(Keys, name):
        return getattr(Keys, name)
    raise ValueError(f"unknown key {key!r}")


class SeleniumBaseHandle:
    """Concrete BrowserHandle backed by SeleniumBase's UC driver.

    Built specifically to survive Akamai bot detection (Schwab's case):
      - UC mode (`uc=True`) plus a chosen remote-debugging-port.
      - Headless: uses `uc_open_with_reconnect` (detach/reconnect dance).
      - Visible: uses `default_get` (UC's `.open` does the wrong thing with
        a sole visible window).
      - `_switch_to_main_window` after construction so we don't drive the
        Omnibox popup that SB UC sometimes attaches to.
      - Form fills use JS event dispatch (focus + .value = val +
        input/change/blur) — Angular reactive forms ignore Selenium's
        plain `send_keys` value sets.
      - Checkbox toggles set `.checked` and dispatch the same event trio.
      - Button clicks escalate: native -> JS click -> synthesized
        MouseEvent (composed through shadow DOM).
    """

    def __init__(self) -> None:
        from seleniumbase import Driver  # heavy; import lazily

        # Sweep any Chrome code-sign clones stranded by prior runs BEFORE
        # launching — this heals whatever a SIGKILLed session left behind,
        # regardless of how it died (the caller terminates our process group,
        # so Chrome's own cleanup helper never runs). Unbounded, these dir
        # entries eventually stall macOS boot. Best-effort, never raises.
        swept = sweep_chrome_code_sign_clones()
        if swept:
            _dlog(f"swept {swept} orphaned Chrome code-sign clone(s) at launch")

        chrome_version = resolve_chrome_version()
        # Headless by default. Set HEADLESS=0 (or false/no/off) for a visible
        # window — useful when debugging selector drift on a new Schwab UI.
        self._headless = _truthy(os.environ.get("HEADLESS", "1"))
        debug_port = _pick_free_port()
        user_data_dir = str(_user_data_dir())

        _dlog(
            f"launching uc driver: headless={self._headless}, "
            f"chrome_version={chrome_version}, profile={user_data_dir}, "
            f"debug_port={debug_port}"
        )

        try:
            self._driver = Driver(
                uc=True,
                headless=self._headless,
                user_data_dir=user_data_dir,
                driver_version=str(chrome_version),
                log_cdp_events=True,
                chromium_arg=f"--remote-debugging-port={debug_port}",
            )
        except Exception as e:
            raise RuntimeError(
                f"Failed to launch SeleniumBase UC driver: {e}"
            ) from e

        _dlog("driver ready, switching to main window")
        self._switch_to_main_window()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _switch_to_main_window(self, target_url: Optional[str] = None) -> None:
        """Point the driver at the most "real" window.

        SB UC under --remote-debugging-port exposes Chrome's own UI overlays
        (omnibox-popup, devtools, etc.) as window handles. If we leave the
        driver attached to one of those, `uc_open_with_reconnect` will load
        the URL into the overlay instead of a real tab — visually it looks
        like the page rendered correctly, but it's painted on top of a still-
        blank "New Tab" window and the address bar stays empty. We never
        close anything (Chrome's overlay handles are tied to the host tab —
        closing them takes both down); we only switch.
        """
        try:
            handles = list(self._driver.window_handles)
        except Exception:
            return
        if not handles:
            return

        surveyed: list[tuple[str, str]] = []
        for h in handles:
            try:
                self._driver.switch_to.window(h)
                url = self._driver.current_url or ""
            except Exception:
                continue
            surveyed.append((h, url))

        if not surveyed:
            return

        if _debug():
            _dlog(
                f"_switch_to_main_window: target={target_url!r} "
                f"handles={len(surveyed)}"
            )
            for h, url in surveyed:
                _dlog(
                    f"  - {h[:8]}: {url[:120]!r} "
                    f"score={_score_window_url(url, target_url)}"
                )

        surveyed.sort(
            key=lambda hu: _score_window_url(hu[1], target_url),
            reverse=True,
        )
        best_handle, _best_url = surveyed[0]

        try:
            self._driver.switch_to.window(best_handle)
        except Exception:
            pass

    def _find_elements(self, selector: str) -> list:
        """Find elements by XPath ("/" or "(" prefix), `text=` substring, or CSS."""
        from selenium.webdriver.common.by import By
        if selector.startswith("text="):
            text = selector[len("text="):]
            xp = f"//*[contains(normalize-space(.), {repr(text)})]"
            return self._driver.find_elements(By.XPATH, xp)
        if selector.startswith(("/", "(")):
            return self._driver.find_elements(By.XPATH, selector)
        return self._driver.find_elements(By.CSS_SELECTOR, selector)

    # ------------------------------------------------------------------
    # BrowserHandle Protocol
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        return self._driver.current_url

    def goto(self, url: str) -> None:
        """Navigate to `url`. Headless uses UC's reconnect dance; visible
        uses Selenium's plain .get (saved as default_get by SB UC).
        """
        _dlog(f"goto: {url[:120]!r}")
        if self._headless:
            # `uc_open_with_reconnect`: open URL, close blank tab, then
            # detach/reconnect the driver to dodge Akamai's detection of
            # an actively-attached debugger.
            self._driver.uc_open_with_reconnect(
                url, reconnect_time=_RECONNECT_SECONDS
            )
        else:
            # Visible mode: SB UC patches driver.get to do the same
            # detach trick — which hangs in visible mode because Chrome
            # can't close the only visible window in 20s. default_get is
            # the original Selenium .get saved before UC patching.
            self._driver.default_get(url)
        # SB UC sometimes leaves the driver attached to a chrome-internal
        # UI overlay (e.g. omnibox-popup) rather than the real tab. Pass
        # the target URL so the switch logic prefers the window that
        # actually navigated to it.
        self._switch_to_main_window(target_url=url)
        if _debug():
            try:
                _dlog(f"goto complete: active url={self._driver.current_url!r}")
            except Exception as e:
                _dlog(f"goto complete: current_url unavailable ({e!r})")

    @_frame_aware
    def fill(self, selector: str, value: str) -> None:
        """Set an input's value via JS + event dispatch.

        Schwab's Angular reactive forms ignore plain Selenium .send_keys
        — the model never binds. The focus + .value = val + input/change/
        blur sequence mirrors what real typing looks like to Angular.
        """
        els = self._find_elements(selector)
        if not els:
            raise AssertionError(f"fill: selector {selector!r} not present")
        el = els[0]
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el
            )
        except Exception:
            pass
        self._driver.execute_script(
            """
            const el = arguments[0];
            const val = arguments[1];
            el.focus();
            el.value = val;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
            """,
            el,
            value,
        )

    @_frame_aware
    def click(self, selector: str) -> None:
        """Click a button with escalating strategies.

        Some Schwab controls (the Informed Consent Accept button) live
        inside a web component's shadow root; native click works but
        synthesized MouseEvents need `composed: true` to cross the
        boundary. Try native first (cheapest), JS click second, full
        MouseEvent sequence last.
        """
        els = self._find_elements(selector)
        if not els:
            raise AssertionError(f"click: selector {selector!r} not present")
        el = els[0]
        try:
            self._driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el
            )
        except Exception:
            pass
        # 1: native Selenium click (real browser-level click)
        try:
            el.click()
            return
        except Exception:
            pass
        # 2: HTMLElement.click() — bypasses Selenium's interactability heuristic
        try:
            self._driver.execute_script("arguments[0].click();", el)
            return
        except Exception:
            pass
        # 3: synthesized MouseEvent (composed for shadow DOM)
        self._driver.execute_script(
            """
            const el = arguments[0];
            const rect = el.getBoundingClientRect();
            const opts = {
                bubbles: true, cancelable: true, view: window, composed: true,
                clientX: rect.left + rect.width / 2,
                clientY: rect.top + rect.height / 2,
                button: 0,
            };
            el.dispatchEvent(new MouseEvent('mousedown', opts));
            el.dispatchEvent(new MouseEvent('mouseup', opts));
            el.dispatchEvent(new MouseEvent('click', opts));
            """,
            el,
        )

    def _set_checkbox(self, selector: str, checked: bool) -> None:
        """Check/uncheck a checkbox or radio via JS + event dispatch."""
        els = self._find_elements(selector)
        if not els:
            raise AssertionError(f"checkbox {selector!r} not present")
        self._driver.execute_script(
            """
            const el = arguments[0];
            const target = Boolean(arguments[1]);
            if (el.checked !== target) {
                el.checked = target;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }
            """,
            els[0],
            checked,
        )

    @_frame_aware
    def check(self, selector: str) -> None:
        self._set_checkbox(selector, True)

    @_frame_aware
    def uncheck(self, selector: str) -> None:
        self._set_checkbox(selector, False)

    @_frame_aware
    def check_all(self, selector: str) -> None:
        elements = self._find_elements(selector)
        for el in elements:
            self._driver.execute_script(
                """
                const el = arguments[0];
                if (!el.checked) {
                    el.checked = true;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
                """,
                el,
            )

    @_frame_aware
    def wait_for_selector(self, selector: str, timeout: float = 30.0) -> None:
        """Wait for `selector` to be present in the DOM (not just visible).

        Selenium's `is_displayed()` misfires on Angular-hydrated forms that
        are plainly on screen; presence is a safer signal.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._find_elements(selector):
                    return
            except Exception:
                pass
            time.sleep(0.2)
        raise TimeoutError(
            f"wait_for_selector: {selector!r} not present after {timeout}s "
            f"(current url: {self.url!r})"
        )

    def wait_for_url(self, substring: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if substring in self.url:
                return
            time.sleep(0.2)
        raise TimeoutError(
            f"wait_for_url: {substring!r} not in URL after {timeout}s "
            f"(current: {self.url!r})"
        )

    def wait_for_load(self, timeout: float = 30.0) -> None:
        try:
            self._driver.wait_for_ready_state_complete(timeout=timeout)
        except Exception:
            # SB raises on timeout; surface a more helpful message.
            raise TimeoutError(
                f"wait_for_load: page did not reach ready state in {timeout}s "
                f"(current: {self.url!r})"
            )

    def sleep_ms(self, ms: int) -> None:
        time.sleep(ms / 1000.0)

    def evaluate(self, script: str, arg: Any = None) -> Any:
        """Run a JS function source in the page with one argument, like Playwright's evaluate.

        Unlike Playwright, a returned Promise is not awaited (execute_script gives {}).
        """
        return self._driver.execute_script(f"return ({script})(arguments[0]);", arg)

    def press(self, key: str) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self._driver).send_keys(selenium_key(key)).perform()

    def screenshot(self, path: str) -> None:
        self._driver.save_screenshot(path)

    @_frame_aware
    def is_visible(self, selector: str) -> bool:
        try:
            els = self._find_elements(selector)
            return bool(els)
        except Exception:
            return False

    @_frame_aware
    def count(self, selector: str) -> int:
        return len(self._find_elements(selector))

    def close(self) -> None:
        # Park on about:blank BEFORE quitting. The persistent profile
        # (kept on purpose, so the device stays trusted and Schwab
        # doesn't re-trigger mobile verification) restores the last tab
        # on next launch. If that tab is an OAuth callback URL — it
        # carries `?code=...` — the browser re-requests it on startup
        # and replays a *stale* code to the relay before the new auth
        # flow runs, breaking auth with a state mismatch. Navigating to
        # about:blank leaves the cookie jar untouched while guaranteeing
        # nothing sensitive sits in the restored tab.
        try:
            nav = getattr(self._driver, "default_get", None) or self._driver.get
            nav("about:blank")
        except Exception:
            pass
        try:
            self._driver.quit()
        except Exception:
            pass
        # Clear this run's clone promptly on a clean exit. The launch-time
        # sweep is the SIGKILL-proof backstop; this just avoids leaving one
        # sitting around between runs.
        sweep_chrome_code_sign_clones()

    # ------------------------------------------------------------------
    # Schwab-specific helpers exposed for `custom(...)` steps that need
    # finer-grained control. Not part of BrowserHandle Protocol.
    # ------------------------------------------------------------------

    def execute_script(self, script: str, *args: Any) -> Any:
        """Run arbitrary JS in the page context. Use sparingly from `custom`."""
        return self._driver.execute_script(script, *args)

    def wait_for_hydration(self, timeout: float = 8.0) -> None:
        """Pause until Angular has hydrated bindings.

        Schwab's post-login pages ship as an Angular shell where buttons
        exist in the DOM but click handlers aren't wired yet — JS-dispatched
        events get ignored until bootstrap finishes. The placeholder
        ``<title>cag</title>`` is a reliable "not hydrated yet" signal.
        """
        deadline = time.monotonic() + timeout
        start = time.monotonic()
        while time.monotonic() < deadline:
            try:
                title = self._driver.execute_script("return document.title || '';")
                if title and title != "cag":
                    break
            except Exception:
                pass
            time.sleep(0.2)
        # Floor of 500ms gives handlers time to attach even if the title
        # has already settled.
        time.sleep(max(0.0, 0.5 - (time.monotonic() - start)))
