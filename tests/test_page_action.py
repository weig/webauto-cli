"""Unit tests for PageAction shorthand kwarg desugaring + validation."""

from __future__ import annotations

import pytest

from webauto._types import PageAction


class TestPageActionShorthand:
    def test_url_contains_shorthand(self) -> None:
        pa = PageAction(url_contains="/login", steps=[])
        assert pa.url("https://h/auth/login")
        assert not pa.url("https://h/auth/mfa")

    def test_url_equals_shorthand(self) -> None:
        pa = PageAction(url_equals="https://h/x", steps=[])
        assert pa.url("https://h/x")
        assert not pa.url("https://h/x?q=1")

    def test_url_startswith_shorthand(self) -> None:
        pa = PageAction(url_startswith="https://", steps=[])
        assert pa.url("https://h/x")
        assert not pa.url("http://h/x")

    def test_url_regex_shorthand(self) -> None:
        pa = PageAction(url_regex=r"/quote/[A-Z]+", steps=[])
        assert pa.url("/v1/quote/AAPL")
        assert not pa.url("/v1/quote/aapl")

    def test_url_any_shorthand(self) -> None:
        pa = PageAction(url_any=True, steps=[])
        assert pa.url("anything")
        assert pa.url("")

    def test_explicit_url_predicate(self) -> None:
        pa = PageAction(url=lambda u: u.endswith("/done"), steps=[])
        assert pa.url("/path/done")
        assert not pa.url("/path/done/x")


class TestPageActionValidation:
    def test_no_matcher_raises(self) -> None:
        with pytest.raises(TypeError, match="requires a url matcher"):
            PageAction(steps=[])

    def test_multiple_matchers_raises(self) -> None:
        with pytest.raises(TypeError, match="exactly one URL matcher"):
            PageAction(url_contains="/a", url_equals="/a", steps=[])

    def test_order_default(self) -> None:
        pa = PageAction(url_any=True, steps=[])
        assert pa.order == 100

    def test_order_override(self) -> None:
        pa = PageAction(url_any=True, steps=[], order=200)
        assert pa.order == 200


class TestPageActionLazyAnchor:
    def test_contains_with_lambda(self) -> None:
        anchor = "/initial"
        pa = PageAction(url_contains=lambda: anchor, steps=[])
        assert pa.url("https://h/initial/page")

        anchor = "/changed"
        assert not pa.url("https://h/initial/page")
        assert pa.url("https://h/changed/page")
