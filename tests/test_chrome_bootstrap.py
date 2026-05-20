"""Unit tests for chrome version detection + CHROME_VERSION override."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from webauto._chrome_bootstrap import (
    ChromeNotFoundError,
    _parse_major,
    resolve_chrome_version,
)


class TestParseMajor:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Google Chrome 124.0.6367.207", 124),
            ("Chromium 123.0.6312.86", 123),
            ("Google Chrome 130.0.1.2", 130),
            ("124", 124),
            ("124.0.6367.207", 124),
            ("", None),
            ("not a version", None),
        ],
    )
    def test_extracts_major_version(self, raw: str, expected) -> None:
        assert _parse_major(raw) == expected


class TestResolveChromeVersion:
    def test_env_override_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("CHROME_VERSION", "120")
        # Should not even consult detect_chrome_version.
        with patch(
            "webauto._chrome_bootstrap.detect_chrome_version",
            side_effect=AssertionError("should not be called"),
        ):
            assert resolve_chrome_version() == 120

    def test_env_with_full_version_string(self, monkeypatch) -> None:
        monkeypatch.setenv("CHROME_VERSION", "124.0.6367.207")
        assert resolve_chrome_version() == 124

    def test_env_empty_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("CHROME_VERSION", "")
        with pytest.raises(ValueError, match="empty"):
            resolve_chrome_version()

    def test_env_unparseable_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("CHROME_VERSION", "not-a-version")
        with pytest.raises(ValueError, match="not parseable"):
            resolve_chrome_version()

    def test_detection_used_when_env_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("CHROME_VERSION", raising=False)
        with patch(
            "webauto._chrome_bootstrap.detect_chrome_version",
            return_value=123,
        ):
            assert resolve_chrome_version() == 123

    def test_no_env_no_chrome_raises(self, monkeypatch) -> None:
        monkeypatch.delenv("CHROME_VERSION", raising=False)
        with patch(
            "webauto._chrome_bootstrap.detect_chrome_version",
            return_value=None,
        ):
            with pytest.raises(ChromeNotFoundError):
                resolve_chrome_version()
