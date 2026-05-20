"""Detect the installed Chrome's major version, or honor CHROME_VERSION env override.

The detected version is passed to SeleniumBase so chromedriver gets pinned
to a matching major version (mismatches cause "session not created" errors
on every launch).

Detection is cross-platform with best-effort: macOS reads the app bundle's
binary, Linux tries `google-chrome --version` and variants, Windows reads
the registry.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Optional


class ChromeNotFoundError(RuntimeError):
    """Raised when neither CHROME_VERSION is set nor a Chrome binary is detectable."""


# ----------------------------------------------------------------------------
# Per-platform detectors
# ----------------------------------------------------------------------------


_MAC_BINARIES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

_LINUX_BINARIES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
]


def _parse_major(version_text: str) -> Optional[int]:
    """Extract the leading integer from a 'Google Chrome 124.0.6367.207' style string."""
    match = re.search(r"(\d+)\.\d+\.\d+\.\d+", version_text)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)", version_text)
    if match:
        return int(match.group(1))
    return None


def _try_run(cmd: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _detect_mac() -> Optional[int]:
    for binary in _MAC_BINARIES:
        if os.path.exists(binary):
            text = _try_run([binary, "--version"])
            if text:
                v = _parse_major(text)
                if v is not None:
                    return v
    return None


def _detect_linux() -> Optional[int]:
    for binary in _LINUX_BINARIES:
        text = _try_run([binary, "--version"])
        if text:
            v = _parse_major(text)
            if v is not None:
                return v
    return None


def _detect_windows() -> Optional[int]:
    # Try the registry first (cheap, no subprocess).
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        winreg = None

    if winreg is not None:
        for hive, path in [
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        ]:
            try:
                with winreg.OpenKey(hive, path) as key:
                    value, _ = winreg.QueryValueEx(key, "version")
                    v = _parse_major(value)
                    if v is not None:
                        return v
            except OSError:
                continue

    # Fall back to PowerShell.
    for path in [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]:
        if os.path.exists(path):
            text = _try_run([
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Item '{path}').VersionInfo.ProductVersion",
            ])
            if text:
                v = _parse_major(text)
                if v is not None:
                    return v
    return None


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


def detect_chrome_version() -> Optional[int]:
    """Return the installed Chrome's major version, or None if undetectable."""
    if sys.platform == "darwin":
        return _detect_mac()
    if sys.platform.startswith("linux"):
        return _detect_linux()
    if sys.platform == "win32":
        return _detect_windows()
    return None


def resolve_chrome_version() -> int:
    """Return the major version to pin chromedriver against.

    Priority:
      1. CHROME_VERSION env var (caller-pinned).
      2. detect_chrome_version().
      3. Raise ChromeNotFoundError pointing to `webauto-cli doctor`.
    """
    env_val = os.environ.get("CHROME_VERSION")
    if env_val is not None:
        env_val = env_val.strip()
        if not env_val:
            raise ValueError("CHROME_VERSION env var is set but empty")
        # Accept "124" or "124.0.6367.207"; take the leading integer.
        v = _parse_major(env_val)
        if v is None:
            raise ValueError(
                f"CHROME_VERSION={env_val!r} is not parseable as a Chrome version"
            )
        return v

    detected = detect_chrome_version()
    if detected is not None:
        return detected

    raise ChromeNotFoundError(
        "Could not detect Chrome's major version. Either install Google Chrome "
        "(run `webauto-cli bootstrap --yes`) or set CHROME_VERSION explicitly."
    )
