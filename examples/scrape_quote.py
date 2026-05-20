"""Sample webauto action script — scrape a quote page.

Demonstrates that nothing about the framework requires OAuth. The action
navigates to a quote page, waits for the price element, builds a payload
dict, and calls notify(payload) — the framework JSON-encodes and sends.

Run via:
    webauto-cli examples/scrape_quote.py \
        -a URL=https://example.com/quote/AAPL \
        -a SYMBOL=AAPL
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from typing import ClassVar

from webauto import (
    init,
    notify,
    BaseAction,
    PageAction,
    custom,
    wait_visible,
)


# --- inputs ----------------------------------------------------------------


@dataclass
class ScrapeParams:
    URL: str
    SYMBOL: str = ""


# --- action ----------------------------------------------------------------


class ScrapeQuote(BaseAction):
    Params: ClassVar[type] = ScrapeParams
    timeout_seconds = 60

    def __init__(self, params=None) -> None:
        super().__init__(params)

        self.actions = [
            PageAction(url_contains="/quote/", steps=[
                wait_visible(".price"),
                custom(self._capture),
            ]),
        ]

    def _capture(self, handle) -> None:
        # In a real implementation, handle would expose a .text(selector)
        # accessor; the BrowserHandle Protocol in this sample is minimal,
        # so we just send the URL as a proof-of-life payload.
        notify({
            "symbol": self.params.SYMBOL,
            "url":    handle.url,
            "ts":     datetime.datetime.utcnow().isoformat() + "Z",
        })


# --- entry point -----------------------------------------------------------


if __name__ == "__main__":
    action = ScrapeQuote()
    init(
        url=action.params.URL,
        notification={
            "type":     os.environ["AUTOMATION_NOTIFICATION_TYPE"],
            "endpoint": os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
        },
        action=action,
    )
