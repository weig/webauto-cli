"""Schwab OAuth — code_relay flow (relay captures the code server-side).

Variant of `schwab_auth.py` for the case where the configured `redirect_uri`
points at a relay endpoint whose backend captures and stores the OAuth
`code` parameter on its own. The relay then exposes a separate storage URL
that the calling application long-polls to retrieve the code.

This script's only job in that flow is to **drive the browser to the relay
URL** — at the moment of arrival the relay has already captured the code.
We then emit a minimal "I'm done" notification (so the framework unwinds
cleanly and closes the browser) without forwarding the code itself.

Pick this file vs `schwab_auth.py`:

  * `schwab_auth.py`             — code_flow / client mode.
                                    Parses `?code=...&state=...` from the
                                    URL and forwards via `notify()`. The
                                    caller reads the code from the
                                    notification channel.
  * `schwab_auth_code_relay.py`  — code_relay mode (this file).
                                    Drives the browser, lets the relay
                                    capture, and exits. Caller polls the
                                    relay's storage API separately.

Run via:
    webauto-cli examples/schwab_auth_code_relay.py \\
        --env ~/.config/schwab_cli/auto_login.env

Where the env file contains:
    URL=https://api.schwabapi.com/v1/oauth/authorize?client_id=...&redirect_uri=https://relay.../callback&...
    USERNAME=alice
    PASSWORD=hunter2

The script does NOT need the `redirect_uri` separately — it's the caller's
job to embed it in the authorize URL and to fetch the captured code from
wherever the relay stores it. The script just drives the browser until the
URL leaves Schwab's domains, then exits.

Selectors and page sequence are identical to schwab_auth.py — see that
file's docstring for the per-page walkthrough. The only difference is the
final PageAction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from webauto import (
    init,
    done,
    BaseAction,
    PageAction,
    check_all,
    click,
    custom,
    fill,
    sleep,
    wait_for_hydration,
    wait_page_change,
    wait_url_contains,
    wait_visible,
)


# ---------------------------------------------------------------------------
# Inputs (caller passes via --env / -a)
# ---------------------------------------------------------------------------


@dataclass
class SchwabParams:
    URL: str            # OAuth authorize URL (the caller embeds whatever
                        # redirect_uri it wants as a query parameter)
    USERNAME: str       # Schwab login id
    PASSWORD: str       # Schwab login password


# ---------------------------------------------------------------------------
# Selectors (mirror schwab_cli/browser/selectors.py @ 2026-04-21)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# URL fragments
# ---------------------------------------------------------------------------

URL_LOGIN            = "/login-one-step"
URL_MFA_PICKER       = "/authenticators"
URL_MFA_WAITING      = "/mobile_approve"
URL_TRUST_DEVICE     = "/devicetag/remember"
URL_CONSENT          = "/third-party-auth/cag"
URL_ACCOUNTS         = "/third-party-auth/account"
URL_CONFIRMATION     = "/third-party-auth/confirmation"


# Domains the OAuth flow walks through. Any URL outside these means the
# browser has been redirected to the caller's redirect_uri — at which
# point our job is done. We deliberately do NOT take the redirect_uri as
# a parameter; that's the caller's concern, not the script's.
SCHWAB_DOMAINS = ("schwab.com", "schwabapi.com")


def _is_off_schwab(url: str) -> bool:
    """Predicate: True when the URL is no longer on any Schwab domain."""
    return all(d not in url for d in SCHWAB_DOMAINS)


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------


class SchwabAuthCodeRelay(BaseAction):
    """Drives Schwab login → reaches relay redirect → minimal notify → exit.

    Differs from `SchwabAuth` (code_flow) only in the final PageAction: we
    send a "reached the redirect" message instead of the parsed code, because
    the relay has already captured the code server-side.
    """

    Params: ClassVar[type] = SchwabParams
    timeout_seconds = 600   # MFA on the phone can eat a minute or two

    def __init__(self, params=None):
        super().__init__(params)
        p = self.get_parameter

        self.actions = [
            # 1. Login form.
            PageAction(url_contains=URL_LOGIN, steps=[
                wait_visible(LOGIN_USERNAME),
                fill(LOGIN_USERNAME, p("USERNAME")),
                fill(LOGIN_PASSWORD, p("PASSWORD")),
                click(LOGIN_SUBMIT),
                wait_page_change(timeout=60),
            ]),

            # 2. MFA picker — click "Schwab App" to send the push.
            PageAction(url_contains=URL_MFA_PICKER, steps=[
                wait_visible(SCHWAB_APP_OPTION),
                click(SCHWAB_APP_OPTION),
                wait_url_contains(URL_MFA_WAITING, timeout=30),
            ]),

            # 3. MFA waiting — phone tap.
            PageAction(url_contains=URL_MFA_WAITING, steps=[
                wait_page_change(timeout=120),
            ]),

            # 4. Trust device — confirm + continue.
            PageAction(url_contains=URL_TRUST_DEVICE, steps=[
                wait_visible(TRUST_YES),
                click(TRUST_YES),
                click(TRUST_CONTINUE),
                wait_url_contains(URL_CONSENT, timeout=30),
            ]),

            # 5. Consent — accept terms + modal.
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

            # 6. Account selection — tick all + continue.
            PageAction(url_contains=URL_ACCOUNTS, steps=[
                wait_for_hydration(),
                wait_visible(ACCOUNT_CHECKBOX, timeout=60),
                check_all(ACCOUNT_CHECKBOX),
                click(ACCOUNT_CONTINUE),
                wait_url_contains(URL_CONFIRMATION, timeout=30),
            ]),

            # 7. Confirmation — click Done; browser redirects to relay.
            PageAction(url_contains=URL_CONFIRMATION, steps=[
                wait_for_hydration(),
                wait_visible(DONE, timeout=30),
                click(DONE),
            ]),

            # 8. Off Schwab — the browser has been redirected to the
            # caller's redirect_uri (the relay), which captured the code
            # server-side. `done()` ends the run cleanly without sending
            # a notification. The script never needs to know the redirect
            # URL — only that we left Schwab.
            PageAction(url=_is_off_schwab, steps=[
                custom(lambda h: done()),
            ]),
        ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    action = SchwabAuthCodeRelay()
    # `notification=` is optional — init() reads AUTOMATION_NOTIFICATION_*
    # from env if present, else runs in no-channel mode (notify() errors,
    # done() works). Compatible with both `webauto-cli` and `--no-notify`.
    # Schwab's bot checks need SeleniumBase's UC mode; Playwright is the default.
    init(url=action.params.URL, action=action, browser="seleniumbase")
