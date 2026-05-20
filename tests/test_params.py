"""Unit tests for Params building from os.environ."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from webauto._params import build_params_from_env


# ----------------------------------------------------------------------------
# build_params_from_env
# ----------------------------------------------------------------------------


@dataclass
class Simple:
    URL: str
    USERNAME: str
    PASSWORD: str


@dataclass
class WithDefaults:
    URL: str
    MODE: str = "client"
    APP: Optional[str] = None


@dataclass
class TypedFields:
    URL: str
    PORT: int
    DEBUG: bool
    RATE: float


class TestBuildParamsFromEnv:
    def test_all_required_present(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x/y")
        monkeypatch.setenv("USERNAME", "alice")
        monkeypatch.setenv("PASSWORD", "hunter2")
        p = build_params_from_env(Simple)
        assert p.URL == "https://x/y"
        assert p.USERNAME == "alice"
        assert p.PASSWORD == "hunter2"

    def test_required_missing_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        with pytest.raises(KeyError, match="USERNAME"):
            build_params_from_env(Simple)

    def test_defaults_used_when_missing(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.delenv("MODE", raising=False)
        monkeypatch.delenv("APP", raising=False)
        p = build_params_from_env(WithDefaults)
        assert p.URL == "https://x"
        assert p.MODE == "client"
        assert p.APP is None

    def test_defaults_overridden(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("MODE", "server")
        monkeypatch.setenv("APP", "Authenticator")
        p = build_params_from_env(WithDefaults)
        assert p.MODE == "server"
        assert p.APP == "Authenticator"

    def test_typed_int_bool_float(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("PORT", "8080")
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("RATE", "1.5")
        p = build_params_from_env(TypedFields)
        assert p.PORT == 8080
        assert p.DEBUG is True
        assert p.RATE == 1.5

    @pytest.mark.parametrize(
        "raw, expected",
        [("1", True), ("true", True), ("YES", True), ("on", True),
         ("0", False), ("false", False), ("no", False), ("", False)],
    )
    def test_bool_parse_variants(self, monkeypatch, raw: str, expected: bool) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("PORT", "1")
        monkeypatch.setenv("DEBUG", raw)
        monkeypatch.setenv("RATE", "0")
        p = build_params_from_env(TypedFields)
        assert p.DEBUG is expected

    def test_int_parse_failure_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("PORT", "not-an-int")
        monkeypatch.setenv("DEBUG", "1")
        monkeypatch.setenv("RATE", "1.0")
        with pytest.raises(ValueError, match="PORT"):
            build_params_from_env(TypedFields)

    def test_non_dataclass_raises(self) -> None:
        class NotADataclass:
            pass

        with pytest.raises(TypeError, match="is not a dataclass"):
            build_params_from_env(NotADataclass)
