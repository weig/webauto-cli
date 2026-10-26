"""Unit tests for SeleniumBaseHandle helpers that don't need a real browser.

The handle itself drives a SeleniumBase UC driver and can't be exercised
in CI without a Chrome install — but the window-stranding fix introduced
`_score_window_url`, which is a pure function and is the most failure-prone
piece. Cover it here.
"""

from __future__ import annotations

import pytest

from webauto._seleniumbase_handle import _score_window_url


class TestScoreWindowUrlNoTarget:
    def test_https_url_beats_chrome_internal(self) -> None:
        assert _score_window_url("https://schwab.com/login") > _score_window_url(
            "chrome://settings/"
        )

    def test_http_and_https_both_score_3(self) -> None:
        assert _score_window_url("https://example.com/") == 3
        assert _score_window_url("http://example.com/") == 3

    def test_new_tab_variants_are_real_blank_tabs(self) -> None:
        # A real but blank tab (about to be navigated). Must score above
        # chrome-internal UI overlays so we pick it before something like
        # chrome://omnibox-popup.
        for url in (
            "chrome://newtab/",
            "chrome://new-tab-page/",
            "chrome-search://local-ntp/",
            "about:blank",
            "data:,",
            "",
        ):
            assert _score_window_url(url) == 2, url

    def test_chrome_ui_overlays_score_zero(self) -> None:
        # CDP exposes Chrome's own UI surfaces as window handles when
        # remote-debugging-port is set. Driving them paints the URL on top
        # of the host tab as an overlay — the address bar stays blank and
        # closing them takes the host tab with them. Must never be chosen.
        for url in (
            "chrome://omnibox-popup.top-chrome/",
            "chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html",
            "chrome://print-dialog/",
            "devtools://devtools/bundled/devtools_app.html",
        ):
            assert _score_window_url(url) == 0, url

    def test_real_blank_tab_beats_chrome_overlay(self) -> None:
        # Regression: the omnibox-popup window must score lower than a
        # real new-tab-page — otherwise we drive the popup and the
        # actual tab stays blank.
        ntp = _score_window_url("chrome://new-tab-page/")
        popup = _score_window_url("chrome://omnibox-popup.top-chrome/")
        assert ntp > popup, f"new-tab-page={ntp}, popup={popup}"

    def test_other_chrome_pages_score_below_blank(self) -> None:
        # Settings/history aren't a real navigable tab; rank below a
        # genuinely-blank new tab.
        assert (
            _score_window_url("chrome://settings/")
            < _score_window_url("chrome://new-tab-page/")
        )


class TestScoreWindowUrlWithTarget:
    def test_target_match_wins_over_other_http(self) -> None:
        target = "https://api.schwabapi.com/v1/oauth/authorize?x=1"
        match = _score_window_url(
            "https://api.schwabapi.com/v1/oauth/authorize", target
        )
        other = _score_window_url("https://google.com/", target)
        assert match > other
        assert match == 5

    def test_target_query_stripped_before_match(self) -> None:
        # We strip ?... from the target URL so Schwab redirecting to its
        # own /login-one-step with a different query still scores high
        # (it's the same origin we just navigated to).
        target = "https://api.schwabapi.com/v1/oauth/authorize?response_type=code"
        landed = "https://api.schwabapi.com/v1/oauth/authorize"
        assert _score_window_url(landed, target) == 5

    def test_unrelated_http_still_beats_blank(self) -> None:
        # If we navigated to schwab.com but the active window is on
        # google.com (shouldn't happen, but defensively), pick that over
        # an about:blank — at least it's a real page.
        target = "https://schwab.com/login"
        assert _score_window_url("https://google.com/", target) > _score_window_url(
            "about:blank", target
        )

    def test_target_none_falls_back_to_scheme_scoring(self) -> None:
        assert _score_window_url("https://x/", None) == 3
        assert _score_window_url("about:blank", None) == 2


class TestCloseParksOnBlank:
    """`close()` must navigate away from the OAuth callback URL before
    quitting, so the persistent profile doesn't restore (and replay) a
    stale `?code=...` callback on the next launch."""

    def _make_handle(self, driver):
        # Bypass __init__ (it spins up a real Chrome); inject a fake
        # driver and the headless flag the method reads.
        from webauto._seleniumbase_handle import SeleniumBaseHandle
        h = SeleniumBaseHandle.__new__(SeleniumBaseHandle)
        h._driver = driver
        h._headless = True
        return h

    def test_navigates_to_blank_then_quits(self) -> None:
        calls: list[tuple[str, str]] = []

        class _FakeDriver:
            def default_get(self, url):
                calls.append(("default_get", url))
            def get(self, url):
                calls.append(("get", url))
            def quit(self):
                calls.append(("quit", ""))

        self._make_handle(_FakeDriver()).close()

        assert ("default_get", "about:blank") in calls
        assert ("quit", "") in calls
        # blank navigation must happen BEFORE quit
        assert calls.index(("default_get", "about:blank")) < calls.index(("quit", ""))

    def test_quit_still_runs_if_blank_nav_raises(self) -> None:
        calls: list[str] = []

        class _AngryDriver:
            def default_get(self, url):
                raise RuntimeError("nav boom")
            def get(self, url):
                raise RuntimeError("nav boom")
            def quit(self):
                calls.append("quit")

        # close() must swallow the nav failure and still quit.
        self._make_handle(_AngryDriver()).close()
        assert calls == ["quit"]

    def test_falls_back_to_get_when_no_default_get(self) -> None:
        calls: list[tuple[str, str]] = []

        class _PlainDriver:
            # No default_get attribute → close() falls back to .get
            def get(self, url):
                calls.append(("get", url))
            def quit(self):
                calls.append(("quit", ""))

        self._make_handle(_PlainDriver()).close()
        assert ("get", "about:blank") in calls
        assert ("quit", "") in calls


