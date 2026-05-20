"""The `notify(...)` completion signal.

`init()` (in `_entry.py`) binds a per-call emitter to `_notify_ctx` before
running the action. Steps call `notify(data)` which:
  1. Serializes (JSON by default; override via `serializer=`).
  2. Hands the resulting bytes to the bound emitter.
  3. Raises `_Done` so the runner unwinds.

Calling `notify` outside an `init()` context raises RuntimeError.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any, Callable, NoReturn, Optional

from webauto._types import _Done


Emitter = Callable[[bytes, Optional[str]], None]
"""(data: bytes, content_type: str | None) -> None."""


# Public for testing only — production code should not touch this directly.
_notify_ctx: contextvars.ContextVar[Emitter] = contextvars.ContextVar(
    "webauto_notify_emitter"
)


def _default_serializer(obj: Any) -> bytes:
    """JSON-encode with utf-8. `default=str` handles datetime/Path/Decimal etc."""
    return json.dumps(obj, default=str).encode("utf-8")


def done() -> NoReturn:
    """End the run cleanly without sending a notification.

    Use this when something else (a server-side relay, a downstream poller,
    a database write) has already captured the result by the time the
    script reaches its final step. The framework just needs to know the
    action is complete so it can close the browser and exit 0.

    Equivalent to `notify(...)` minus the POST. Pairs with the CLI's
    `--no-notify` mode, which spawns the subprocess without a listener.
    """
    raise _Done()


def notify(
    data: Any,
    *,
    serializer: Optional[Callable[[Any], bytes]] = None,
    content_type: Optional[str] = None,
) -> NoReturn:
    """Send `data` to the configured endpoint, then end the run.

    Default behavior: `data` is JSON-encoded and sent with
    Content-Type: application/json.

    Override `serializer` to keep full control of the bytes that hit the wire:

        notify({"price": "187.42"})                         # default JSON
        notify(b"<raw PDF>",
               serializer=lambda x: x,                      # passthrough
               content_type="application/pdf")
        notify(my_obj, serializer=msgpack.packb,
               content_type="application/msgpack")

    Raises:
        RuntimeError: if called outside an init() context, or if the
                      script was launched without a notification endpoint
                      (use `done()` instead to end cleanly with no POST).
        TypeError:    if a custom serializer doesn't return bytes.
    """
    try:
        emitter = _notify_ctx.get()
    except LookupError as exc:
        raise RuntimeError(
            "notify() called outside an init() context "
            "(or the script was launched with --no-notify; use done() instead)"
        ) from exc

    if serializer is None:
        body = _default_serializer(data)
        ct = content_type or "application/json"
    else:
        body = serializer(data)
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError(
                f"serializer must return bytes, got {type(body).__name__}"
            )
        body = bytes(body)
        ct = content_type  # may be None → transport picks octet-stream

    emitter(body, ct)
    raise _Done()
