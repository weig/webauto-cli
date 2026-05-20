"""Integration tests for webauto-cli.

Runs the CLI as a subprocess against tiny fixture action scripts that don't
need a real browser — they read AUTOMATION_NOTIFICATION_* from env and POST
directly with urllib.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import pytest


CLI = [sys.executable, "-m", "webauto.cli"]


def _write_fixture(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


# A minimal action-script that just POSTs whatever it reads from env.
ECHO_FIXTURE = '''
    import json, os, urllib.request

    payload = {
        "got_foo":    os.environ.get("FOO"),
        "got_symbol": os.environ.get("SYMBOL"),
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()
'''

SLEEP_FOREVER_FIXTURE = '''
    import time
    while True:
        time.sleep(1)
'''


# ----------------------------------------------------------------------------
# Run mode — listener
# ----------------------------------------------------------------------------


class TestListenerMode:
    def test_basic_round_trip(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "echo.py", ECHO_FIXTURE)
        result = subprocess.run(
            CLI + [str(script), "-a", "FOO=bar"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["got_foo"] == "bar"
        assert out["got_symbol"] is None

    def test_repeated_a_last_wins(self, tmp_path: Path) -> None:
        # Repeated -a for the same key: last value overrides earlier ones.
        # No JSON-array encoding — values are bare strings.
        script = _write_fixture(
            tmp_path,
            "multi.py",
            '''
            import json, os, urllib.request
            body = json.dumps({"got": os.environ.get("SYMBOL")}).encode()
            urllib.request.urlopen(urllib.request.Request(
                os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=5).read()
            ''',
        )
        result = subprocess.run(
            CLI + [str(script), "-a", "SYMBOL=AAPL", "-a", "SYMBOL=MSFT"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Last -a wins. Bare string, no JSON array.
        assert out["got"] == "MSFT"

    def test_env_file_loaded(self, tmp_path: Path) -> None:
        # Dotenv file values reach the script.
        envfile = tmp_path / ".env"
        envfile.write_text(
            "# comment line\n"
            "\n"
            "FOO=from_file\n"
            "QUOTED=\"with spaces\"\n"
        )
        script = _write_fixture(
            tmp_path,
            "echo.py",
            '''
            import json, os, urllib.request
            body = json.dumps({
                "foo":    os.environ.get("FOO"),
                "quoted": os.environ.get("QUOTED"),
            }).encode()
            urllib.request.urlopen(urllib.request.Request(
                os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=5).read()
            ''',
        )
        result = subprocess.run(
            CLI + [str(script), "--env", str(envfile)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["foo"] == "from_file"
        assert out["quoted"] == "with spaces"

    def test_dash_a_overrides_env_file(self, tmp_path: Path) -> None:
        # `-a KEY=...` fully overrides the file's value for the same key.
        envfile = tmp_path / ".env"
        envfile.write_text("FOO=from_file\n")
        script = _write_fixture(
            tmp_path,
            "echo.py",
            '''
            import json, os, urllib.request
            body = json.dumps({"foo": os.environ.get("FOO")}).encode()
            urllib.request.urlopen(urllib.request.Request(
                os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=5).read()
            ''',
        )
        result = subprocess.run(
            CLI + [str(script), "--env", str(envfile), "-a", "FOO=from_cli"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["foo"] == "from_cli"

    def test_env_file_malformed_rejected(self, tmp_path: Path) -> None:
        envfile = tmp_path / "bad.env"
        envfile.write_text("KEY_ONLY_NO_EQUALS\n")
        script = _write_fixture(tmp_path, "noop.py", "pass\n")
        result = subprocess.run(
            CLI + [str(script), "--env", str(envfile)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        # malformed-line error surfaces via Python traceback or our handler.
        assert (
            "malformed" in result.stderr
            or "KEY_ONLY_NO_EQUALS" in result.stderr
        )

    def test_malformed_a_rejected(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "noop.py", "pass\n")
        result = subprocess.run(
            CLI + [str(script), "-a", "NOEQUALS"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "KEY=VALUE" in result.stderr

    def test_empty_key_rejected(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "noop.py", "pass\n")
        result = subprocess.run(
            CLI + [str(script), "-a", "=value"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "empty key" in result.stderr

    def test_script_not_found(self, tmp_path: Path) -> None:
        result = subprocess.run(
            CLI + [str(tmp_path / "does_not_exist.py")],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "script not found" in result.stderr

    def test_sock_rejected(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "noop.py", "pass\n")
        result = subprocess.run(
            CLI + [str(script), "--notification-type=sock"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "deferred to v2" in result.stderr

    def test_timeout_cleans_up_subprocess(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "sleep.py", SLEEP_FOREVER_FIXTURE)
        start = time.monotonic()
        result = subprocess.run(
            CLI + [str(script), "--timeout=1"],
            capture_output=True, text=True, timeout=20,
        )
        elapsed = time.monotonic() - start
        # Should give up well before the outer 20s timeout.
        assert elapsed < 15
        assert result.returncode != 0


# ----------------------------------------------------------------------------
# Run mode — remote
# ----------------------------------------------------------------------------


class TestNoNotifyMode:
    def test_done_only_script_runs_to_clean_exit(self, tmp_path: Path) -> None:
        """--no-notify: no listener, no AUTOMATION_NOTIFICATION_*, script calls done()."""
        script = _write_fixture(
            tmp_path,
            "done_only.py",
            '''
            import os, sys
            from webauto._notify import _notify_ctx
            from webauto._entry import _no_channel_emitter
            # Simulate what init() would do for no-channel mode.
            _notify_ctx.set(_no_channel_emitter)
            assert "AUTOMATION_NOTIFICATION_TYPE" not in os.environ
            assert "AUTOMATION_NOTIFICATION_ENDPOINT" not in os.environ
            from webauto import done
            try:
                done()
            except SystemExit:
                pass
            except Exception as e:
                # done() raises _Done internally — match exception type name
                if type(e).__name__ != "_Done":
                    raise
            sys.exit(0)
            ''',
        )
        result = subprocess.run(
            CLI + [str(script), "--no-notify"],
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stderr

    def test_conflicts_with_notification_endpoint(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "noop.py", "pass\n")
        result = subprocess.run(
            CLI + [str(script), "--no-notify",
                   "--notification-endpoint", "http://127.0.0.1:9999/x"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr


class TestRemoteMode:
    def test_posts_to_external_endpoint(self, tmp_path: Path) -> None:
        # Stand up a sink that captures the POSTed body.
        captured: dict = {}

        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                captured["body"] = self.rfile.read(length)
                captured["ct"] = self.headers.get("Content-Type")
                self.send_response(204)
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), Sink)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            script = _write_fixture(tmp_path, "echo.py", ECHO_FIXTURE)
            result = subprocess.run(
                CLI + [
                    str(script),
                    "--notification-endpoint", f"http://127.0.0.1:{port}/sink",
                    "-a", "FOO=remote",
                ],
                capture_output=True, text=True, timeout=20,
            )
            assert result.returncode == 0, result.stderr
            # In remote mode, CLI's stdout is empty.
            assert result.stdout == ""
            assert captured["body"] is not None
            body = json.loads(captured["body"])
            assert body["got_foo"] == "remote"
        finally:
            server.shutdown()
            server.server_close()


# ----------------------------------------------------------------------------
# --log flag
# ----------------------------------------------------------------------------


# Fixture that:
#   - prints to stderr so we can verify tee-to-file
#   - reports its DEBUG env value into the notify payload
LOG_PROBE_FIXTURE = '''
    import json, os, sys, urllib.request
    print("hello-from-script-stderr", file=sys.stderr, flush=True)
    payload = {"got_debug": os.environ.get("DEBUG")}
    body = json.dumps(payload).encode()
    urllib.request.urlopen(urllib.request.Request(
        os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    ), timeout=5).read()
'''


class TestLogFlag:
    def test_log_writes_subprocess_stderr_to_file(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "probe.py", LOG_PROBE_FIXTURE)
        log = tmp_path / "run.log"
        result = subprocess.run(
            CLI + [str(script), "--log", str(log)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        # File exists and captured the subprocess's stderr line.
        assert log.exists()
        content = log.read_text()
        assert "hello-from-script-stderr" in content
        # Header line written.
        assert "webauto-cli" in content
        # And stderr was still tee'd to the CLI's own stderr too.
        assert "hello-from-script-stderr" in result.stderr

    def test_log_defaults_debug_to_1(self, tmp_path: Path) -> None:
        script = _write_fixture(tmp_path, "probe.py", LOG_PROBE_FIXTURE)
        log = tmp_path / "run.log"
        # Strip DEBUG from the caller env so we can prove the default kicks in.
        env = {k: v for k, v in os.environ.items() if k != "DEBUG"}
        result = subprocess.run(
            CLI + [str(script), "--log", str(log)],
            env=env,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert out["got_debug"] == "1"

    def test_log_respects_explicit_debug_value(self, tmp_path: Path) -> None:
        """If the caller already set DEBUG, --log must not clobber it."""
        script = _write_fixture(tmp_path, "probe.py", LOG_PROBE_FIXTURE)
        log = tmp_path / "run.log"
        env = {**os.environ, "DEBUG": "0"}
        result = subprocess.run(
            CLI + [str(script), "--log", str(log)],
            env=env,
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        # Caller's "0" wins — --log only defaults when unset.
        assert out["got_debug"] == "0"

    def test_log_appends_not_truncates(self, tmp_path: Path) -> None:
        """Pre-existing log content is preserved across runs."""
        script = _write_fixture(tmp_path, "probe.py", LOG_PROBE_FIXTURE)
        log = tmp_path / "run.log"
        log.write_text("PRE-EXISTING-LINE\n")
        result = subprocess.run(
            CLI + [str(script), "--log", str(log)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        content = log.read_text()
        assert content.startswith("PRE-EXISTING-LINE\n")
        assert "hello-from-script-stderr" in content


# ----------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------


class TestDoctor:
    def test_doctor_runs(self) -> None:
        # doctor should at least produce output and exit with a code (0 or 1).
        result = subprocess.run(
            CLI + ["doctor"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode in (0, 1)
        assert "Chrome binary" in result.stdout
        assert "Loopback port" in result.stdout


class TestBootstrapDryRun:
    def test_bootstrap_no_yes_prints_commands(self) -> None:
        result = subprocess.run(
            CLI + ["bootstrap"],
            capture_output=True, text=True, timeout=15,
        )
        # Should exit 0 (or 1 on unsupported platforms) and print "would run".
        if result.returncode == 0:
            assert "would run" in result.stdout
        else:
            # Unsupported platform — still graceful.
            assert "unsupported" in result.stderr or "manually" in result.stderr


class TestHelp:
    def test_dash_h(self) -> None:
        result = subprocess.run(
            CLI + ["-h"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0
        assert "automation" in result.stdout.lower()
        assert "doctor" in result.stdout
        assert "bootstrap" in result.stdout


# ----------------------------------------------------------------------------
# `secrets` subcommand + encrypted --env auto-detect
# ----------------------------------------------------------------------------


class TestSecretsSubcommand:
    def test_keygen_encrypt_decrypt_roundtrip(self, tmp_path: Path) -> None:
        key = tmp_path / "private_key"
        plain = tmp_path / "plain.env"
        enc = tmp_path / "enc.env"

        plain.write_text("URL=https://x\nPASSWORD=hunter2\n")

        env = {**os.environ, "WEBAUTO_KEY_PATH": str(key)}

        r = subprocess.run(
            CLI + ["secrets", "keygen"], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, r.stderr
        assert key.exists()

        r = subprocess.run(
            CLI + ["secrets", "encrypt", str(plain), "--out", str(enc)], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, r.stderr
        assert enc.exists()
        assert b"hunter2" not in enc.read_bytes()

        r = subprocess.run(
            CLI + ["secrets", "decrypt", str(enc)], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, r.stderr
        assert "PASSWORD=hunter2" in r.stdout

    def test_keygen_refuses_overwrite_non_tty(self, tmp_path: Path) -> None:
        """When stdin is not a TTY (the case for subprocess tests),
        keygen refuses without --force or --if-missing."""
        key = tmp_path / "private_key"
        env = {**os.environ, "WEBAUTO_KEY_PATH": str(key)}
        subprocess.run(CLI + ["secrets", "keygen"], env=env, check=True, timeout=10)
        r = subprocess.run(
            CLI + ["secrets", "keygen"], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode != 0
        assert "already exists" in r.stderr
        assert "non-interactive" in r.stderr

    def test_keygen_if_missing_is_idempotent(self, tmp_path: Path) -> None:
        """--if-missing: no-op when the key already exists, exit 0."""
        key = tmp_path / "private_key"
        env = {**os.environ, "WEBAUTO_KEY_PATH": str(key)}
        # First call generates.
        r1 = subprocess.run(
            CLI + ["secrets", "keygen", "--if-missing"], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r1.returncode == 0
        first_content = key.read_bytes()
        # Second call no-ops; content unchanged.
        r2 = subprocess.run(
            CLI + ["secrets", "keygen", "--if-missing"], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r2.returncode == 0
        assert "already exists" in r2.stderr
        assert key.read_bytes() == first_content

    def test_keygen_force_overwrites(self, tmp_path: Path) -> None:
        """--force overwrites silently (no prompt)."""
        key = tmp_path / "private_key"
        env = {**os.environ, "WEBAUTO_KEY_PATH": str(key)}
        subprocess.run(CLI + ["secrets", "keygen"], env=env, check=True, timeout=10)
        original = key.read_bytes()
        r = subprocess.run(
            CLI + ["secrets", "keygen", "--force"], env=env,
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0
        assert key.read_bytes() != original


class TestEncryptedEnvAutoDetect:
    def test_run_with_encrypted_env_file(self, tmp_path: Path) -> None:
        # Set up: keygen, write a plaintext env, encrypt it, then run a
        # fixture script that reads PASSWORD from env and POSTs it.
        key = tmp_path / "private_key"
        plain = tmp_path / "plain.env"
        enc = tmp_path / "auto_login.env"

        plain.write_text("PASSWORD=hunter2\n")
        env = {**os.environ, "WEBAUTO_KEY_PATH": str(key)}

        subprocess.run(CLI + ["secrets", "keygen"], env=env, check=True, timeout=10)
        subprocess.run(
            CLI + ["secrets", "encrypt", str(plain), "--out", str(enc)],
            env=env, check=True, timeout=10,
        )

        script = _write_fixture(
            tmp_path,
            "echo_pw.py",
            '''
            import json, os, urllib.request
            body = json.dumps({"got": os.environ.get("PASSWORD")}).encode()
            urllib.request.urlopen(urllib.request.Request(
                os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=5).read()
            ''',
        )

        r = subprocess.run(
            CLI + [str(script), "--env", str(enc)], env=env,
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["got"] == "hunter2"
