"""Unit tests for notify() function."""

from __future__ import annotations

import datetime
import json
from typing import Optional

import pytest

from webauto._notify import _notify_ctx, done, notify
from webauto._types import _Done


class StubEmitter:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, Optional[str]]] = []

    def __call__(self, body: bytes, ct: Optional[str]) -> None:
        self.calls.append((body, ct))


@pytest.fixture
def emitter():
    e = StubEmitter()
    token = _notify_ctx.set(e)
    try:
        yield e
    finally:
        _notify_ctx.reset(token)


class TestNotifyOutsideContext:
    def test_unbound_raises(self) -> None:
        # No fixture — no emitter bound.
        with pytest.raises(RuntimeError, match="outside an init"):
            notify({"x": 1})


class TestNotifyDefaultSerializer:
    def test_dict_to_json_with_content_type(self, emitter) -> None:
        with pytest.raises(_Done):
            notify({"a": 1, "b": "two"})
        assert len(emitter.calls) == 1
        body, ct = emitter.calls[0]
        assert json.loads(body) == {"a": 1, "b": "two"}
        assert ct == "application/json"

    def test_default_handles_datetime(self, emitter) -> None:
        ts = datetime.datetime(2026, 5, 14, 18, 0, 0)
        with pytest.raises(_Done):
            notify({"ts": ts})
        body, _ = emitter.calls[0]
        # datetime serialized via default=str
        assert b"2026-05-14" in body

    def test_explicit_content_type_wins(self, emitter) -> None:
        with pytest.raises(_Done):
            notify({"x": 1}, content_type="application/vnd.example+json")
        _, ct = emitter.calls[0]
        assert ct == "application/vnd.example+json"


class TestNotifyCustomSerializer:
    def test_passthrough_bytes(self, emitter) -> None:
        with pytest.raises(_Done):
            notify(b"raw", serializer=lambda x: x)
        body, ct = emitter.calls[0]
        assert body == b"raw"
        assert ct is None  # transport picks default

    def test_string_to_utf8(self, emitter) -> None:
        with pytest.raises(_Done):
            notify("hello", serializer=lambda x: x.encode())
        body, _ = emitter.calls[0]
        assert body == b"hello"

    def test_serializer_returning_non_bytes_raises(self, emitter) -> None:
        with pytest.raises(TypeError, match="must return bytes"):
            notify({}, serializer=lambda x: "not bytes")
        assert emitter.calls == []


class TestDone:
    def test_raises_done_without_emitter_call(self, emitter) -> None:
        with pytest.raises(_Done):
            done()
        # Did NOT touch the emitter — no POST happened.
        assert emitter.calls == []

    def test_works_outside_init_context(self) -> None:
        # done() doesn't need a bound emitter — just raises the sentinel.
        with pytest.raises(_Done):
            done()


class TestNoChannelEmitter:
    """When init() launches with no notification endpoint, calling notify()
    should fail with a clear message pointing at done()."""

    def test_no_channel_notify_errors(self) -> None:
        from webauto._entry import _no_channel_emitter
        token = _notify_ctx.set(_no_channel_emitter)
        try:
            with pytest.raises(RuntimeError, match="done"):
                notify({"x": 1})
        finally:
            _notify_ctx.reset(token)

    def test_no_channel_done_still_works(self) -> None:
        from webauto._entry import _no_channel_emitter
        token = _notify_ctx.set(_no_channel_emitter)
        try:
            with pytest.raises(_Done):
                done()
        finally:
            _notify_ctx.reset(token)
