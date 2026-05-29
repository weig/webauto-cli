"""Schwab OAuth — local callback-server flow.

Variant of `schwab_auth.py` for the case where the configured `redirect_uri`
is a loopback HTTPS callback that schwab_cli itself serves, e.g.
`https://127.0.0.1:19806/schwab/callback`. schwab_cli stands up a tiny local
HTTPS server (with a locally-trusted cert installed via `schwab cert install`)
and the browser is redirected straight back to it with `?code=...&state=...`.

This script's job is to **drive the browser through Schwab login**. When the
trusted cert is installed, the browser's own navigation to the loopback
callback delivers the code to schwab_cli's server — so the final step would
just need `done()`. As a belt-and-suspenders fallback (the "CDP watch"
method), the final step ALSO re-issues a GET to the captured callback URL
over loopback, so the code is delivered even if the in-browser navigation
didn't reach the server (e.g. a fresh profile that doesn't trust the cert).
The relayed GET disables TLS verification because it targets `127.0.0.1`
only and the code is already in hand.

Run via:
    webauto-cli examples/schwab_auth_local.py --no-notify --state <state> \\
        -a URL=<authorize-url>

Selectors and page sequence are identical to schwab_auth.py — only the final
PageAction differs (it relays + `done()` instead of `notify()`).
"""

from __future__ import annotations

import ssl
import urllib.request
from dataclasses import dataclass
from typing import ClassVar

from webauto import (
    BaseAction,
    PageAction,
    check_all,
    click,
    custom,
    done,
    fill,
    init,
    sleep,
    wait_for_hydration,
    wait_page_change,
    wait_url_contains,
    wait_visible,
)


@dataclass
class SchwabParams:
    URL: str            # OAuth authorize URL (redirect_uri embedded by caller)
    USERNAME: str       # Schwab login id
    PASSWORD: str       # Schwab login password


# Selectors (mirror schwab_cli/browser/selectors.py @ 2026-04-21)
LOGIN_USERNAME       = "input#loginIdInput"
LOGIN_PASSWORD       = "input#passwordInput"
LOGIN_SUBMIT         = "button#btnLogin"
SCHWAB_APP_OPTION    = "#mobile_approve"
TRUST_YES            = "input#remember-device-yes"
TRUST_CONTINUE       = "button#btnContinue"
CONSENT_CHECKBOX     = "input#acceptTerms"
CONSENT_CONTINUE     = "button#submit-btn"
CONSENT_MODAL_ACCEPT = "button#agree-modal-btn-"
ACCOUNT_CHECKBOX     = 'input[type="checkbox"]'
ACCOUNT_CONTINUE     = "button#submit-btn"
DONE                 = "button#cancel-btn"

URL_LOGIN            = "/login-one-step"
URL_MFA_PICKER       = "/authenticators"
URL_MFA_WAITING      = "/mobile_approve"
URL_TRUST_DEVICE     = "/devicetag/remember"
URL_CONSENT          = "/third-party-auth/cag"
URL_ACCOUNTS         = "/third-party-auth/account"
URL_CONFIRMATION     = "/third-party-auth/confirmation"

SCHWAB_DOMAINS = ("schwab.com", "schwabapi.com")


def _is_off_schwab(url: str) -> bool:
    """True once the browser has been redirected off Schwab (to the callback)."""
    return all(d not in url for d in SCHWAB_DOMAINS)


def _relay_then_done(handle) -> None:
    """CDP-watch fallback: re-issue the captured callback URL to the local
    loopback server, then end the run.

    The browser's own navigation to the trusted-cert callback usually delivers
    the code already; this redundant GET guarantees delivery and is harmless
    (the server is one-shot/idempotent). TLS verification is disabled because
    the target is `127.0.0.1` and the authorization code is already captured.
    """
    url = handle.url
    if url and url.startswith("https://127.0.0.1") and "code=" in url:
        ctx = ssl._create_unverified_context()
        try:
            urllib.request.urlopen(url, context=ctx, timeout=5)  # noqa: S310 — loopback only
        except Exception:
            # The in-browser navigation may have already delivered the code,
            # or the server already closed — either way we're done.
            pass
    done()


class SchwabAuthLocal(BaseAction):
    """Drives Schwab login → reaches the loopback callback → relay + exit."""

    Params: ClassVar[type] = SchwabParams
    timeout_seconds = 600   # MFA on the phone can eat a minute or two

    def __init__(self, params=None):
        super().__init__(params)
        p = self.get_parameter

        self.actions = [
            PageAction(url_contains=URL_LOGIN, steps=[
                wait_visible(LOGIN_USERNAME),
                fill(LOGIN_USERNAME, p("USERNAME")),
                fill(LOGIN_PASSWORD, p("PASSWORD")),
                click(LOGIN_SUBMIT),
                wait_page_change(timeout=60),
            ]),
            PageAction(url_contains=URL_MFA_PICKER, steps=[
                wait_visible(SCHWAB_APP_OPTION),
                click(SCHWAB_APP_OPTION),
                wait_url_contains(URL_MFA_WAITING, timeout=30),
            ]),
            PageAction(url_contains=URL_MFA_WAITING, steps=[
                wait_page_change(timeout=120),
            ]),
            PageAction(url_contains=URL_TRUST_DEVICE, steps=[
                wait_visible(TRUST_YES),
                click(TRUST_YES),
                click(TRUST_CONTINUE),
                wait_url_contains(URL_CONSENT, timeout=30),
            ]),
            PageAction(url_contains=URL_CONSENT, steps=[
                wait_visible(CONSENT_CHECKBOX),
                wait_for_hydration(),
                click(CONSENT_CHECKBOX),
                click(CONSENT_CONTINUE),
                wait_visible(CONSENT_MODAL_ACCEPT, timeout=30),
                wait_for_hydration(),
                click(CONSENT_MODAL_ACCEPT),
                sleep(1000),
                wait_url_contains(URL_ACCOUNTS, timeout=60),
            ]),
            PageAction(url_contains=URL_ACCOUNTS, steps=[
                wait_for_hydration(),
                wait_visible(ACCOUNT_CHECKBOX, timeout=60),
                check_all(ACCOUNT_CHECKBOX),
                click(ACCOUNT_CONTINUE),
                wait_url_contains(URL_CONFIRMATION, timeout=30),
            ]),
            PageAction(url_contains=URL_CONFIRMATION, steps=[
                wait_for_hydration(),
                wait_visible(DONE, timeout=30),
                click(DONE),
            ]),
            # Off Schwab — the browser landed on the loopback callback.
            # Relay the captured URL to the local server (fallback), then exit.
            PageAction(url=_is_off_schwab, steps=[
                custom(_relay_then_done),
            ]),
        ]


if __name__ == "__main__":
    action = SchwabAuthLocal()
    init(url=action.params.URL, action=action)
