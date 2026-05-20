"""webauto-cli — run an action script with notification capture.

Usage:
    webauto-cli SCRIPT [OPTIONS]         (run an action script)
    webauto-cli doctor                   (environment health check)
    webauto-cli bootstrap [--yes]        (install Chrome + warm chromedriver)

Dispatch: if the first positional is `doctor` or `bootstrap`, that
subcommand runs. Otherwise the positional is treated as a script path.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from typing import IO, Optional

# json is used only in _print_payload's JSON parsing — kept for clarity.

from webauto._notification_listener import (
    HttpNotificationListener,
    make_notification_listener,
)


RESERVED_SUBCOMMANDS = {"doctor", "bootstrap", "secrets"}


# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------


def _parse_kv(raw: str) -> tuple[str, str]:
    """Parse a `-a KEY=VALUE` argument. Empty keys / no `=` → ArgumentTypeError."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"-a expects KEY=VALUE, got {raw!r}"
        )
    key, _, value = raw.partition("=")
    if not key:
        raise argparse.ArgumentTypeError(
            f"-a got empty key in {raw!r}"
        )
    return key, value


def _build_run_parser(prog: str = "webauto-cli") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog,
        description="Run a webauto action script.",
    )
    p.add_argument("script", help="Path to the action-script Python file.")
    p.add_argument(
        "--notification-type",
        choices=["http", "sock"],
        default="http",
        help="Transport. Default: http. (sock deferred to v2.)",
    )
    p.add_argument(
        "--notification-endpoint",
        default=None,
        help=(
            "Where the script should POST. If omitted, CLI stands up its own "
            "local listener and prints the received payload."
        ),
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help=(
            "Don't stand up a listener and don't set AUTOMATION_NOTIFICATION_* "
            "env vars. The script must end with `done()` instead of `notify()`. "
            "Use this for code_relay-style flows where a separate backend has "
            "already captured the result."
        ),
    )
    p.add_argument(
        "--env",
        dest="env_file",
        default=None,
        metavar="FILE",
        help=(
            "Load KEY=VALUE pairs from FILE (dotenv format). Comments (#) "
            "and blank lines are ignored. Each `-a` for the same key fully "
            "overrides the file value."
        ),
    )
    p.add_argument(
        "-a",
        dest="env_pairs",
        action="append",
        type=_parse_kv,
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Env var passed to the script. Repeatable; last value wins for "
            "the same key. Overrides --env file values. Each value is a "
            "bare string — if you need a list, pass `KEY=v1,v2` and split "
            "in your script."
        ),
    )
    p.add_argument(
        "--state",
        default=None,
        help="Opaque correlation token. Default: random token in listener mode.",
    )
    p.add_argument(
        "--no-state",
        action="store_true",
        help="Skip the state token entirely.",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="Outer subprocess wall-time budget. Default: 600s.",
    )
    p.add_argument(
        "--output",
        choices=["json", "raw", "kv"],
        default="json",
        help="Listener-mode output format. Default: json.",
    )
    p.add_argument(
        "--log",
        dest="log_file",
        default=None,
        metavar="FILE",
        help=(
            "Append the script's stderr (and CLI status lines) to FILE. "
            "Output is still tee'd to the CLI's own stderr. Implies "
            "DEBUG=1 in the subprocess env unless DEBUG is already set "
            "to a falsey value."
        ),
    )
    return p


# ----------------------------------------------------------------------------
# Env construction
# ----------------------------------------------------------------------------


def _load_env_file(path: str) -> dict[str, str]:
    """Parse a dotenv-style file. Auto-detects Fernet-encrypted files.

    Plaintext format:
      - Lines starting with `#` are comments; blank lines ignored.
      - Surrounding single/double quotes on the value are stripped.
      - No variable interpolation, no multi-line values.

    Encrypted format:
      - A Fernet token (URL-safe base64, starts with `gAAAAA`).
      - Decrypted with the key at `~/.config/webauto-cli/private_key`
        (override via `WEBAUTO_KEY_PATH`).
      - The decrypted content is parsed as plaintext dotenv.
    """
    with open(path, "rb") as f:
        raw_bytes = f.read()

    from webauto._secrets import looks_encrypted, decrypt_file
    if looks_encrypted(raw_bytes):
        text = decrypt_file(path)  # decrypt_file uses the key resolver
    else:
        text = raw_bytes.decode("utf-8")

    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"{path}:{lineno}: malformed line "
                f"(expected KEY=VALUE): {raw!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"{path}:{lineno}: empty key")
        value = value.strip()
        # Strip surrounding quotes if matched.
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _apply_a_pairs(env: dict[str, str], pairs: list[tuple[str, str]]) -> None:
    """Apply `-a KEY=VALUE` pairs to env, last-wins for duplicate keys.

    Each `-a` is a bare string. If the user wants a list, they pass
    `-a KEY=v1,v2` and split in their script.
    """
    for k, v in pairs:
        env[k] = v


