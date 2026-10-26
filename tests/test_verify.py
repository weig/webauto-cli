"""Unit tests for verification steps: verify_all, click_if_unique, unexpected_page."""

from __future__ import annotations

import pytest

from webauto._verify import (
    UnexpectedPage,
    VerificationFailed,
    click_if_unique,
    expect,
    unexpected_page,
    verify_all,
)


class TestVerifyAll:
    def test_passes_when_every_selector_matches_once(self, handle) -> None:
        handle.visible |= {"//form", "#user"}

        verify_all(expect("form", "//form"), expect("user", "#user"))(handle)

        assert handle.calls == []

    def test_reports_every_check_not_only_the_first_failure(self, handle) -> None:
        handle.visible.add("#user")
        handle.counts["#btn"] = 2

        with pytest.raises(VerificationFailed) as err:
            verify_all(
                expect("form", "//form"),
                expect("user", "#user"),
                expect("button", "#btn"),
            )(handle)

        results = {r.name: (r.ok, r.actual) for r in err.value.results}
        assert results == {"form": (False, 0), "user": (True, 1), "button": (False, 2)}

    def test_message_lists_matches_and_mismatches(self, handle) -> None:
        handle.visible.add("#user")

        with pytest.raises(VerificationFailed) as err:
            verify_all(expect("form", "//form"), expect("user", "#user"))(handle)

        text = str(err.value)
        assert "✗ form" in text and "expected 1, found 0" in text
        assert "✓ user" in text

    def test_custom_expected_count(self, handle) -> None:
        handle.counts[".row"] = 3

        verify_all(expect("rows", ".row", count=3))(handle)

    def test_screenshot_taken_only_on_failure(self, handle) -> None:
        handle.visible.add("#ok")
        verify_all(expect("ok", "#ok"), screenshot="/tmp/ok.png")(handle)
        assert handle.calls == []

        with pytest.raises(VerificationFailed):
            verify_all(expect("missing", "#nope"), screenshot="/tmp/fail.png")(handle)
        assert handle.calls == [("screenshot", "/tmp/fail.png")]

    def test_requires_at_least_one_expectation(self) -> None:
        with pytest.raises(ValueError):
            verify_all()


class TestClickIfUnique:
    def test_clicks_when_exactly_one_match(self, handle) -> None:
        handle.visible.add("#logout")

        assert click_if_unique("#logout")(handle) is None
        assert handle.calls == [("click", "#logout")]

    @pytest.mark.parametrize("count", [0, 2])
    def test_skips_otherwise(self, handle, count) -> None:
        handle.counts["#logout"] = count

        click_if_unique("#logout")(handle)

        assert handle.calls == []


class TestUnexpectedPage:
    def test_returns_when_url_changes_within_grace(self, handle) -> None:
        handle.url = "https://h/transient"
        original_sleep = handle.sleep_ms

        def navigate_on_sleep(ms: int) -> None:
            original_sleep(ms)
            handle.url = "https://h/next"

        handle.sleep_ms = navigate_on_sleep

        unexpected_page(grace_seconds=1.0)(handle)

    def test_fails_with_screenshot_when_url_stays(self, handle) -> None:
        handle.url = "https://h/password-expired"

        with pytest.raises(UnexpectedPage) as err:
            unexpected_page(grace_seconds=0.05, screenshot="/tmp/unexpected.png")(handle)

        assert "https://h/password-expired" in str(err.value)
        assert ("screenshot", "/tmp/unexpected.png") in handle.calls