class TestSeleniumKeyMapping:
    """press() key names follow Playwright; unknown names must fail loudly."""

    @pytest.mark.parametrize("name, attr", [
        ("Escape", "ESCAPE"), ("Enter", "ENTER"), ("Tab", "TAB"),
        ("ArrowDown", "ARROW_DOWN"), ("PageUp", "PAGE_UP"), ("Backspace", "BACKSPACE"),
    ])
    def test_named_keys_map_to_selenium_constants(self, name, attr) -> None:
        from selenium.webdriver.common.keys import Keys
        from webauto._seleniumbase_handle import selenium_key

        assert selenium_key(name) == getattr(Keys, attr)

    @pytest.mark.parametrize("char", ["a", "+", "7"])
    def test_single_characters_are_sent_literally(self, char) -> None:
        from webauto._seleniumbase_handle import selenium_key

        assert selenium_key(char) == char

    def test_unknown_key_raises(self) -> None:
        from webauto._seleniumbase_handle import selenium_key

        with pytest.raises(ValueError, match="unknown key 'Hyper'"):
            selenium_key("Hyper")

    def test_combination_raises(self) -> None:
        from webauto._seleniumbase_handle import selenium_key

        with pytest.raises(ValueError, match="combination"):
            selenium_key("Control+A")

    def test_press_sends_mapped_key_through_action_chains(self, monkeypatch) -> None:
        from selenium.webdriver.common.keys import Keys
        from webauto._seleniumbase_handle import SeleniumBaseHandle

        sent = []

        class FakeChains:
            def __init__(self, driver) -> None:
                self.driver = driver

            def send_keys(self, key):
                sent.append(key)
                return self

            def perform(self) -> None:
                sent.append("performed")

        monkeypatch.setattr("selenium.webdriver.common.action_chains.ActionChains", FakeChains)
        handle = object.__new__(SeleniumBaseHandle)
        handle._driver = object()

        handle.press("ArrowDown")

        assert sent == [Keys.ARROW_DOWN, "performed"]


class TestSeleniumEvaluate:
    def test_wraps_function_source_for_execute_script(self) -> None:
        from webauto._seleniumbase_handle import SeleniumBaseHandle

        calls = []

        class FakeDriver:
            def execute_script(self, script, *args):
                calls.append((script, args))
                return "ok"

        handle = object.__new__(SeleniumBaseHandle)
        handle._driver = FakeDriver()

        assert handle.evaluate("(x) => x", 5) == "ok"
        assert calls == [("return ((x) => x)(arguments[0]);", (5,))]


class TestSeleniumFrameSelectors:
    def _handle(self, frames=1, inner=1):
        from webauto._seleniumbase_handle import SeleniumBaseHandle

        log = []

        class SwitchTo:
            def frame(self, el):
                log.append(("frame", el))

            def default_content(self):
                log.append(("default",))

        class FakeDriver:
            switch_to = SwitchTo()

            def find_elements(self, by, value):
                log.append(("find", value))
                if "iframe" in value:
                    return ["FRAME"] * frames
                return ["EL"] * inner

        handle = object.__new__(SeleniumBaseHandle)
        handle._driver = FakeDriver()
        return handle, log

    def test_split_frame_selector(self) -> None:
        from webauto._selectors import split_frame

        assert split_frame("//iframe[@id='f'] >> //input") == ("//iframe[@id='f']", "//input")
        assert split_frame("//input[@id='x']") == (None, "//input[@id='x']")

    def test_count_switches_into_frame_and_back(self) -> None:
        handle, log = self._handle(frames=1, inner=2)

        assert handle.count("//iframe[@id='f'] >> //input") == 2
        assert log == [("find", "//iframe[@id='f']"), ("frame", "FRAME"), ("find", "//input"), ("default",)]

    def test_missing_frame_counts_zero_without_switching(self) -> None:
        handle, log = self._handle(frames=0)

        assert handle.count("//iframe[@id='f'] >> //input") == 0
        assert ("frame", "FRAME") not in log

    def test_ambiguous_frame_raises(self) -> None:
        handle, _ = self._handle(frames=2)

        with pytest.raises(ValueError, match="2 frames"):
            handle.count("//iframe >> //input")


class TestSeleniumClickPosition:
    def test_offset_is_converted_from_top_left_to_centre(self, monkeypatch) -> None:
        from webauto._seleniumbase_handle import SeleniumBaseHandle

        moves = []

        class FakeEl:
            size = {"width": 40, "height": 60}

        class FakeChains:
            def __init__(self, driver) -> None:
                pass

            def move_to_element_with_offset(self, el, x, y):
                moves.append((x, y))
                return self

            def click(self):
                moves.append("click")
                return self

            def perform(self) -> None:
                moves.append("perform")

        class FakeDriver:
            def find_elements(self, by, value):
                return [FakeEl()]

        monkeypatch.setattr("selenium.webdriver.common.action_chains.ActionChains", FakeChains)
        handle = object.__new__(SeleniumBaseHandle)
        handle._driver = FakeDriver()

        handle.click("#icon", position=(20, 5))

        assert moves == [(0, -25), "click", "perform"]