def _build_script_env(args: argparse.Namespace) -> dict[str, str]:
    """Compose the script's env: os.environ → --env file → -a pairs.

    Precedence (later wins):
      1. os.environ inherited from the CLI process.
      2. `--env FILE` (KEY=VALUE per line).
      3. `-a KEY=VALUE` (last-wins for repeated keys).
    """
    env = {**os.environ}
    if args.env_file is not None:
        env.update(_load_env_file(args.env_file))
    _apply_a_pairs(env, args.env_pairs)
    return env


# ----------------------------------------------------------------------------
# Subprocess lifecycle
# ----------------------------------------------------------------------------


def _spawn(script: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=None,
        stderr=subprocess.PIPE,
    )


def _tee_stderr(
    proc: subprocess.Popen,
    log_fp: Optional[IO[bytes]] = None,
) -> threading.Thread:
    """Forward subprocess stderr to ours, optionally mirroring to a log file."""

    def pump() -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            try:
                sys.stderr.buffer.write(line)
                sys.stderr.flush()
            except (ValueError, OSError):
                pass
            if log_fp is not None:
                try:
                    log_fp.write(line)
                    log_fp.flush()
                except (ValueError, OSError):
                    pass

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return t


def _open_log_file(path: str, args: argparse.Namespace) -> IO[bytes]:
    """Open the log file for append in binary mode and write a header.

    Caller owns closing the returned handle.
    """
    fp = open(path, "ab", buffering=0)
    header = (
        f"\n==== webauto-cli {datetime.datetime.now().isoformat(timespec='seconds')} "
        f"script={args.script} pid={os.getpid()} ====\n"
    ).encode("utf-8")
    try:
        fp.write(header)
    except OSError:
        pass
    return fp


def _apply_log_defaults(
    env: dict[str, str], args: argparse.Namespace
) -> None:
    """When --log is set, default DEBUG=1 in the subprocess env.

    Caller's existing DEBUG value (truthy or falsey) wins. If DEBUG is
    unset, we set it to "1" so the runner's _dlog() output reaches the
    log file.
    """
    if args.log_file is None:
        return
    if "DEBUG" not in env or env.get("DEBUG", "") == "":
        env["DEBUG"] = "1"


def _terminate(proc: subprocess.Popen, grace_seconds: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


# ----------------------------------------------------------------------------
# Output formatting (listener mode)
# ----------------------------------------------------------------------------


def _print_payload(body: bytes, content_type: Optional[str], fmt: str) -> None:
    if fmt == "raw":
        # If JSON with exactly one non-state scalar field, print just its value.
        # Otherwise dump JSON.
        decoded = _try_decode_json(body)
        if isinstance(decoded, dict):
            scalar_fields = {
                k: v for k, v in decoded.items()
                if k != "state" and isinstance(v, (str, int, float, bool))
            }
            if len(scalar_fields) == 1:
                print(next(iter(scalar_fields.values())))
                return
        # Fallback: dump as JSON if it's decodable, otherwise raw bytes.
        if decoded is not None:
            print(json.dumps(decoded, ensure_ascii=False))
        else:
            sys.stdout.buffer.write(body)
        return

    if fmt == "kv":
        decoded = _try_decode_json(body)
        if isinstance(decoded, dict):
            for k, v in decoded.items():
                if isinstance(v, (str, int, float, bool)):
                    print(f"{k}={v}")
                else:
                    print(f"{k}={json.dumps(v, ensure_ascii=False)}")
            return
        # Not a JSON object; print raw.
        sys.stdout.buffer.write(body)
        return

    # Default: json
    decoded = _try_decode_json(body)
    if decoded is not None:
        print(json.dumps(decoded, ensure_ascii=False))
    else:
        # Body wasn't JSON — print it verbatim.
        sys.stdout.buffer.write(body)


def _try_decode_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------------------------
# Run modes
# ----------------------------------------------------------------------------


def run_listener_mode(args: argparse.Namespace) -> int:
    """Stand up a local listener, spawn the script, print captured payload."""
    state: Optional[str] = None
    if not args.no_state:
        state = args.state or secrets.token_urlsafe(32)

    listener = make_notification_listener(args.notification_type)
    proc: Optional[subprocess.Popen] = None
    stderr_thread: Optional[threading.Thread] = None
    log_fp: Optional[IO[bytes]] = None
    rc = 0

    try:
        endpoint = listener.endpoint
        print(f"[webauto-cli] listener: {endpoint}", file=sys.stderr)

        env = _build_script_env(args)
        env["AUTOMATION_NOTIFICATION_TYPE"] = args.notification_type
        env["AUTOMATION_NOTIFICATION_ENDPOINT"] = endpoint
        if state is not None:
            env["AUTOMATION_STATE"] = state
        _apply_log_defaults(env, args)

        if args.log_file is not None:
            log_fp = _open_log_file(args.log_file, args)

        proc = _spawn(args.script, env)
        stderr_thread = _tee_stderr(proc, log_fp=log_fp)

        deadline = time.time() + args.timeout
        try:
            body, content_type = listener.wait(deadline=deadline)
        except TimeoutError as e:
            print(f"[webauto-cli] {e}", file=sys.stderr)
            return 1

        _print_payload(body, content_type, args.output)
        return 0
    except KeyboardInterrupt:
        rc = 130
        return rc
    finally:
        if proc is not None:
            _terminate(proc)
            if stderr_thread is not None:
                stderr_thread.join(timeout=1.0)
        try:
            listener.close()
        except Exception:
            pass
        if log_fp is not None:
            try:
                log_fp.close()
            except OSError:
                pass


def run_no_notify_mode(args: argparse.Namespace) -> int:
    """Spawn the script with NO notification channel.

    The script must call `done()` (or never need to send a payload). CLI
    waits for the subprocess to exit and mirrors its exit code.
    """
    env = _build_script_env(args)
    # Explicitly DO NOT set AUTOMATION_NOTIFICATION_*. The script's
    # init() reads None → installs the no-channel emitter that errors on
    # accidental notify() calls, but done() works.
    _apply_log_defaults(env, args)

    log_fp: Optional[IO[bytes]] = None
    if args.log_file is not None:
        log_fp = _open_log_file(args.log_file, args)

    proc = _spawn(args.script, env)
    stderr_thread = _tee_stderr(proc, log_fp=log_fp)

    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[webauto-cli] subprocess timed out after {args.timeout}s",
            file=sys.stderr,
        )
        _terminate(proc)
        return 1
    finally:
        stderr_thread.join(timeout=1.0)
        if log_fp is not None:
            try:
                log_fp.close()
            except OSError:
                pass

    return proc.returncode if proc.returncode is not None else 1


