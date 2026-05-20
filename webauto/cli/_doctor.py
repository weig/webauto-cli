"""webauto-cli doctor + bootstrap subcommands."""

from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from webauto._chrome_bootstrap import detect_chrome_version


# ----------------------------------------------------------------------------
# doctor — environment health check
# ----------------------------------------------------------------------------


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def _row(level: str, name: str, detail: str = "") -> Tuple[str, str, str]:
    return (level, name, detail)


def _check_chrome() -> Tuple[str, str, str]:
    v = detect_chrome_version()
    if v is None:
        return _row(FAIL, "Chrome binary", _install_command_hint())
    return _row(PASS, "Chrome binary", f"detected major version {v}")


def _check_chrome_version_pin() -> Tuple[str, str, str]:
    env_val = os.environ.get("CHROME_VERSION")
    if env_val is None:
        return _row(PASS, "CHROME_VERSION pin", "unset; using auto-detection")
    detected = detect_chrome_version()
    if detected is None:
        return _row(
            WARN,
            "CHROME_VERSION pin",
            f"pinned to {env_val} but couldn't detect installed Chrome",
        )
    try:
        pinned = int(env_val.split(".")[0])
    except ValueError:
        return _row(FAIL, "CHROME_VERSION pin", f"{env_val!r} not parseable")
    delta = abs(pinned - detected)
    if delta > 1:
        return _row(
            WARN,
            "CHROME_VERSION pin",
            f"pinned to {pinned}; installed Chrome is {detected} (diff {delta})",
        )
    return _row(PASS, "CHROME_VERSION pin", f"pin={pinned}; installed={detected}")


def _check_profile_dir() -> Tuple[str, str, str]:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    path = Path(base) / "webauto" / "chromium-uc"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _row(FAIL, "Profile dir", f"{path}: {e}")
    if not os.access(path, os.W_OK):
        return _row(FAIL, "Profile dir", f"{path}: not writable")
    return _row(PASS, "Profile dir", str(path))


def _check_seleniumbase() -> Tuple[str, str, str]:
    try:
        import seleniumbase  # type: ignore[import-not-found]
    except ImportError as e:
        return _row(FAIL, "SeleniumBase", f"not importable: {e}")
    version = getattr(seleniumbase, "__version__", "unknown")
    return _row(PASS, "SeleniumBase", f"version {version}")


def _check_loopback_port() -> Tuple[str, str, str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return _row(PASS, "Loopback port", f"bound 127.0.0.1:{port}")
    except OSError as e:
        return _row(FAIL, "Loopback port", f"cannot bind: {e}")


def _install_command_hint() -> str:
    if sys.platform == "darwin":
        return "Chrome not found. Install: brew install --cask google-chrome"
    if sys.platform.startswith("linux"):
        return (
            "Chrome not found. Install (Debian/Ubuntu): "
            "wget -qO - https://dl.google.com/linux/linux_signing_key.pub "
            "| sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg "
            "&& echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg]"
            " https://dl.google.com/linux/chrome/deb/ stable main' "
            "| sudo tee /etc/apt/sources.list.d/google-chrome.list "
            "&& sudo apt update && sudo apt install -y google-chrome-stable"
        )
    if sys.platform == "win32":
        return "Chrome not found. Install: winget install Google.Chrome"
    return f"Chrome not found. Platform {sys.platform} unsupported."


def doctor_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="webauto-cli doctor",
        description="Environment health check.",
    )
    parser.parse_args(argv)

    checks: List[Tuple[str, str, str]] = [
        _check_chrome(),
        _check_chrome_version_pin(),
        _check_profile_dir(),
        _check_seleniumbase(),
        _check_loopback_port(),
    ]

    name_width = max(len(c[1]) for c in checks)
    any_fail = False
    for level, name, detail in checks:
        print(f"  [{level}] {name.ljust(name_width)}  {detail}")
        if level == FAIL:
            any_fail = True

    if any_fail:
        print("\nOne or more checks FAILED. See messages above.", file=sys.stderr)
        return 1
    return 0


# ----------------------------------------------------------------------------
# bootstrap — install Chrome (per-OS) + pre-warm chromedriver
# ----------------------------------------------------------------------------


def _install_commands() -> List[List[str]]:
    if sys.platform == "darwin":
        return [["brew", "install", "--cask", "google-chrome"]]
    if sys.platform.startswith("linux"):
        # Detect apt vs dnf vs pacman.
        if _which("apt-get"):
            keyring = "/usr/share/keyrings/google-chrome.gpg"
            return [
                [
                    "bash",
                    "-c",
                    "wget -qO - https://dl.google.com/linux/linux_signing_key.pub "
                    f"| sudo gpg --dearmor -o {keyring}",
                ],
                [
                    "bash",
                    "-c",
                    f"echo 'deb [arch=amd64 signed-by={keyring}] "
                    "https://dl.google.com/linux/chrome/deb/ stable main' "
                    "| sudo tee /etc/apt/sources.list.d/google-chrome.list",
                ],
                ["sudo", "apt-get", "update"],
                ["sudo", "apt-get", "install", "-y", "google-chrome-stable"],
            ]
        if _which("dnf"):
            return [
                ["sudo", "dnf", "install", "-y", "google-chrome-stable"],
            ]
        return []
    if sys.platform == "win32":
        return [["winget", "install", "-e", "--id", "Google.Chrome"]]
    return []


def _which(cmd: str) -> Optional[str]:
    from shutil import which
    return which(cmd)


def _warm_chromedriver() -> int:
    """Try to launch SeleniumBase once so chromedriver gets cached."""
    print("[bootstrap] pre-warming chromedriver (may download ~10MB)...")
    try:
        from webauto._seleniumbase_handle import SeleniumBaseHandle

        # Force headless for the warm-up.
        prev = os.environ.get("HEADLESS")
        os.environ["HEADLESS"] = "1"
        try:
            h = SeleniumBaseHandle()
            h.goto("about:blank")
            h.close()
        finally:
            if prev is None:
                os.environ.pop("HEADLESS", None)
            else:
                os.environ["HEADLESS"] = prev
        print("[bootstrap] chromedriver ready.")
        return 0
    except Exception as e:
        print(f"[bootstrap] chromedriver warm-up failed: {e}", file=sys.stderr)
        return 1


def bootstrap_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="webauto-cli bootstrap",
        description="Install Chrome + pre-warm chromedriver.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually run the install commands. Without --yes, prints them only.",
    )
    parser.add_argument(
        "--skip-warm",
        action="store_true",
        help="Don't pre-warm chromedriver after install.",
    )
    args = parser.parse_args(argv)

    cmds = _install_commands()
    if not cmds:
        print(
            f"[bootstrap] no install recipe for platform {sys.platform!r}. "
            "Please install Chrome manually.",
            file=sys.stderr,
        )
        return 1

    if not args.yes:
        print("[bootstrap] would run (pass --yes to execute):")
        for cmd in cmds:
            print("  $", " ".join(cmd))
        if not args.skip_warm:
            print("[bootstrap]   ...then warm chromedriver")
        return 0

    for cmd in cmds:
        print("[bootstrap] $", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[bootstrap] command failed: {e}", file=sys.stderr)
            return e.returncode

    if not args.skip_warm:
        return _warm_chromedriver()
    return 0
