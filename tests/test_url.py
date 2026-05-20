"""Unit tests for parse_url() — generic URL splitter + optional validator."""

from __future__ import annotations

import pytest

from webauto import contains, equals, parse_url, startswith
from webauto._url import _current_handle_ctx


class TestBasicParsing:
    def test_simple_url_with_query(self) -> None:
        assert parse_url("https://h/cb?code=abc&state=s1") == {
            "base_url": "https://h/cb",
            "code": "abc",
            "state": "s1",
        }

    def test_no_query(self) -> None:
        assert parse_url("https://h/cb") == {"base_url": "https://h/cb"}

    def test_repeated_key_becomes_list(self) -> None:
        result = parse_url("https://h/cb?x=1&x=2&y=3")
        assert result == {"base_url": "https://h/cb", "x": ["1", "2"], "y": "3"}

    def test_blank_value_preserved(self) -> None:
        result = parse_url("https://h/cb?flag=&code=x")
        assert result == {"base_url": "https://h/cb", "flag": "", "code": "x"}

    def test_relative_url(self) -> None:
        assert parse_url("/cb?code=x") == {"base_url": "/cb", "code": "x"}

    def test_url_with_port(self) -> None:
        assert parse_url("http://127.0.0.1:8080/x?y=1") == {
            "base_url": "http://127.0.0.1:8080/x",
            "y": "1",
        }


class TestIncludeBaseUrl:
    def test_default_includes(self) -> None:
        assert "base_url" in parse_url("https://h/cb?x=1")

    def test_can_exclude(self) -> None:
        result = parse_url("https://h/cb?code=x&state=y", include_base_url=False)
        assert result == {"code": "x", "state": "y"}
        assert "base_url" not in result


class TestStripUrl:
    def test_strip_prefix(self) -> None:
        result = parse_url(
            "https://relay/abc123/callback?code=x",
            strip_url="https://relay/abc123",
        )
        assert result == {"base_url": "/callback", "code": "x"}

    def test_strip_full_match_leaves_empty_base(self) -> None:
        result = parse_url(
            "https://relay/abc123?code=x",
            strip_url="https://relay/abc123",
        )
        # After strip, URL is "?code=x" → base_url is empty.
        assert result.get("code") == "x"

    def test_strip_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="not a prefix"):
            parse_url(
                "https://other/path?code=x",
                strip_url="https://relay/abc123",
            )


class TestExpectedUrl:
    def test_passes_when_matcher_returns_true(self) -> None:
        parse_url(
            "https://relay/cb?code=x",
            expected_url=startswith("https://relay/"),
        )  # no raise

    def test_raises_on_mismatch(self) -> None:
        with pytest.raises(ValueError, match="expected_url predicate"):
            parse_url(
                "https://other/cb?code=x",
                expected_url=startswith("https://relay/"),
            )

    def test_with_contains_matcher(self) -> None:
        parse_url(
            "https://relay/cb?code=x",
            expected_url=contains("/cb"),
        )

    def test_strip_runs_before_expected_url(self) -> None:
        # After strip, the URL is /callback?code=x — expected_url sees that.
        parse_url(
            "https://relay/abc/callback?code=x",
            strip_url="https://relay/abc",
            expected_url=equals("/callback?code=x"),
        )


class TestExpectedQueryString:
    def test_subset_match_passes(self) -> None:
        parse_url(
            "https://h/cb?code=x&state=s1&extra=ok",
            expected_query_string={"state": "s1"},
        )  # extra= is allowed

    def test_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="state="):
            parse_url(
                "https://h/cb?state=actual",
                expected_query_string={"state": "expected"},
            )

    def test_missing_key_raises(self) -> None:
        with pytest.raises(ValueError, match="state=None"):
            parse_url(
                "https://h/cb?code=x",
                expected_query_string={"state": "anything"},
            )

    def test_combined_with_expected_url(self) -> None:
        parse_url(
            "https://relay/cb?code=x&state=s1",
            expected_url=startswith("https://relay/"),
            expected_query_string={"state": "s1"},
        )


class TestImplicitHandleFromContext:
    """parse_url() with no url= reads from the runner's ContextVar."""

    def test_reads_from_bound_handle(self, handle) -> None:
        handle._url = "https://h/cb?code=abc"
        token = _current_handle_ctx.set(handle)
        try:
            result = parse_url()
        finally:
            _current_handle_ctx.reset(token)
        assert result == {"base_url": "https://h/cb", "code": "abc"}

    def test_no_url_no_handle_raises(self) -> None:
        with pytest.raises(RuntimeError, match="no handle is bound"):
            parse_url()