def run_remote_mode(args: argparse.Namespace) -> int:
    """Spawn the script with a caller-supplied endpoint; CLI just orchestrates."""
    env = _build_script_env(args)
    env["AUTOMATION_NOTIFICATION_TYPE"] = args.notification_type
    env["AUTOMATION_NOTIFICATION_ENDPOINT"] = args.notification_endpoint
    if args.state is not None:
        env["AUTOMATION_STATE"] = args.state
    _apply_log_defaults(env, args)

    log_fp: Optional[IO[bytes]] = None
    if args.log_file is not None:
        log_fp = _open_log_file(args.log_file, args)

    proc = _spawn(args.script, env)
    stderr_thread = _tee_stderr(proc, log_fp=log_fp)

    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[webauto-cli] subprocess timed out after {args.timeout}s",
            file=sys.stderr,
        )
        _terminate(proc)
        return 1
    finally:
        stderr_thread.join(timeout=1.0)
        if log_fp is not None:
            try:
                log_fp.close()
            except OSError:
                pass

    return proc.returncode if proc.returncode is not None else 1


# ----------------------------------------------------------------------------
# Subcommand dispatch
# ----------------------------------------------------------------------------


def _run_subcommand(name: str, argv: list[str]) -> int:
    if name == "doctor":
        from webauto.cli._doctor import doctor_main
        return doctor_main(argv)
    if name == "bootstrap":
        from webauto.cli._doctor import bootstrap_main
        return bootstrap_main(argv)
    if name == "secrets":
        from webauto.cli._secrets_cmd import secrets_main
        return secrets_main(argv)
    raise AssertionError(f"unreachable: unknown subcommand {name!r}")


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Handle bare --help / -h / --version on top-level.
    if not argv or argv[0] in ("-h", "--help"):
        _build_run_parser().print_help()
        print("\nReserved subcommands: doctor, bootstrap.")
        return 0

    # Dispatch to subcommand if first positional is reserved.
    if argv[0] in RESERVED_SUBCOMMANDS:
        return _run_subcommand(argv[0], argv[1:])

    # Run mode.
    args = _build_run_parser().parse_args(argv)

    if args.notification_type == "sock":
        print(
            "[webauto-cli] sock transport is deferred to v2 — http only in v1.",
            file=sys.stderr,
        )
        return 2

    if not os.path.exists(args.script):
        print(f"[webauto-cli] script not found: {args.script}", file=sys.stderr)
        return 2

    if args.env_file is not None and not os.path.exists(args.env_file):
        print(
            f"[webauto-cli] --env file not found: {args.env_file}",
            file=sys.stderr,
        )
        return 2

    # Validate the env file up-front so malformed content fails cleanly
    # before we spawn anything.
    if args.env_file is not None:
        try:
            _load_env_file(args.env_file)
        except (ValueError, OSError) as e:
            print(f"[webauto-cli] {e}", file=sys.stderr)
            return 2

    if args.no_notify:
        if args.notification_endpoint is not None:
            print(
                "[webauto-cli] --no-notify and --notification-endpoint are "
                "mutually exclusive.",
                file=sys.stderr,
            )
            return 2
        return run_no_notify_mode(args)
    if args.notification_endpoint is not None:
        return run_remote_mode(args)
    return run_listener_mode(args)


if __name__ == "__main__":
    sys.exit(main())
