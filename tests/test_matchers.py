"""Unit tests for URL matcher factories."""

from __future__ import annotations

import pytest

from webauto._matchers import (
    any_url,
    contains,
    equals,
    regex,
    startswith,
)


class TestAnyUrl:
    def test_matches_every_url(self) -> None:
        m = any_url()
        assert m("https://example.com/")
        assert m("")
        assert m("not even a url")


class TestContains:
    def test_substring_present(self) -> None:
        m = contains("/login")
        assert m("https://schwab.com/auth/login?next=/")

    def test_substring_absent(self) -> None:
        m = contains("/login")
        assert not m("https://schwab.com/auth/mfa")

    def test_lazy_anchor(self) -> None:
        anchor = "/dynamic"
        m = contains(lambda: anchor)
        assert m("https://host/dynamic/page")

        anchor = "/changed"
        assert not m("https://host/dynamic/page")
        assert m("https://host/changed/page")

    def test_non_string_anchor_callable_raises(self) -> None:
        m = contains(lambda: 42)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            m("anything")


class TestStartswith:
    def test_prefix_match(self) -> None:
        m = startswith("https://relay/")
        assert m("https://relay/callback?code=x")
        assert not m("http://relay/callback?code=x")


class TestEquals:
    def test_exact_match(self) -> None:
        m = equals("https://h/y")
        assert m("https://h/y")
        assert not m("https://h/y?q=1")


class TestRegex:
    def test_search_semantics(self) -> None:
        m = regex(r"/quote/[A-Z]+")
        assert m("https://h/quote/AAPL")
        assert m("https://h/v1/quote/MSFT?x=1")
        assert not m("https://h/quote/aapl")
