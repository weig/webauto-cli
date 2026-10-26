"""Declarative sites: a YAML file of pages, selectors and steps instead of a script.

    name: wbct
    start_url: https://example.com/logon
    params: [USERNAME, PASSWORD]          # must all be supplied to run_site()
    browser: playwright                   # optional; see init(browser=)
    timeout_seconds: 180                  # optional
    unexpected_page: {grace_seconds: 10}  # optional; `false` disables the catch-all
    on_failure:                           # optional cleanup, run when a step raises
      - click_if_unique: "//a[@href='/logout']"
    pages:
      - name: login
        url_contains: /logon              # exactly one of url_contains / url_equals /
                                          # url_startswith / url_regex / url_any
        steps:
          - verify:                       # every check is evaluated and reported
              login form: "//form[@id='loginForm'][count(.//input)=2]"
              rows: {selector: ".row", count: 3}
          - fill: {selector: "#UserName", value: $USERNAME}
          - click: "#btnLogon"
          - wait_page_change: 30
          - done

Step keys: verify, fill, click, click_if_unique, check, uncheck, wait_visible,
wait_url_contains, wait_page_change, sleep, screenshot, done.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

import yaml

from webauto import _factories as f
from webauto._entry import init
from webauto._notify import done
from webauto._types import BaseAction, PageAction, Step
from webauto._verify import click_if_unique, expect, unexpected_page, verify_all

URL_MATCHERS = ("url_contains", "url_equals", "url_startswith", "url_regex", "url_any")
DEFAULT_GRACE_SECONDS = 10.0
CATCH_ALL_ORDER = 1000


class SiteError(ValueError):
    """The site file is malformed or a required param is missing."""


@dataclass(frozen=True)
class Site:
    name: str
    start_url: str
    params: tuple
    pages: list
    on_failure: list
    unexpected_page: Optional[dict]
    timeout_seconds: float
    browser: Optional[str]


def load_site(path: Union[str, Path]) -> Site:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise SiteError(f"{path}: expected a mapping at the top level")
    for key in ("name", "start_url", "pages"):
        if key not in data:
            raise SiteError(f"{path}: missing required key {key!r}")
    grace = data.get("unexpected_page", {})
    return Site(
        name=str(data["name"]),
        start_url=str(data["start_url"]),
        params=tuple(data.get("params") or ()),
        pages=list(data["pages"]),
        on_failure=list(data.get("on_failure") or ()),
        unexpected_page=None if grace is False else dict(grace or {}),
        timeout_seconds=float(data.get("timeout_seconds", BaseAction.timeout_seconds)),
        browser=data.get("browser"),
    )


def _value(raw: Any, params: dict) -> Any:
    if isinstance(raw, str) and raw.startswith("$"):
        return params[raw[1:]]
    return raw


def _shot(screenshot_dir: Optional[Path], name: str) -> Optional[str]:
    return str(screenshot_dir / f"{name}.png") if screenshot_dir else None


def _verify_step(spec: dict, label: str, screenshot_dir: Optional[Path]) -> Step:
    expectations = []
    for name, check in spec.items():
        if isinstance(check, str):
            expectations.append(expect(name, check))
        else:
            expectations.append(expect(name, check["selector"], int(check.get("count", 1))))
    return verify_all(*expectations, screenshot=_shot(screenshot_dir, f"{label}_failed"))


def _done_step(_handle: Any) -> None:
    done()


def _build_step(raw: Any, params: dict, label: str, screenshot_dir: Optional[Path]) -> Step:
    if raw == "done":
        return f.custom(_done_step)
    if not isinstance(raw, dict) or len(raw) != 1:
        raise SiteError(f"{label}: each step must be 'done' or a single-key mapping, got {raw!r}")
    (kind, arg), = raw.items()
    builders: dict[str, Callable[[], Step]] = {
        "verify": lambda: _verify_step(arg, label, screenshot_dir),
        "fill": lambda: f.fill(arg["selector"], _value(arg["value"], params)),
        "click": lambda: f.click(arg),
        "click_if_unique": lambda: click_if_unique(arg),
        "check": lambda: f.check(arg),
        "uncheck": lambda: f.uncheck(arg),
        "wait_visible": lambda: (f.wait_visible(arg) if isinstance(arg, str)
                                 else f.wait_visible(arg["selector"], float(arg.get("timeout", 30)))),
        "wait_url_contains": lambda: f.wait_url_contains(arg),
        "wait_page_change": lambda: f.wait_page_change(float(arg or 30)),
        "sleep": lambda: f.sleep(int(arg)),
        "screenshot": lambda: f.screenshot(arg),
    }
    if kind not in builders:
        raise SiteError(f"{label}: unknown step {kind!r}; known: {', '.join(sorted(builders))}, done")
    return builders[kind]()


def _page_action(page: dict, params: dict, screenshot_dir: Optional[Path]) -> PageAction:
    name = page.get("name", "?")
    matchers = {k: page[k] for k in URL_MATCHERS if k in page}
    if len(matchers) != 1:
        raise SiteError(f"page {name!r}: needs exactly one of {', '.join(URL_MATCHERS)}")
    steps = [_build_step(raw, params, f"{name}_step{i}", screenshot_dir)
             for i, raw in enumerate(page.get("steps") or [], start=1)]
    return PageAction(steps=steps, order=int(page.get("order", 100)), **matchers)


def build_action(site: Site, params: dict, screenshot_dir: Optional[Path] = None) -> BaseAction:
    missing = [p for p in site.params if p not in params]
    if missing:
        raise SiteError(f"site {site.name!r}: missing params {', '.join(missing)}")
    actions = [_page_action(page, params, screenshot_dir) for page in site.pages]
    if site.unexpected_page is not None:
        grace = float(site.unexpected_page.get("grace_seconds", DEFAULT_GRACE_SECONDS))
        actions.append(PageAction(url_any=True, order=CATCH_ALL_ORDER, steps=[
            unexpected_page(grace_seconds=grace, screenshot=_shot(screenshot_dir, "unexpected_page")),
        ]))
    cleanup = [_build_step(raw, params, f"on_failure{i}", None) for i, raw in enumerate(site.on_failure, 1)]

    action_cls = type(f"Site_{site.name}", (BaseAction,), {"timeout_seconds": site.timeout_seconds})
    action = action_cls()
    action.actions = actions
    action.on_failure = cleanup
    return action


def run_site(
    path: Union[str, Path],
    params: dict,
    *,
    handle: Any = None,
    browser: Optional[str] = None,
    screenshot_dir: Optional[Union[str, Path]] = None,
    navigate: bool = True,
) -> None:
    """Load a site file and drive it. Raises VerificationFailed / UnexpectedPage on mismatch.

    `navigate=False` starts from the page the handle is already on instead of
    `start_url` (useful when attaching to a browser someone has open).
    """
    site = load_site(path)
    shots = Path(screenshot_dir) if screenshot_dir else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    action = build_action(site, params, shots)
    url = site.start_url
    if not navigate:
        if handle is None:
            raise SiteError("navigate=False needs an explicit handle")
        url = handle.url
    init(url=url, action=action, handle=handle, browser=browser or site.browser)
