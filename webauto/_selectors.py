"""Selector helpers shared by the browser backends.

A selector of the form "<frame selector> >> <element selector>" addresses an
element inside one iframe level: the left part must match exactly one iframe in
the page, the right part is resolved inside that iframe's document. Selectors
without " >> " are unchanged.
"""

from __future__ import annotations

from typing import Optional

FRAME_SEPARATOR = " >> "


def split_frame(selector: str) -> tuple[Optional[str], str]:
    if FRAME_SEPARATOR in selector:
        frame, inner = selector.split(FRAME_SEPARATOR, 1)
        return frame.strip(), inner.strip()
    return None, selector
