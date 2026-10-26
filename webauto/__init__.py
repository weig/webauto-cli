"""webauto — standalone webauto library + CLI runner.

Public API surface; internals live in modules prefixed with `_`.

Quickstart:

    from webauto import (
        init, notify,
        BaseAction, PageAction,
        fill, click, wait_visible, custom,
    )

    class Scrape(BaseAction):
        actions = [
            PageAction(url_contains="/quote/", steps=[
                wait_visible(".price"),
                custom(lambda h: notify({"price": h.text(".price")})),
            ]),
        ]

    if __name__ == "__main__":
        init(
            url="https://example.com/quote/AAPL",
            notification={
                "type":     os.environ["AUTOMATION_NOTIFICATION_TYPE"],
                "endpoint": os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
            },
            action=Scrape(),
        )
"""

from webauto._types import (
    BaseAction,
    BrowserHandle,
    PageAction,
)
from webauto._matchers import (
    any_url,
    contains,
    equals,
    regex,
    startswith,
)
from webauto._url import parse_url
from webauto._factories import (
    check,
    check_all,
    click,
    custom,
    fill,
    press,
    screenshot,
    sleep,
    uncheck,
    wait_for_hydration,
    wait_page_change,
    wait_url_contains,
    wait_visible,
)

# init() and notify() are wired up in Task F (depend on _notification_client + _runner).
from webauto._notify import done, notify
from webauto._entry import init
from webauto._verify import (
    CheckResult,
    Expectation,
    UnexpectedPage,
    VerificationFailed,
    click_if_unique,
    expect,
    unexpected_page,
    verify_all,
)
from webauto._site import Site, SiteError, load_site, run_site

__version__ = "0.1.0"

__all__ = [
    # Entry + completion
    "init",
    "notify",
    "done",
    # Types
    "BaseAction",
    "BrowserHandle",
    "PageAction",
    # URL matchers
    "any_url",
    "contains",
    "equals",
    "regex",
    "startswith",
    # Step factories
    "check",
    "check_all",
    "click",
    "custom",
    "fill",
    "press",
    "screenshot",
    "sleep",
    "uncheck",
    "wait_for_hydration",
    "wait_page_change",
    "wait_url_contains",
    "wait_visible",
    # URL helper
    "parse_url",
    # Verification
    "CheckResult",
    "Expectation",
    "UnexpectedPage",
    "VerificationFailed",
    "click_if_unique",
    "expect",
    "unexpected_page",
    "verify_all",
    # Declarative sites
    "Site",
    "SiteError",
    "load_site",
    "run_site",
]
