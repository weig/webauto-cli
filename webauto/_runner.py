"""Action runner — URL-routed PageAction dispatch.

The runner does NOT walk PageActions forward like a sequence. It treats
`action.actions` as a routing table:

  1. Read `handle.url`.
  2. Find every PageAction whose predicate returns True AND that is not
     exhausted-for-this-URL.
  3. Sort by `(order, declaration_index)` ascending; pick the first.
  4. Run that PageAction's steps to completion.
  5. Mark it exhausted. If `handle.url` changed during the steps, clear
     the exhausted set (new URL = new dispatch context).
  6. Repeat until a step raises `_Done` (caught here) or the timeout fires.

Set DEBUG=1 in the env to log dispatch events to stderr.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

from webauto._types import BaseAction, BrowserHandle, PageAction, _Done
from webauto._url import _current_handle_ctx


def _debug_enabled() -> bool:
    v = os.environ.get("DEBUG", "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _dlog(msg: str) -> None:
    if _debug_enabled():
        print(f"[ba.runner] {msg}", file=sys.stderr, flush=True)


def run_actions(
    handle: BrowserHandle,
    action: BaseAction,
    *,
    timeout_seconds: Optional[float] = None,
) -> None:
    """Drive `action.actions` against `handle` until a step calls notify().

    When `timeout_seconds` is None, falls back to `action.timeout_seconds`.

    Raises:
        TimeoutError: budget exhausted without notify() being called.
        Anything a step raises (besides `_Done`, which is caught internally).
    """
    if timeout_seconds is None:
        timeout_seconds = action.timeout_seconds

    deadline = time.monotonic() + timeout_seconds
    actions: list[PageAction] = list(action.actions)
    if not actions:
        # No PageActions declared — nothing to do but wait for timeout. This
        # is almost certainly a bug in the user's script, so fail fast.
        raise RuntimeError(
            f"{type(action).__name__} has no PageActions; nothing to run"
        )

    # Decorate with declaration index for the tiebreak sort.
    indexed: list[tuple[int, PageAction]] = list(enumerate(actions))

    exhausted: set[int] = set()
    last_url = handle.url
    last_logged_url = None  # for periodic url-change logging

    _dlog(f"start: {len(actions)} PageActions, timeout={timeout_seconds}s, url={last_url!r}")

    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError(
                    f"run_actions: timeout after {timeout_seconds}s "
                    f"(no notify() was called; last URL: {handle.url!r})"
                )

            current_url = handle.url
            if current_url != last_url:
                _dlog(f"url change: {last_url!r} -> {current_url!r}")
                # New page — every PageAction is re-eligible.
                exhausted.clear()
                last_url = current_url

            # Find matching, non-exhausted PageActions.
            candidates: list[tuple[int, PageAction]] = [
                (idx, pa) for idx, pa in indexed
                if idx not in exhausted and pa.url(current_url)
            ]

            if not candidates:
                # Periodic log of "still waiting on this URL" so a hang is
                # observable.
                if current_url != last_logged_url:
                    _dlog(f"no PageAction matches; waiting (url={current_url!r})")
                    last_logged_url = current_url
                # Nothing matches the current URL. Poll briefly and re-check —
                # the page might navigate due to in-flight JS, a redirect,
                # or external input (e.g. MFA on phone).
                handle.sleep_ms(100)
                continue

            # Sort by (order, declaration_index); pick the first.
            candidates.sort(key=lambda t: (t[1].order, t[0]))
            chosen_idx, chosen = candidates[0]

            _dlog(
                f"dispatch PA[{chosen_idx}] (order={chosen.order}, "
                f"steps={len(chosen.steps)}) url={current_url!r}"
            )

            url_before_steps = handle.url
            for step_idx, step in enumerate(chosen.steps):
                _dlog(
                    f"  step[{step_idx}] {getattr(step, '__name__', repr(step))} "
                    f"url={handle.url!r}"
                )
                # Bind the handle for helpers like parse_url() that take no
                # arguments. Reset on each step so callers using ContextVar.run
                # don't leak state.
                ctx_token = _current_handle_ctx.set(handle)
                try:
                    step(handle)
                finally:
                    _current_handle_ctx.reset(ctx_token)

            exhausted.add(chosen_idx)
            _dlog(f"PA[{chosen_idx}] complete; exhausted={sorted(exhausted)}")

            if handle.url != url_before_steps:
                # URL changed mid-page — fresh dispatch context.
                exhausted.clear()
                last_url = handle.url
    except _Done:
        _dlog("_Done raised — exiting normally")
        return
