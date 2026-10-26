"""Browser backend selection and on_failure cleanup in init()."""

from __future__ import annotations

import pytest

import webauto._entry as entry
from webauto import BaseAction, PageAction, custom
from webauto._handles import make_handle


class _Marker:
    def __init__(self, kind: str) -> None:
        self.kind = kind


@pytest.fixture
def fake_backends(monkeypatch):
    monkeypatch.setattr("webauto._handles._playwright", lambda: _Marker("playwright"))
    monkeypatch.setattr("webauto._handles._seleniumbase", lambda: _Marker("seleniumbase"))


class TestMakeHandle:
    def test_default_is_playwright(self, fake_backends, monkeypatch) -> None:
        monkeypatch.delenv("WEBAUTO_BROWSER", raising=False)

        assert make_handle().kind == "playwright"

    def test_env_selects_seleniumbase(self, fake_backends, monkeypatch) -> None:
        monkeypatch.setenv("WEBAUTO_BROWSER", "seleniumbase")

        assert make_handle().kind == "seleniumbase"

    def test_explicit_argument_beats_env(self, fake_backends, monkeypatch) -> None:
        monkeypatch.setenv("WEBAUTO_BROWSER", "seleniumbase")

        assert make_handle("playwright").kind == "playwright"

    def test_unknown_backend_is_an_error(self, fake_backends) -> None:
        with pytest.raises(ValueError, match="netscape"):
            make_handle("netscape")


class TestOnFailure:
    def test_cleanup_steps_run_then_error_propagates(self, handle) -> None:
        def boom(_h) -> None:
            raise RuntimeError("step failed")

        class Failing(BaseAction):
            def __init__(self) -> None:
                super().__init__()
                self.actions = [PageAction(url_any=True, steps=[custom(boom)])]
                self.on_failure = [custom(lambda h: h.click("#logout"))]

        with pytest.raises(RuntimeError, match="step failed"):
            entry.init(url="https://h/", action=Failing(), handle=handle)

        assert handle.calls[-2:] == [("click", "#logout"), ("close",)]

    def test_cleanup_error_does_not_mask_original(self, handle) -> None:
        def boom(_h) -> None:
            raise RuntimeError("original")

        def cleanup_boom(_h) -> None:
            raise OSError("cleanup broke")

        class Failing(BaseAction):
            def __init__(self) -> None:
                super().__init__()
                self.actions = [PageAction(url_any=True, steps=[custom(boom)])]
                self.on_failure = [custom(cleanup_boom)]

        with pytest.raises(RuntimeError, match="original"):
            entry.init(url="https://h/", action=Failing(), handle=handle)
