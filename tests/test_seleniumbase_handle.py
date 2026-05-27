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
