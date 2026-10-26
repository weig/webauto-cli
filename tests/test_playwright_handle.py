"""PlaywrightHandle against a local HTML page in headless Chrome (no network)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

CHROME = Path("/Applications/Google Chrome.app") if Path("/Applications").exists() else None
pytestmark = pytest.mark.skipif(
    not ((CHROME and CHROME.exists()) or shutil.which("google-chrome")),
    reason="needs a local Chrome for the playwright 'chrome' channel",
)

PAGE = """<!doctype html><html><head><title>Log In</title></head><body>
<form id="login" action="/logon" method="post">
  <div class="label">User Name :</div><input id="u" type="text">
  <input id="p" type="password">
  <button id="go" type="button" onclick="location.hash='#/home'">Log On</button>
</form>
<a class="dup">x</a><a class="dup">y</a>
<script>document.addEventListener('keydown', e => { if (e.key === 'Escape') location.hash = '#/escaped'; });</script>
</body></html>"""


@pytest.fixture
def pw(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADLESS", "1")
    monkeypatch.delenv("WEBAUTO_CDP_URL", raising=False)
    from webauto._playwright_handle import PlaywrightHandle

    page = tmp_path / "login.html"
    page.write_text(PAGE)
    handle = PlaywrightHandle()
    handle.goto(page.as_uri())
    yield handle
    handle.close()


def test_count_supports_xpath_css_and_text(pw) -> None:
    assert pw.count("//form[@id='login'][count(.//input|.//button)=3]") == 1
    assert pw.count("//form[@id='login'][count(.//input|.//button)=4]") == 0
    assert pw.count("#u") == 1
    assert pw.count("a.dup") == 2
    assert pw.count("text=User Name") >= 1
    assert pw.count("//title[normalize-space()='Log In']") == 1


def test_fill_click_and_url(pw) -> None:
    pw.fill("//input[@id='u']", "alice")
    pw.click("//button[@id='go']")
    pw.wait_for_url("#/home", timeout=5)

    assert pw.url.endswith("#/home")
    assert pw.is_visible("#u") is True


def test_screenshot_writes_file(pw, tmp_path) -> None:
    out = tmp_path / "shot.png"
    pw.fill("#p", "secret")

    pw.screenshot(str(out))

    assert out.exists() and out.stat().st_size > 0


def test_ambiguous_click_is_refused(pw) -> None:
    from playwright.sync_api import Error

    with pytest.raises(Error, match="strict mode violation"):
        pw.click("a.dup")


def test_press_sends_key_to_page(pw) -> None:
    pw.press("Escape")
    pw.wait_for_url("#/escaped", timeout=5)

    assert pw.url.endswith("#/escaped")


def test_evaluate_runs_script_with_argument(pw) -> None:
    assert pw.evaluate("(x) => x * 2", 21) == 42
    assert pw.evaluate("() => document.title") == "Log In"


FRAME_PAGE = """<!doctype html><html><head><title>Outer</title></head><body>
<iframe id="inner" srcdoc="<input id='x' type='password'><button id='b' onclick=&quot;parent.location.hash='#/clicked'&quot;>Go</button>"></iframe>
<input id="x" type="text">
</body></html>"""


@pytest.fixture
def framed(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADLESS", "1")
    monkeypatch.delenv("WEBAUTO_CDP_URL", raising=False)
    from webauto._playwright_handle import PlaywrightHandle

    page = tmp_path / "framed.html"
    page.write_text(FRAME_PAGE)
    handle = PlaywrightHandle()
    handle.goto(page.as_uri())
    yield handle
    handle.close()


def test_frame_selector_counts_inside_the_iframe(framed) -> None:
    assert framed.count("//iframe[@id='inner'] >> //input[@id='x' and @type='password']") == 1
    assert framed.count("//input[@id='x' and @type='password']") == 0  # not visible from the page
    assert framed.count("//iframe[@id='missing'] >> //input") == 0


def test_frame_selector_fill_and_click(framed) -> None:
    framed.fill("//iframe[@id='inner'] >> //input[@id='x']", "secret")
    framed.click("//iframe[@id='inner'] >> //button[@id='b']")
    framed.wait_for_url("#/clicked", timeout=5)

    value = framed.evaluate("() => document.getElementById('inner').contentDocument.getElementById('x').value")
    assert value == "secret"
