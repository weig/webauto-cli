"""Schwab OAuth — code_relay flow.

Translates schwab_cli's existing auto-login flow (see
schwab_cli/browser/_seleniumbase_flow.py and schwab_cli/browser/selectors.py)
into a webauto action script.

Only the code_relay method is supported. The script drives the full
login → MFA → consent → accounts → confirmation flow; when the browser
finally hits the relay's callback URL (which contains `?code=...&state=...`),
the script forwards the full URL via `notify({"url": handle.url})` and
exits. The caller (e.g. schwab_cli) is responsible for long-polling the
relay's separate storage endpoint to fetch the captured code.

Selectors mirror selectors.py at rev 2026-04-21. If Schwab changes the UI,
regenerate by running with DEBUG=1 and inspecting the dumped HTML.

Caveat: a few Schwab pages (consent checkbox, account checkbox list, modal
Accept) listen for Angular reactive-form events that native Selenium clicks
sometimes miss. The original schwab_cli wraps these in JS-dispatch helpers
(focus + .value + input/change/blur). Our framework's `fill`/`click`/`check`
factories use SeleniumBase's `.type()`/`.click()` which work in most cases.
If a step fails, swap to `custom(...)` with a callable that drives the
underlying driver directly (see `SeleniumBaseHandle._driver`).

Run via:
    webauto-cli examples/schwab_auth.py \\
        --env .schwab.env

Where .schwab.env contains:
    URL=https://api.schwabapi.com/v1/oauth/authorize?client_id=...&redirect_uri=...&...
    USERNAME=alice
    PASSWORD=hunter2
    REDIRECT_URI=https://relay.example.com/callback
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar

from webauto import (
    init,
    notify,
    parse_url,
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

# Login page (URL: /login-one-step)
LOGIN_USERNAME       = "input#loginIdInput"
LOGIN_PASSWORD       = "input#passwordInput"
LOGIN_SUBMIT         = "button#btnLogin"

# MFA picker (URL: /authenticators)
SCHWAB_APP_OPTION    = "#mobile_approve"

# Trust device (URL: /devicetag/remember)
TRUST_YES            = "input#remember-device-yes"
TRUST_CONTINUE       = "button#btnContinue"

# Consent (URL: /third-party-auth/cag)
CONSENT_CHECKBOX     = "input#acceptTerms"
CONSENT_CONTINUE     = "button#submit-btn"
CONSENT_MODAL_ACCEPT = "button#agree-modal-btn-"   # trailing hyphen is intentional

# Account selection (URL: /third-party-auth/account)
ACCOUNT_CHECKBOX     = 'input[type="checkbox"]'
ACCOUNT_CONTINUE     = "button#submit-btn"

# Confirmation (URL: /third-party-auth/confirmation)
DONE                 = "button#cancel-btn"          # id literally says "cancel"


# ---------------------------------------------------------------------------
# URL fragments — what each PageAction matches on
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


class SchwabAuth(BaseAction):
    """Drives Schwab login → captures relay redirect URL → notifies."""

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
                # Post-login destination depends on Schwab's state for this
                # account: → /authenticators (need to pick MFA method),
                #           → /mobile_approve (MFA preference remembered),
                #           or → /third-party-auth/cag (skipped MFA entirely).
                wait_page_change(timeout=60),
            ]),

            # 2. MFA picker — click the "Schwab App" option to send the push.
            PageAction(url_contains=URL_MFA_PICKER, steps=[
                wait_visible(SCHWAB_APP_OPTION),
                click(SCHWAB_APP_OPTION),
                wait_url_contains(URL_MFA_WAITING, timeout=30),
            ]),

            # 3. MFA waiting — phone tap can take ~60s.
            PageAction(url_contains=URL_MFA_WAITING, steps=[
                # No interaction; just wait for the URL to change after the
                # user approves on their phone.
                # Destination: /devicetag/remember (first time on this device)
                #              or /third-party-auth/cag (trust cookie already set).
                wait_page_change(timeout=120),
            ]),

            # 4. Trust device — say yes so the trust cookie sticks in the profile.
            PageAction(url_contains=URL_TRUST_DEVICE, steps=[
                wait_visible(TRUST_YES),
                click(TRUST_YES),
                click(TRUST_CONTINUE),
                wait_url_contains(URL_CONSENT, timeout=30),
            ]),

            # 5. Consent — accept terms, continue, accept the modal.
            # The /cag page renders with title="cag" until Angular hydrates;
            # clicking buttons before hydration silently no-ops because the
            # event handlers haven't been wired up yet. `wait_for_hydration`
            # polls for the title change. `sleep(1000)` after the modal click
            # gives the accept handler time to start navigating.
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

            # 6. Account selection — Schwab fetches the list async, so
            # checkboxes don't exist when the URL flips. Wait, then tick all.
            PageAction(url_contains=URL_ACCOUNTS, steps=[
                wait_for_hydration(),
                wait_visible(ACCOUNT_CHECKBOX, timeout=60),
                check_all(ACCOUNT_CHECKBOX),
                click(ACCOUNT_CONTINUE),
                wait_url_contains(URL_CONFIRMATION, timeout=30),
            ]),

            # 7. Confirmation — click Done; browser then redirects to the relay.
            # No `wait_url_contains(REDIRECT_URI, ...)` here: that would do a
            # substring match, which falsely succeeds on any intermediate URL
            # that embeds REDIRECT_URI as a query parameter. The dispatch
            # loop polls between PageActions anyway — PA 8 will fire as soon
            # as the URL truly starts with REDIRECT_URI.
            PageAction(url_contains=URL_CONFIRMATION, steps=[
                wait_for_hydration(),
                wait_visible(DONE, timeout=30),
                click(DONE),
            ]),

            # 8. Off Schwab — we've navigated away from any Schwab domain,
            # which means the OAuth flow landed on whatever redirect_uri the
            # caller embedded in the authorize URL. The script doesn't need
            # to know what that URL was; the caller does. We parse current
            # URL into {base_url, code, state, ...} and forward via notify().
            PageAction(url=_is_off_schwab, steps=[
                custom(lambda h: notify(parse_url())),
            ]),
        ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    action = SchwabAuth()
    init(
        url=action.params.URL,
        notification={
            "type":     os.environ["AUTOMATION_NOTIFICATION_TYPE"],
            "endpoint": os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
        },
        action=action,
        # Schwab's bot checks need SeleniumBase's UC mode; Playwright is the default.
        browser="seleniumbase",
    )
