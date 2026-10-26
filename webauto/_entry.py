"""Top-level `init(...)` — the script's terminal call.

Binds `notify` to the configured endpoint (or installs a "no-channel" stub
for `done()`-only scripts), launches the browser, runs the action, exits.
"""

from __future__ import annotations

import os
import sys
from typing import Any, NoReturn, Optional

from webauto._handles import make_handle
from webauto._notify import _notify_ctx
from webauto._notification_client import _post_notification
from webauto._runner import run_actions
from webauto._types import BaseAction


def _resolve_notification(explicit: Optional[dict]) -> Optional[dict]:
    """Return a notification dict from explicit arg, env, or None.

    Precedence: explicit kwarg → AUTOMATION_NOTIFICATION_TYPE+ENDPOINT env vars
    → None (no channel; script must call `done()`).
    """
    if explicit is not None:
        return explicit
    ntype = os.environ.get("AUTOMATION_NOTIFICATION_TYPE")
    endpoint = os.environ.get("AUTOMATION_NOTIFICATION_ENDPOINT")
    if ntype and endpoint:
        return {"type": ntype, "endpoint": endpoint}
    return None


def _no_channel_emitter(_data: bytes, _content_type: Optional[str] = None) -> NoReturn:
    """Bound when init() runs without a notification endpoint.

    Tells the script clearly that notify() needs a channel; suggests done()
    as the alternative.
    """
    raise RuntimeError(
        "notify() requires a notification endpoint, but none was configured. "
        "Either launch via `webauto-cli` (listener or remote mode) so the "
        "AUTOMATION_NOTIFICATION_* env vars get set, or call `done()` instead "
        "to end the run without sending a payload."
    )


def init(
    *,
    url: str,
    action: BaseAction,
    notification: Optional[dict] = None,
    timeout_seconds: Optional[float] = None,
    handle: Any = None,
    browser: Optional[str] = None,
) -> None:
    """Drive `action` against `url`. Ends when a step calls notify() or done().

    Args:
      url:            URL to navigate to first.
      action:         BaseAction subclass instance.
      notification:   Optional dict `{"type": "http", "endpoint": "<url>"}`.
                      If None, reads `AUTOMATION_NOTIFICATION_TYPE` +
                      `AUTOMATION_NOTIFICATION_ENDPOINT` from env. If those
                      are absent too, no channel — script must call `done()`.
      timeout_seconds: optional override for action.timeout_seconds.
      handle:         optional BrowserHandle (for testing). If None, one is
                      built for `browser`.
      browser:        "playwright" (default) or "seleniumbase". Falls back to
                      the WEBAUTO_BROWSER env var, then "playwright".

    If a step raises, `action.on_failure` steps run (best effort) before the
    browser closes and the original error propagates.
    """
    resolved = _resolve_notification(notification)

    if resolved is not None:
        def _emit(data: bytes, content_type: Optional[str] = None) -> None:
            _post_notification(resolved, data, content_type=content_type)
        emitter = _emit
    else:
        emitter = _no_channel_emitter

    if handle is None:
        handle = make_handle(browser)

    token = _notify_ctx.set(emitter)
    try:
        handle.goto(url)
        run_actions(handle, action, timeout_seconds=timeout_seconds)
    except Exception:
        _run_cleanup(handle, action.on_failure)
        raise
    finally:
        _notify_ctx.reset(token)
        handle.close()


def _run_cleanup(handle: Any, steps: list) -> None:
    """Run on_failure steps; report and swallow their errors so the original one wins."""
    for step in steps:
        try:
            step(handle)
        except Exception as exc:  # noqa: BLE001 — cleanup must never mask the real failure
            name = getattr(step, "__name__", repr(step))
            print(f"[webauto] on_failure step {name} failed: {exc!r}", file=sys.stderr)
