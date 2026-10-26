"""Unit tests for the YAML site loader and run_site()."""

from __future__ import annotations

import textwrap

import pytest

from webauto._site import SiteError, build_action, load_site, run_site
from webauto._verify import UnexpectedPage, VerificationFailed

SITE_YAML = textwrap.dedent(
    """
    name: demo
    start_url: https://h/logon
    params: [USERNAME, PASSWORD]
    verify_timeout: 0.1
    unexpected_page: {grace_seconds: 0.05}
    on_failure:
      - click_if_unique: "//a[@id='logout']"
    pages:
      - name: login
        url_contains: /logon
        steps:
          - verify:
              login form: "//form[@id='login']"
              user field: {selector: "//input[@id='u']", count: 1}
          - fill: {selector: "//input[@id='u']", value: $USERNAME}
          - fill: {selector: "//input[@id='p']", value: $PASSWORD}
          - click: "//button[@id='go']"
      - name: home
        url_contains: /home
        steps:
          - verify:
              banner: "//div[@id='banner']"
          - click_if_unique: "//a[@id='logout']"
          - done
    """
)


@pytest.fixture
def site_file(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text(SITE_YAML)
    return path


class TestLoad:
    def test_reads_pages_in_order(self, site_file) -> None:
        site = load_site(site_file)

        assert site.name == "demo"
        assert site.start_url == "https://h/logon"
        assert [p["name"] for p in site.pages] == ["login", "home"]

    def test_rejects_unknown_step(self, tmp_path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(SITE_YAML.replace("- done", "- explode: now"))

        with pytest.raises(SiteError, match="explode"):
            build_action(load_site(path), {"USERNAME": "u", "PASSWORD": "p"})

    def test_rejects_page_without_exactly_one_url_matcher(self, tmp_path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(SITE_YAML.replace("url_contains: /home", "name2: x"))

        with pytest.raises(SiteError, match="home"):
            build_action(load_site(path), {"USERNAME": "u", "PASSWORD": "p"})

    def test_missing_param_fails_before_browser_starts(self, site_file, handle) -> None:
        with pytest.raises(SiteError, match="PASSWORD"):
            run_site(site_file, {"USERNAME": "u"}, handle=handle)

        assert handle.calls == []

    def test_verify_timeout_defaults_to_ten_seconds_and_is_configurable(self, tmp_path) -> None:
        path = tmp_path / "t.yaml"
        without = SITE_YAML.replace("verify_timeout: 0.1\n", "")
        path.write_text(without)
        assert load_site(path).verify_timeout == 10.0

        path.write_text("verify_timeout: 3\n" + without)
        assert load_site(path).verify_timeout == 3.0

    def test_catch_all_page_is_added_last_with_high_order(self, site_file) -> None:
        action = build_action(load_site(site_file), {"USERNAME": "u", "PASSWORD": "p"})

        assert len(action.actions) == 3
        assert action.actions[-1].order >= 200
        assert action.actions[-1].url("https://anything/") is True


class TestRun:
    def _login_ok(self, handle) -> None:
        handle.visible |= {"//form[@id='login']", "//input[@id='u']", "//div[@id='banner']",
                           "//a[@id='logout']"}
        # fill, fill, click → the click (3rd step-advancing call) lands on /home
        handle.url_after_step = [None, None, "https://h/home"]

    def test_happy_path_fills_params_and_ends_on_done(self, site_file, handle) -> None:
        self._login_ok(handle)

        run_site(site_file, {"USERNAME": "alice", "PASSWORD": "pw"}, handle=handle)

        assert ("fill", "//input[@id='u']", "alice") in handle.calls
        assert ("fill", "//input[@id='p']", "pw") in handle.calls
        assert ("click", "//a[@id='logout']") in handle.calls
        assert handle.calls[-1] == ("close",)

    def test_verification_failure_stops_before_any_fill(self, site_file, handle, tmp_path) -> None:
        handle.visible |= {"//a[@id='logout']"}  # login form missing

        with pytest.raises(VerificationFailed) as err:
            run_site(site_file, {"USERNAME": "alice", "PASSWORD": "pw"}, handle=handle,
                     screenshot_dir=tmp_path)

        assert not [c for c in handle.calls if c[0] == "fill"]
        assert {r.name for r in err.value.results} == {"login form", "user field"}
        # on_failure cleanup ran (logout link was unique), then the browser closed
        assert ("click", "//a[@id='logout']") in handle.calls
        assert handle.calls[-1] == ("close",)
        assert any(c[0] == "screenshot" and str(tmp_path) in c[1] for c in handle.calls)

    def test_press_step(self, tmp_path, handle) -> None:
        path = tmp_path / "press.yaml"
        path.write_text(SITE_YAML.replace("- done", "- press: Escape\n      - done"))
        self._login_ok(handle)

        run_site(path, {"USERNAME": "a", "PASSWORD": "b"}, handle=handle)

        assert ("press", "Escape") in handle.calls

    def test_extra_steps_bare_and_with_argument(self, tmp_path, handle) -> None:
        path = tmp_path / "extra.yaml"
        path.write_text(SITE_YAML.replace("- done", "- solve_captcha\n      - stamp: hello\n      - done"))
        self._login_ok(handle)
        seen = []
        extra = {
            "solve_captcha": lambda arg: (lambda h: seen.append(("solve_captcha", arg))),
            "stamp": lambda arg: (lambda h: seen.append(("stamp", arg))),
        }

        run_site(path, {"USERNAME": "a", "PASSWORD": "b"}, handle=handle, extra_steps=extra)

        assert seen == [("solve_captcha", None), ("stamp", "hello")]

    def test_extra_step_cannot_shadow_builtin(self, site_file, handle) -> None:
        with pytest.raises(SiteError, match="click"):
            run_site(site_file, {"USERNAME": "a", "PASSWORD": "b"}, handle=handle,
                     extra_steps={"click": lambda arg: (lambda h: None)})

    def test_unknown_bare_step_still_rejected(self, tmp_path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(SITE_YAML.replace("- done", "- solve_captcha\n      - done"))

        with pytest.raises(SiteError, match="solve_captcha"):
            build_action(load_site(path), {"USERNAME": "u", "PASSWORD": "p"})

    def test_start_url_override(self, site_file, handle) -> None:
        with pytest.raises(UnexpectedPage):
            run_site(site_file, {"USERNAME": "a", "PASSWORD": "b"}, handle=handle,
                     start_url="https://other/site")

        assert handle.calls[0] == ("goto", "https://other/site")

    def test_unknown_page_fails_via_catch_all(self, site_file, handle) -> None:
        handle.url = "https://h/password-expired"

        with pytest.raises(UnexpectedPage):
            run_site(site_file, {"USERNAME": "alice", "PASSWORD": "pw"}, handle=handle,
                     navigate=False)
