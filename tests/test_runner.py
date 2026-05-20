"""Unit tests for the URL-routed dispatch runner."""

from __future__ import annotations

from typing import ClassVar

import pytest

from webauto._notify import _notify_ctx
from webauto._runner import run_actions
from webauto._types import BaseAction, PageAction, _Done


def _stub_emitter(*_args, **_kwargs):
    """No-op emitter used so notify() inside steps doesn't fail before _Done."""


@pytest.fixture(autouse=True)
def bind_emitter():
    token = _notify_ctx.set(_stub_emitter)
    try:
        yield
    finally:
        _notify_ctx.reset(token)


class _A(BaseAction):
    timeout_seconds: ClassVar[float] = 5.0


def _build_action(actions_list: list[PageAction]) -> BaseAction:
    a = _A()
    a.actions = actions_list
    return a


# ----------------------------------------------------------------------------
# Happy path: a single matching PageAction whose final step calls notify().
# ----------------------------------------------------------------------------


def _notify_step(payload):
    """Build a step that raises _Done — simulates notify()'s effect."""

    def step(_handle):
        # In production, notify() does the POST then raises _Done. For the
        # runner tests we don't need the POST.
        raise _Done()

    return step


class TestSinglePageHappyPath:
    def test_runs_steps_in_order(self, handle) -> None:
        seen: list[str] = []

        def s(name):
            def step(_h):
                seen.append(name)
            return step

        action = _build_action([
            PageAction(url_any=True, steps=[s("a"), s("b"), _notify_step({})]),
        ])
        run_actions(handle, action, timeout_seconds=2.0)
        assert seen == ["a", "b"]


# ----------------------------------------------------------------------------
# Dispatch model: order independence + tiebreak.
# ----------------------------------------------------------------------------


class TestDispatchOrderIndependence:
    def test_non_overlapping_predicates_run_correctly(self, handle) -> None:
        log: list[str] = []

        handle._url = "/login"
        handle.url_after_step = ["/done"]  # the click below flips url to /done

        action = _build_action([
            # Declared in non-flow order — runner should still dispatch correctly.
            PageAction(url_contains="/done", steps=[
                (lambda _h: log.append("on /done")),
                _notify_step({}),
            ]),
            PageAction(url_contains="/login", steps=[
                (lambda _h: log.append("on /login")),
                # Trigger URL change via the mock's url_after_step machinery.
                (lambda h: h.click("#submit")),
            ]),
        ])
        run_actions(handle, action, timeout_seconds=2.0)
        assert log == ["on /login", "on /done"]


class TestCatchallLowerPriority:
    def test_specific_beats_any_url_via_order(self, handle) -> None:
        log: list[str] = []

        action = _build_action([
            PageAction(url_any=True, order=200, steps=[
                (lambda _h: log.append("catchall")),
                _notify_step({}),
            ]),
            PageAction(url_contains="example", order=100, steps=[
                (lambda _h: log.append("specific")),
                _notify_step({}),
            ]),
        ])
        run_actions(handle, action, timeout_seconds=2.0)
        assert log == ["specific"]


class TestExhaustion:
    def test_no_url_change_means_no_refire_on_same_url(self, handle) -> None:
        # An action whose steps don't navigate — runner should not loop on it.
        call_count = [0]

        action = _build_action([
            PageAction(url_any=True, order=100, steps=[
                (lambda _h: call_count.__setitem__(0, call_count[0] + 1)),
            ]),
        ])
        # After the only PageAction is exhausted on this URL, nothing matches →
        # runner spins (sleeping) until timeout.
        with pytest.raises(TimeoutError):
            run_actions(handle, action, timeout_seconds=0.3)
        assert call_count[0] == 1


# ----------------------------------------------------------------------------
# Timeout
# ----------------------------------------------------------------------------


class TestTimeout:
    def test_no_notify_raises_timeout(self, handle) -> None:
        action = _build_action([
            PageAction(url_any=True, steps=[(lambda _h: None)]),
        ])
        with pytest.raises(TimeoutError):
            run_actions(handle, action, timeout_seconds=0.2)

    def test_no_pageactions_raises_runtime_error(self, handle) -> None:
        action = _build_action([])
        with pytest.raises(RuntimeError, match="no PageActions"):
            run_actions(handle, action, timeout_seconds=1.0)

    def test_uses_action_timeout_when_not_passed(self, handle) -> None:
        action = _build_action([
            PageAction(url_any=True, steps=[(lambda _h: None)]),
        ])
        action.timeout_seconds = 0.2
        with pytest.raises(TimeoutError):
            run_actions(handle, action)  # no explicit timeout
