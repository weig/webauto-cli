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
