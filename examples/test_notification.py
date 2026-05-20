"""Self-contained end-to-end smoke test for schwab_auth.py via remote-mode notification.

What it does:
  1. Stands up a tiny HTTP server on 127.0.0.1:0 with a /callback endpoint.
  2. Spawns `webauto-cli examples/schwab_auth.py --notification-endpoint=<our URL>`.
  3. schwab_auth.py drives the browser through Schwab login and POSTs the
     captured redirect URL to /callback when it lands on the relay.
  4. This script prints the received JSON to stdout and exits 0.

Run:
    uv run examples/test_notification.py

Requires a populated ../.schwab.env (URL, USERNAME, PASSWORD, REDIRECT_URI).
Defaults to headless; set HEADLESS=0 for a visible browser window.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

CALLBACK_PATH = "/callback"
OVERALL_TIMEOUT_SECONDS = 600  # 10 min — generous; MFA can take a while


def main() -> int:
    examples_dir = Path(__file__).resolve().parent
    project_root = examples_dir.parent
    schwab_script = examples_dir / "schwab_auth.py"
    env_file = project_root / ".schwab.env"

    if not env_file.exists():
        print(f"[test] error: .schwab.env not found at {env_file}", file=sys.stderr)
        return 2
    if not schwab_script.exists():
        print(f"[test] error: schwab_auth.py not found at {schwab_script}", file=sys.stderr)
        return 2

    # --- 1. Stand up the receiving HTTP server.
    captured: dict = {"body": None, "content_type": None}
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return  # quiet

        def do_POST(self):  # noqa: N802 — http.server naming
            if self.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            captured["body"] = body
            captured["content_type"] = self.headers.get("Content-Type")
            received.set()
            self.send_response(204)
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    endpoint = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
    print(f"[test] listening on {endpoint}", file=sys.stderr)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # --- 2. Spawn webauto-cli in remote mode (POST goes to our endpoint).
    cmd = [
        "uv", "run", "webauto-cli",
        str(schwab_script),
        "--env", str(env_file),
        "--notification-endpoint", endpoint,
        "--timeout", str(OVERALL_TIMEOUT_SECONDS),
    ]
    print(f"[test] spawning: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=sys.stderr, stderr=sys.stderr)

    rc = 1
    try:
        # --- 3. Wait for either the POST to arrive or the subprocess to die.
        deadline = time.time() + OVERALL_TIMEOUT_SECONDS
        while time.time() < deadline:
            if received.is_set():
                break
            if proc.poll() is not None:
                break
            time.sleep(0.3)

        if received.is_set():
            # --- 4. Print captured payload to stdout.
            body: bytes = captured["body"] or b""
            ct: Optional[str] = captured["content_type"]
            print(
                f"\n[test] received {len(body)} bytes (Content-Type: {ct})",
                file=sys.stderr,
            )
            try:
                obj = json.loads(body.decode("utf-8"))
                print(json.dumps(obj, ensure_ascii=False, indent=2))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Not JSON — emit raw bytes.
                sys.stdout.buffer.write(body)
                sys.stdout.flush()
                print()
            rc = 0
        else:
            print(
                f"[test] subprocess exited (code={proc.returncode}) "
                f"without posting a notification",
                file=sys.stderr,
            )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.shutdown()
        server.server_close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
