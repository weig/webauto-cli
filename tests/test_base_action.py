"""Unit tests for BaseAction's Params auto-build + get_parameter()."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

import pytest

from webauto._types import BaseAction


@dataclass
class SampleParams:
    URL: str
    USERNAME: str
    PASSWORD: str
    MODE: str = "client"


class SampleAction(BaseAction):
    Params: ClassVar[type] = SampleParams


class TestBaseActionInit:
    def test_auto_builds_params(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("USERNAME", "alice")
        monkeypatch.setenv("PASSWORD", "p1")
        a = SampleAction()
        assert a.params.URL == "https://x"
        assert a.params.USERNAME == "alice"
        assert a.params.MODE == "client"

    def test_explicit_params_bypass_env(self, monkeypatch) -> None:
        monkeypatch.delenv("URL", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        monkeypatch.delenv("PASSWORD", raising=False)
        a = SampleAction(params=SampleParams(URL="x", USERNAME="u", PASSWORD="p"))
        assert a.params.URL == "x"

    def test_no_params_class_works(self) -> None:
        # An action without Params doesn't try to build anything.
        class Noddy(BaseAction):
            pass

        a = Noddy()
        assert a.params is None


class TestGetParameter:
    def test_returns_lambda_resolved_late(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("USERNAME", "alice")
        monkeypatch.setenv("PASSWORD", "secret")
        a = SampleAction()

        p = a.get_parameter("PASSWORD")
        # Lambda not yet called.
        a.params.PASSWORD = "rotated"  # mutate to verify late binding
        assert p() == "rotated"

    def test_invalid_name_raises_at_definition_time(self, monkeypatch) -> None:
        monkeypatch.setenv("URL", "https://x")
        monkeypatch.setenv("USERNAME", "alice")
        monkeypatch.setenv("PASSWORD", "p")
        a = SampleAction()
        with pytest.raises(AttributeError, match="no field 'NOPE'"):
            a.get_parameter("NOPE")

    def test_no_params_raises(self) -> None:
        class Noddy(BaseAction):
            pass

        a = Noddy()
        with pytest.raises(AttributeError, match="no Params declared"):
            a.get_parameter("anything")
