"""Selector helpers shared by the browser backends.

A selector of the form "<frame selector> >> <element selector>" addresses an
element inside one iframe level: the left part must match exactly one iframe in
the page, the right part is resolved inside that iframe's document. Selectors
without " >> " are unchanged.

webauto's " >> " only means "enter this iframe". It is not Playwright's
locator chaining (find b inside a); write that as a single XPath or CSS
selector instead. The split happens at the first " >> ", so a `text=` value
containing a literal " >> " would be read as a frame selector; avoid it.

SeleniumBase's wait_for_selector does not wait for a frame that is not there
yet. To wait for a late iframe, use verify (it re-counts until its timeout)
or wait_visible on the iframe itself first.
"""

from __future__ import annotations

from typing import Optional

FRAME_SEPARATOR = " >> "


def split_frame(selector: str) -> tuple[Optional[str], str]:
    if FRAME_SEPARATOR in selector:
        frame, inner = selector.split(FRAME_SEPARATOR, 1)
        return frame.strip(), inner.strip()
    return None, selector
