# `webauto` — Standalone Browser-Automation Library + CLI Runner

**Date:** 2026-05-14
**Project root:** `/Users/weig/Projects/finance/webauto/`. This plan lives at `docs/plan/2026-05-14-webauto.md`.
**Sibling plan (consumer side):** `~/Projects/finance/schwab_cli/docs/superpowers/plans/2026-05-14-auto-login-script.md` — schwab_cli's `AutoLoginHandler` + listener + config schema. (Lives inside schwab_cli's auth-refactor worktree during dev: `~/Projects/finance/schwab_cli/.claude/worktrees/auth-refactor/docs/superpowers/plans/`.)
**Prior architecture reference:** `~/Projects/finance/schwab_cli/docs/superpowers/plans/2026-04-30-pluggable-auto-login.md` — the action-script vocabulary (`BaseLoginAction`, `PageAction`, step factories) that ports into this project.
**Status:** PLAN — initial dev happens directly inside this project root; graduates to its own git repo once the wire protocol stabilizes.

---

## Goal

Build a standalone, general-purpose webauto service. **Inputs:** a URL + an action script. **Output:** whatever JSON payload the action chose to report, delivered to the caller via a notification channel. No OAuth assumptions in the framework.

- **As a library**: `from webauto import init, BaseAction, PageAction, fill, click, ...` — users write action scripts (subclasses of `BaseAction`) that drive any web flow through a configurable browser backend (SeleniumBase UC by default; pluggable to plain Selenium / Playwright / Firefox later). The action declares (a) the steps to run per page and (b) when it's done + what payload to report.
- **As a CLI tool**: `webauto-cli path/to/action.py -a URL=... -a USERNAME=... --notification-type=http` — opens a listener, spawns the action script as a subprocess, prints the script's notification payload, exits. Also ships `webauto-cli doctor` and `webauto-cli bootstrap` for environment setup. Lets a human run + debug an automation script end-to-end without involving any specific caller.

The library defines a small vocabulary for "drive a browser to a page, do some steps, report a result"; the CLI is a reference implementation of the listener+spawn pattern. Both consume the wire protocol below.

**OAuth is one shape of "report a result"** — captured via `webauto.oauth.notify_redirect(redirect_uri=..., expected_state=...)`, a step factory the script drops into a trailing PageAction. It is **not** baked into the framework. schwab_cli is just one consumer.

Naming note: the **eventual repo** is `webauto`. The **Python package** is `webauto` (matching the repo). The **CLI entry point** is `webauto-cli`. Action scripts pull names from `webauto` (e.g. `from webauto import init, BaseAction`).

## Architectural overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                  webauto (this project)                   │
│                                                                       │
│  ┌───────────────────────────┐    ┌────────────────────────────┐    │
│  │ As a library:             │    │ As a CLI:                  │    │
│  │                           │    │                            │    │
│  │   from webauto │    │   webauto-cli           │    │
│  │     import init           │    │     action.py              │    │
│  │           BaseAction      │    │     --notification-type=http│   │
│  │           fill click ...  │    │     [--notification-endpoint│   │
│  │                           │    │                          ...]│   │
│  │   ── used inside the      │    │     -a KEY=VALUE  ...       │   │
│  │      user's action.py     │    │     [--state ...]           │   │
│  │      script               │    │     [--timeout 600]         │   │
│  │                           │    │   ── stands up listener    │    │
│  │                           │    │      spawns action.py as   │    │
│  │                           │    │      subprocess with env   │    │
│  │                           │    │      vars, waits, prints   │    │
│  │                           │    │      JSON, exits           │    │
│  └───────────────────────────┘    └────────────────────────────┘    │
│                  │                            │                       │
│                  └────────────┬───────────────┘                       │
│                               │                                       │
│                  Shared internals:                                    │
│                   • action-script runner (port of 2026-04-30 plan)    │
│                   • BrowserHandle Protocol + impls                    │
│                   • notification client (http only in v1; sock = v2)  │
│                   • notification listener (http only in v1; sock = v2)│
└─────────────────────────────────────────────────────────────────────┘
                               │
                               │ AUTOMATION_* env vars + JSON POST
                               ▼
                  Any caller that speaks the wire protocol —
                  schwab_cli today, anything else tomorrow.
```

## The wire protocol

Caller-agnostic. Anything that can set env vars, listen on a port or unix socket, and parse JSON can drive an action script.

**Env vars (caller → script, set by whoever spawns):**

The framework owns only the notification-related vars. **Everything else is script-defined** — including the URL to navigate to. The caller passes whatever the script needs via `-a KEY=VALUE` (CLI) or `env=...` (library).

| Name | Value | Owner |
|---|---|---|
| `AUTOMATION_NOTIFICATION_TYPE` | `"http"` (v1 only — `"sock"` deferred to v2) | framework |
| `AUTOMATION_NOTIFICATION_ENDPOINT` | full URL (http) | framework |
| `AUTOMATION_STATE` | opaque correlation token; framework echoes it back in the notification. Optional. | framework |
| `HEADLESS` | headless by default; set `HEADLESS=0`/`false`/`no`/`off` for a visible window | framework (passthrough) |
| `DEBUG` | same — enables verbose logging | framework (passthrough) |
| `CHROME_VERSION` | major version (e.g. `124`) to pin chromedriver against. If unset, framework auto-detects from the installed Chrome binary. | framework |
| *anything the script needs* (e.g. `URL`, `USERNAME`, `PASSWORD`, `SCHWAB_REDIRECT_URI`) | whatever the caller passed via `-a` | script-defined |

Notes:
- There is **no** `AUTOMATION_URL`. The script picks its own env-var name and reads it (e.g. `os.environ["URL"]`).
- There is **no** `AUTOMATION_REDIRECT_URI`. Completion is signaled by the action itself calling `notify(payload)` from inside a step. OAuth-flavored scripts read their `redirect_uri` from their own env and use it in a trailing PageAction.
- Convention: most scripts use `URL` as the env-var name for the page to drive. Not enforced.

**Script output (one byte payload to the endpoint, exits 0 after):**

The framework sends `str | bytes` to the wire. **No body inspection, no auto-merging of fields.** The script is responsible for whatever encoding it wants. Helpers stack on top:

```python
notify({"price": "187.42"})                  # default: JSON → utf-8 → wire
                                             # (http Content-Type: application/json)
notify(b"<raw PDF bytes>",                   # explicit serializer = passthrough
       serializer=lambda x: x,
       content_type="application/pdf")
notify(my_obj,                               # plug in any encoder
       serializer=msgpack.packb,
       content_type="application/msgpack")
```

`AUTOMATION_STATE` is still passed in (as an env var) but the framework does NOT merge it into the body. If the caller wants the script to echo state in the response, the script does so explicitly:

```python
notify({
    "price": price,
    "state": os.environ.get("AUTOMATION_STATE"),
})
```

Correlation is via the **endpoint URL token** (e.g. `/notify/a1b2c3d4`); the listener validates that path token, not body fields. Body content is opaque to the framework.

Common payload shapes (totally up to the script):

```jsonc
// OAuth-flavored action (script used oauth.parse_redirect + notify)
{"kind": "code", "code": "C0.b2c1..."}
{"kind": "error", "error": "access_denied", "error_description": "..."}

// Scraping action
{"price": "187.42", "currency": "USD", "ts": "2026-05-14T18:00:00Z"}

// Binary download — straight bytes, no JSON envelope
<raw PDF bytes>
```

If `AUTOMATION_STATE` was set, the framework adds a `state` field to the outgoing payload (overwriting any `state` the action set, to prevent forgery). If it wasn't set, no `state` field is added.

**Operational failure (script can't capture anything):** write a message to stderr, exit non-zero. The caller treats this as a script failure. Don't POST a payload for operational failures — a structured error is something the action explicitly chose to report (e.g. `{"error": "captcha_failed"}`), not a stack trace.

## Decisions

**B1 — Two entry surfaces.** Library (`init(...)`) and CLI (`webauto-cli`). Both invoke the same underlying machinery; the CLI is just a thin shell that mirrors what schwab_cli's `AutoLoginHandler` does.

**B2 — Subprocess model in the CLI.** The CLI runner spawns the user-provided `--script path/to/action.py` as a subprocess via `python -m`-style invocation. Inherits its own env, sets the `AUTOMATION_*` vars, captures stderr to schwab_cli's stderr in real time, awaits the POST. Same lifecycle discipline as schwab_cli's handler (SIGTERM → 5s → SIGKILL → close listener on every exit path).

**B3 — Library entry: `init(url, notification, state, action)`.** Action scripts use it as the bottom of their `if __name__ == "__main__":` block. The script reads `url` from its own env (typically `os.environ["URL"]`) and passes it into `init`. `init` handles: bind `notify` to the configured endpoint, launch browser, navigate to `url`, run the action list, catch the `_Done` raised by `notify(payload)`, exit. The framework prescribes neither the URL env-var name nor any OAuth knowledge.

**B4 — Notification transport: HTTP only in v1; sock deferred to v2.** HTTP gives us framing for free (Content-Length, request boundaries). A raw unix socket stream would need an additional framing protocol (length-prefix, EOF-delim, etc.) — we haven't designed that, and getting it wrong silently corrupts payloads. Defer.

The transport seam is preserved: `_post_notification(notification, data, content_type=...)` already branches on `notification["type"]`, and the listener factory `make_notification_listener(transport_type)` already takes a type. v1 ships only the `"http"` branch (works on `http.server` from stdlib, no extra deps); the `"sock"` branch raises `NotImplementedError("sock transport deferred to v2 — see open questions")`.

If the user passes `--notification-type=sock` to the CLI in v1, the CLI rejects it at arg-parse time with a clear "not yet implemented" message that mentions the design issue.

**B5 — Browser backend: hardcoded to SeleniumBase UC in v1.** The architecture allows pluggable backends (the `BrowserHandle` Protocol), but only one impl ships in v1. Switching to plain Selenium + undetected-chromedriver or Playwright requires writing another `BrowserHandle` impl (~50 lines per backend). Documented seam; no scope creep here.

**B6 — Credential management is out of scope.** Action scripts read credentials from wherever they want — script-side env vars, a sibling dotfile, keychain, anything. `webauto` itself never touches passwords. The user's action script knows it's logging into Schwab and pulls `SCHWAB_USERNAME` / `SCHWAB_PASSWORD` (or `MY_BROKER_PASSWORD`, whatever) from its own env. The names are caller-defined, not library-defined.

**B7 — No `record` mode in v1.** Future feature: `webauto-cli record <url> --out action.py` opens a visible browser, records user interactions, emits a draft `BaseLoginAction` subclass. Conceptually similar to `playwright codegen` or Selenium IDE. Marked low priority by the user; deferred.

**B8 — Hot-swap: every CLI run re-reads the script.** No module caching to worry about. Edit the action list, re-run `webauto-cli`, changes pick up.

**B9 — Standalone repo, eventually.** The plan today lives in this repo (`docs/plan/`). Once the wire protocol stabilizes (i.e., at least one real consumer ships against it), `webauto` graduates to its own repo / PyPI release. The split is mechanically trivial because the project shares no source files with any caller — only the wire protocol document.

**B10 — Completion signal: `notify(data, *, serializer=None, content_type=None)`.** Single function, configurable serialization. Mechanics:
- `init()` binds `notify` to the configured endpoint via a `ContextVar` before kicking off the runner.
- Default serializer: `lambda x: json.dumps(x, default=str).encode("utf-8")`. Default content-type for http: `application/json`.
- Override: `notify(b"raw", serializer=lambda x: x, content_type="application/pdf")` keeps full control of the wire payload. Serializer must return `bytes` or `TypeError`.
- After the bytes are sent, `notify` raises an internal `_Done` sentinel.
- The runner catches `_Done`, aborts remaining steps, closes the browser, exits 0.
- Calling `notify` outside an `init()` context raises `RuntimeError`.

State / correlation:
- Framework does NOT inject `AUTOMATION_STATE` into the body — that conflated transport with structured data and didn't work for binary payloads.
- Correlation is via the endpoint URL token (the 8-hex token in `/notify/<token>` — set by the listener and read by the script from `AUTOMATION_NOTIFICATION_ENDPOINT`).
- If a script wants to echo state in its JSON body, it does so explicitly: `notify({"data": ..., "state": os.environ["AUTOMATION_STATE"]})`.

This is the *only* completion mechanism — there is no polled `result()` method. The action chooses when (and from which step) to send. For OAuth, a trailing PageAction matches the redirect URL and runs `oauth.notify_redirect()`, which reads `handle.current_url`, parses query params, and calls `notify(parsed)` (default JSON path).

**B11 — `oauth` submodule, not OAuth in core.** `webauto.oauth` ships with `parse_redirect(handle, redirect_uri, expected_state) -> dict | None` (pure function) and `notify_redirect(redirect_uri=..., expected_state=...)` (step factory that reads the URL, parses, and calls `notify`). Importing is opt-in. Action scripts that don't do OAuth never touch it.

**B13 — Params dataclass + `get_parameter` for late-bound values.** Action scripts declare their inputs as a `@dataclass` and reference values by name through `self.get_parameter("USERNAME")`, which returns a zero-arg lambda. Mechanics:

1. Subclasses set `Params: ClassVar[type] = MyDataclass` on the action.
2. `BaseAction.__init__` (auto-called by `init()`) reads `os.environ` for each field of `Params` and constructs the dataclass instance, stored as `self.params`. Field-type → env-read mapping:
   - `str` → `os.environ[name]` (required; raises `KeyError` on startup if missing).
   - `Optional[str]` / `str | None` (or any with default) → `os.environ.get(name, default)`.
   - Multi-values: NOT framework-supported. Declare the field as `str` and split it yourself (e.g. `params.SYMBOLS.split(",")`).
   - `int` / `bool` → parse from string with clear failure messages.
3. `self.get_parameter("NAME") -> Callable[[], Any]` returns `lambda: getattr(self.params, "NAME")`. Raises `AttributeError` at definition time if the name doesn't match a declared field — fail-fast.
4. All step factories that take a "value" argument accept `str | Callable[[], str]`. Steps resolve the callable at execution time (so the value is read from `self.params` *when the step runs*, not when the action class is constructed).
5. Because `get_parameter` is an instance method, action lists must be built in `__init__` (not as a class attribute) when they reference parameters. Plain class-attribute `actions = [...]` still works for actions with no parameter substitution.

This eliminates module-scope `USERNAME = os.environ["USERNAME"]` boilerplate and keeps the action self-contained: one class, one dataclass, no global state.

**B12 — chromedriver / Chrome version handling.** chromedriver gets auto-downloaded by SeleniumBase, but it must match the installed Chrome's major version or every launch fails. The framework:
1. If `CHROME_VERSION` env var is set, pin chromedriver to that major version.
2. Otherwise, detect the installed Chrome version at handle-construct time and pin to that.
3. If detection fails (Chrome not installed / unparseable output), raise a clear error pointing the user to `webauto-cli doctor`.
Chrome **binary** installation is NOT automatic — `webauto-cli doctor` prints the right install command for the OS; `webauto-cli bootstrap --yes` runs it.

## Project layout

```
webauto/
├── README.md                                ← user-facing docs
├── pyproject.toml                           ← own project, own venv
├── webauto/
│   ├── __init__.py                          ← public API exports (init + types + factories)
│   ├── _types.py                            ← BaseAction, PageAction, BrowserHandle Protocol
│   ├── _factories.py                        ← fill / click / check / wait_* / sleep / screenshot / custom
│   ├── _runner.py                           ← walks list[PageAction], catches _Done from notify()
│   ├── _seleniumbase_handle.py              ← BrowserHandle impl for SeleniumBase UC
│   ├── _notification_client.py              ← _post_notification (http + sock)
│   ├── _notification_listener.py            ← HttpNotificationListener + SockNotificationListener
│   ├── oauth.py                             ← parse_redirect + notify_redirect step (opt-in)
│   └── cli/
│       ├── __init__.py
│       └── __main__.py                      ← webauto-cli entry point
├── examples/
│   ├── schwab_login.py                      ← OAuth example — uses oauth.notify_redirect in a step
│   └── scrape_quote.py                      ← non-OAuth example — drives a page, returns a dict
└── tests/
    ├── test_factories.py
    ├── test_runner.py
    ├── test_notification_client.py
    ├── test_notification_listener.py
    ├── test_oauth.py
    └── test_cli.py
```

## Public API surface

Stable contract for action scripts:

```python
from webauto import (
    # Top-level entry — used at script bottom
    init,

    # Completion signal — called from a step or callback
    notify,               # notify(data) → serializes (JSON by default) + sends + ends the run

    # Types
    BaseAction,
    PageAction,
    BrowserHandle,        # for type hints inside `custom` callbacks

    # URL matcher factories — return Callable[[str], bool]
    any_url,              # any_url()              → matches every URL
    contains,             # contains("/foo")       → substring
    startswith,           # startswith("https://") → prefix
    equals,               # equals("https://x/y")  → exact equality
    regex,                # regex(r"/q/[A-Z]+")    → re.search-based

    # Step factories (11)
    fill, click,
    check, uncheck,       # strict — fail-loud on multi-match
    check_all,            # batch — ticks every match
    wait_visible, wait_url_contains, wait_page_change,
    sleep, screenshot,
    custom,
)

# Opt-in OAuth helpers
from webauto.oauth import parse_redirect, notify_redirect
```

19 core names + 2 optional OAuth names.

**`PageAction.url`** is a predicate — `Callable[[str], bool]`. The runner passes `handle.current_url` to it and runs the page's steps when it returns True.

For the 90% case (substring match) you don't need to spell out the predicate. `PageAction` accepts these mutually-exclusive shorthand kwargs and desugars to a predicate internally:

| Shorthand | Desugars to |
|---|---|
| `url_contains="X"` | `url=contains("X")` |
| `url_equals="X"` | `url=equals("X")` |
| `url_startswith="X"` | `url=startswith("X")` |
| `url_regex=r"X"` | `url=regex(r"X")` |
| `url_any=True` | `url=any_url()` |

`PageAction.__init__` validates that exactly one of `{url, url_contains, url_equals, url_startswith, url_regex, url_any}` is set; multiple → `TypeError`. Each shorthand also accepts a `Callable[[], str]` for late-binding (e.g. `url_contains=self.get_parameter("REDIRECT_URI")`).

Matcher factories (for the explicit `url=` form when shorthand isn't enough):

```python
any_url()                       # always True
contains("/login")              # substring match
contains(p("REDIRECT_URI"))     # substring against a late-bound lambda value
startswith("https://relay/")    # prefix match
equals("https://x/y")           # exact equality
regex(r"/quote/[A-Z]+")         # re.search against the URL
```

Each matcher accepts either a literal `str` or a `Callable[[], str]` (consistent with step factories) for its anchor value, so `contains(action.get_parameter("X"))` works.

Catch-all watcher PageActions use `url=any_url()` — runs on every URL transition. Useful for global error sniffers and redirect-URL watchers.

`BaseAction` shape:

```python
class BaseAction:
    Params: ClassVar[type] | None = None    # optional @dataclass declaring inputs
    actions: list[PageAction]               # the per-page step lists
    timeout_seconds: float = 300.0          # inner timeout: budget for the
                                             # whole action to reach completion.
                                             # Override per-subclass.

    def __init__(self, params=None):
        # Auto-build params from os.environ if Params is declared.
        if params is None and self.Params is not None:
            params = build_params_from_env(self.Params)
        self.params = params

    def get_parameter(self, name: str) -> Callable[[], Any]:
        """Return a zero-arg lambda that pulls `name` from self.params at call time."""
        if name not in {f.name for f in fields(self.Params)}:
            raise AttributeError(f"{self.Params.__name__} has no field {name!r}")
        return lambda: getattr(self.params, name)
```

No `result()` method — completion is signaled imperatively via `notify(payload)`. The runner walks `actions` until a step calls `notify`, then unwinds.

Subclasses can declare their own budget — slow flows like OAuth + MFA might want 600s:

```python
class SchwabLogin(BaseAction):
    timeout_seconds = 600                    # MFA prompts on the phone take time
    actions = [
        PageAction(url_contains="/login", steps=[...]),
        PageAction(url_contains="/mfa",   steps=[...]),
        # trailing page: when we hit the redirect URL, parse + notify
        PageAction(url_contains="relay.example.com/callback", steps=[
            notify_redirect(expected_state=...),
        ]),
    ]
```

`notify` semantics:

```python
def notify(
    data: Any,
    *,
    serializer: Callable[[Any], bytes] | None = None,
    content_type: str | None = None,
) -> NoReturn:
    """Serialize `data`, send it to the configured endpoint, and end the run.

    Default behavior: `data` is JSON-encoded (json.dumps(data, default=str)
    → utf-8) and sent with Content-Type: application/json.

    Override the serializer to keep full control of the bytes that hit the wire:

        notify({"price": "187.42"})                       # default: JSON
        notify(b"<raw PDF bytes>",
               serializer=lambda x: x,                    # passthrough
               content_type="application/pdf")
        notify(my_obj,
               serializer=msgpack.packb,
               content_type="application/msgpack")

    Side-effects:
      1. Applies `serializer` to `data` (or json.dumps + utf-8 if None).
      2. Sends the resulting bytes to notification["endpoint"] over the
         configured transport (http POST body, or sock stream).
      3. For http: sets Content-Type — explicit `content_type` wins;
         otherwise `application/json` if using the default serializer,
         else `application/octet-stream`.
      4. Raises webauto._Done — caught by init(); the runner
         aborts, the browser closes, and the script exits 0.

    Calling notify() outside an init() context raises RuntimeError.
    Calling notify() with a serializer that returns non-bytes raises TypeError.
    """
```

`init` signature:

```python
def init(
    *,
    url: str,
    notification: dict,        # {"type": "http" | "sock", "endpoint": str}
    state: str | None,         # if set, framework adds it to the outgoing payload
    action: BaseAction,
    timeout_seconds: float | None = None,    # None → use action.timeout_seconds
) -> None: ...
```

`init` does:
1. Bind `notify` to the configured `notification` endpoint + `state` via a `ContextVar`.
2. Launch the configured browser (SeleniumBase UC in v1).
3. Honor `HEADLESS` env.
4. Navigate to `url`.
5. Walk `action.actions` via the runner: per-page URL match → run steps. A step calls `notify(payload)` somewhere → framework POSTs (with `state` merged in) → raises `_Done`.
6. Runner catches `_Done`, aborts remaining steps in the current PageAction, exits the walk.
7. Close the browser.
8. Exit 0.

Failures (any uncaught exception, or timeout): log to stderr, exit non-zero, no POST. A structured error (e.g. `notify({"error": "captcha_failed"})`) is something the action explicitly chose to send, not an exception escape hatch. If the action never calls `notify`, the inner timeout fires and the script exits non-zero.

## CLI runner: `webauto-cli`

```
Usage:
  webauto-cli SCRIPT [OPTIONS]         (run an action script)
  webauto-cli doctor                   (environment health check)
  webauto-cli bootstrap [--yes]        (install Chrome + warm chromedriver)

Dispatch rule: if the first positional is one of the reserved keywords
(`doctor`, `bootstrap`), that subcommand runs. Otherwise the positional
is treated as a script path.

Positional (run mode):
  SCRIPT                       Path to the action-script Python file
                               (must call init(...) when run as __main__)

Notification:
  --notification-type {http}   Transport. Default: http
                               (sock is reserved for v2; rejected at parse time
                                in v1 with a "not yet implemented" message.)
  --notification-endpoint ENDPOINT
                               Where the script should POST its result.
                               For http: a full URL (http://host:port/path).
                               For sock: a filesystem path.
                               If omitted, the CLI stands up its OWN
                               local listener and prints the received
                               payload to stdout (debug / standalone mode).
                               If provided, the script POSTs to that
                               endpoint and the CLI just orchestrates
                               execution.

Script env:
  --env FILE                   Load KEY=VALUE pairs from a dotenv-style file.
                               Comments (# ...) and blank lines ignored;
                               surrounding quotes stripped. Validated up-front;
                               malformed file → exit 2 with a clear message.
  -a KEY=VALUE                 Pass an env var into the script's process.
                               Repeatable; last-wins for duplicate keys.
                               `-a` for a key fully overrides any value
                               supplied via --env for the same key.
                               Each value is a bare string. If you need a
                               list, pass `KEY=v1,v2` and split in the script.

                               Precedence (later wins):
                                 1. os.environ inherited from the CLI process.
                                 2. --env FILE
                                 3. -a KEY=VALUE pairs

                               Example:
                                 -a URL=https://example.com/login
                                 -a USERNAME=alice
                                 -a PASSWORD_PATH=/path/to/secret

Correlation:
  --state STATE                Opaque correlation token. CLI sets it as
                               AUTOMATION_STATE, validates it on incoming
                               notifications (listener mode), and echoes
                               it into the printed payload.
                               Default in listener mode: random
                               secrets.token_urlsafe(32).
                               Default in remote mode: not set.
  --no-state                   Skip the state token entirely.

Lifecycle:
  --timeout SECONDS            Outer subprocess wall-time budget — CLI safety
                               net only. The action itself has its own inner
                               timeout (BaseAction.timeout_seconds) which fires
                               first and produces a structured error. Set this
                               above the action's inner budget. Default: 600
  --output {json|raw|kv}       How to print the result (listener mode only).
                               Default: json
                               raw: print the single scalar payload field;
                                    fall back to json if not a single scalar.
                               kv:  key=value lines.
  -h, --help                   Show this help
```

Two operating modes, distinguished by whether `--notification-endpoint` is supplied:

**Listener mode (default — for debugging / standalone runs):**
1. Validate args; resolve `SCRIPT` to an absolute path; check it exists.
2. Generate state if `--no-state` not set and `--state` not provided.
3. Stand up the chosen listener (`HttpNotificationListener()` or `SockNotificationListener()`). Capture its `endpoint`.
4. Print to stderr: a banner like `[webauto-cli] listener: http://127.0.0.1:38291/notify/a1b2c3d4`.
5. Spawn:
   ```python
   env = {**os.environ}
   for k, v in parsed_a_pairs:
       env[k] = v
   env["AUTOMATION_NOTIFICATION_TYPE"]     = ntype
   env["AUTOMATION_NOTIFICATION_ENDPOINT"] = listener.endpoint
   if state is not None:
       env["AUTOMATION_STATE"]             = state
   subprocess.Popen([sys.executable, str(script_path)], env=env,
                    stdin=DEVNULL, stdout=None, stderr=PIPE)
   ```
   Inner thread tees stderr to terminal.
6. `listener.wait(expected_state=state, deadline=time.time()+timeout, cancel=None)`.
7. On success: print payload to stdout per `--output`. Terminate subprocess (should already be exiting). Close listener. Exit 0.
8. On failure (timeout / script exit without notify / state mismatch): print error to stderr. SIGTERM → SIGKILL → close listener. Exit non-zero.

**Remote mode (when `--notification-endpoint` is supplied):**
1. Validate args.
2. Spawn the script with `AUTOMATION_NOTIFICATION_ENDPOINT` = the provided URL (no local listener).
3. Tee stderr to terminal. Wait for subprocess to exit (or timeout).
4. CLI exit code mirrors subprocess exit code. CLI doesn't see the payload — it went straight to the remote endpoint.
5. Same SIGTERM → SIGKILL cleanup on timeout / abort.

Cleanup is unconditional via `finally:` in both modes.

## Tasks

### Task A — Project skeleton + types + factories

**Goal:** a working library with no browser backend yet — pure types and step factory shells.

**Files:**
- `pyproject.toml` (own deps: `seleniumbase`, no listed test deps yet)
- `webauto/__init__.py` (public re-exports)
- `webauto/_types.py`
- `webauto/_factories.py`
- `tests/test_factories.py`

- [ ] Port `PageAction`, `BrowserHandle` Protocol from `2026-04-30-pluggable-auto-login.md` §Task 1.
- [ ] `PageAction.url: Callable[[str], bool]` — a predicate. The runner passes `handle.current_url` and runs steps when it returns True.
- [ ] `PageAction.order: int = 100` — priority among matching PageActions. **Lower wins.** Convention:
      - Specific pages (login, terms, accounts): leave at default `100`.
      - Catch-all watchers (`url_any=True`): set `order=200` (or higher) so they fire only after specifics have skipped.
      - Hot-path overrides (e.g. an error sniffer that should fire before everything): set `order=10`.
- [ ] `PageAction.__init__` accepts these mutually-exclusive shorthand kwargs and desugars to a predicate:
      `url_contains=` → `contains(...)`,
      `url_equals=` → `equals(...)`,
      `url_startswith=` → `startswith(...)`,
      `url_regex=` → `regex(...)`,
      `url_any=True` → `any_url()`.
      Multiple set at once → `TypeError("PageAction accepts exactly one URL matcher")`. None set → `TypeError("PageAction requires a url matcher")`. Each shorthand value accepts `str | Callable[[], str]` for late binding.
- [ ] Define matcher factories in `_matchers.py`:
      ```python
      UrlMatcher = Callable[[str], bool]
      StrOrLazy = str | Callable[[], str]

      def _resolve(s: StrOrLazy) -> str:
          return s() if callable(s) else s

      def any_url() -> UrlMatcher:
          return lambda _url: True

      def contains(s: StrOrLazy) -> UrlMatcher:
          return lambda url: _resolve(s) in url

      def startswith(s: StrOrLazy) -> UrlMatcher:
          return lambda url: url.startswith(_resolve(s))

      def equals(s: StrOrLazy) -> UrlMatcher:
          return lambda url: url == _resolve(s)

      def regex(pattern: str) -> UrlMatcher:
          compiled = re.compile(pattern)
          return lambda url: bool(compiled.search(url))
      ```
- [ ] Unit tests for each matcher:
      - `any_url()(<any url>)` → True.
      - `contains("/x")("https://h/x/y")` → True; `contains("/z")(...)` → False.
      - `contains(lambda: "/x")(...)` → True (callable anchor).
      - `startswith` / `equals` / `regex` — round-trip examples.
- [ ] Define `BaseAction` (renamed from `BaseLoginAction`) with:
      - `Params: ClassVar[type] | None = None` — optional `@dataclass` declaring inputs.
      - `actions: list[PageAction]` — class attribute OR built in `__init__`.
      - `timeout_seconds: float = 300.0` — class attribute.
      - `__init__(self, params=None)` — auto-builds `self.params` from env via `build_params_from_env(cls.Params)` if Params is declared and `params` is not passed.
      - `get_parameter(name) -> Callable[[], Any]` — returns a zero-arg lambda; raises `AttributeError` at definition time if name doesn't match a declared field.
      - No `result()` method. Completion is via `notify(payload)`.
- [ ] Implement `build_params_from_env(cls)` in `_params.py`:
      - For each `field` in `dataclasses.fields(cls)`:
        - `field.type == "str"` (no default) → `os.environ[field.name]`; missing → `KeyError` with clear msg.
        - field has default → `os.environ.get(field.name, field.default)`.
        - `field.type == "int"` → `int(os.environ[field.name])` with clear `ValueError` on bad input.
        - `field.type == "bool"` → truthy parse: `1`/`true`/`yes`/`on` → True, else False.
        - `field.type == "float"` → `float(os.environ[field.name])`.
        - `Optional[T]` / `T | None` → pick the non-None member; same coercion.
        - Other types → `TypeError("Params field {name} has unsupported type {t}")`.
      - No list/multi-value support. Users that need a list declare `str` and split themselves.
- [ ] Port the 11 step factories from prior plan §Task 2 — pure callables returning `(handle, params) -> None`. **Each factory that takes a value argument (e.g. `fill(selector, value)`) accepts `str | Callable[[], str]`**. The step impl resolves callables at execution time via `value() if callable(value) else value`. No browser dep at this layer.
- [ ] Unit test value-resolution: `fill("#id", lambda: "alice")` against a `MockBrowserHandle` → handle receives `"alice"` at step-execution time (not at factory-construction time).
- [ ] Unit tests for `build_params_from_env`:
      - Required str field present → populated; missing → KeyError.
      - Optional field (with default) → default used when env missing.
      - `int` / `bool` / `float` parse correctness.
      - Unsupported field type → TypeError at startup.
- [ ] Define the `_Done` internal sentinel exception in `_types.py` (not exported publicly — `notify` raises it; `init` catches it). No payload carried — by the time it's raised, the emitter has already sent.
- [ ] Define `notify(data, *, serializer=None, content_type=None)` module-level function:
      ```python
      _DEFAULT_SERIALIZER = lambda obj: json.dumps(obj, default=str).encode("utf-8")

      def notify(data, *, serializer=None, content_type=None):
          try:
              emitter = _notify_ctx.get()
          except LookupError:
              raise RuntimeError("notify() called outside an init() context")
          if serializer is None:
              body = _DEFAULT_SERIALIZER(data)
              ct = content_type or "application/json"
          else:
              body = serializer(data)
              if not isinstance(body, (bytes, bytearray)):
                  raise TypeError(
                      f"serializer must return bytes, got {type(body).__name__}"
                  )
              ct = content_type        # may be None → transport defaults to octet-stream
          emitter(bytes(body), content_type=ct)
          raise _Done()
      ```
- [ ] Useful default: `default=str` in the JSON serializer handles `datetime`, `Path`, `Decimal`, etc. gracefully.
- [ ] Unit tests use a `MockBrowserHandle` (records calls) and verify each factory does the right thing.
- [ ] Unit tests for `notify`:
      - Outside an `init()` ContextVar binding → `RuntimeError`.
      - `notify({"a": 1})` (default serializer) → emitter receives `(b'{"a": 1}', "application/json")`; `_Done` raised.
      - `notify({}, content_type="application/vnd.example+json")` → emitter receives the explicit content-type.
      - `notify(b"raw", serializer=lambda x: x)` → emitter receives `(b"raw", None)` so transport picks octet-stream.
      - `notify("hello", serializer=lambda x: x.encode())` → emitter receives `(b"hello", None)`.
      - `notify({}, serializer=lambda x: "not bytes")` → `TypeError` before any emit.
      - `notify({"ts": datetime.now()})` → serialized via `default=str`, no TypeError.
- [ ] Public re-exports in `__init__.py` per the surface above (includes `notify`).

### Task B — Notification client (`_post_notification`)

**Files:** `webauto/_notification_client.py`, `tests/test_notification_client.py`

- [ ] Implement `_post_notification(notification, data, content_type=None)` — http only in v1:
  ```python
  def _post_notification(
      notification: dict,
      data: bytes,
      content_type: str | None = None,
  ) -> None:
      if notification["type"] == "http":
          headers = {"Content-Type": content_type or "application/octet-stream"}
          req = urllib.request.Request(
              notification["endpoint"], data=data,
              headers=headers, method="POST",
          )
          urllib.request.urlopen(req, timeout=5).read()
      elif notification["type"] == "sock":
          raise NotImplementedError(
              "sock transport deferred to v2 — see open questions in the plan"
          )
      else:
          raise ValueError(f"unsupported notification type: {notification['type']}")
  ```
- [ ] Keep the `sock` branch as a stub raising `NotImplementedError`. This preserves the seam — when sock framing is designed in v2, only this function and the listener need to grow a real implementation.
- [ ] Tests use a tiny throwaway `http.server` in the test, call `_post_notification`, assert the server received the expected bytes and Content-Type.
- [ ] Tests:
      - `_post_notification(http, b"raw")` → server sees `Content-Type: application/octet-stream`.
      - `_post_notification(http, b'{"a":1}', content_type="application/json")` → server sees the right header + body.
      - `_post_notification({"type": "sock", ...}, b"raw")` → `NotImplementedError`.
- [ ] Negative tests: server returns 500 → raises; unsupported type → ValueError.

### Task C — Notification listener (http only in v1)

**Files:** `webauto/_notification_listener.py`, `tests/test_notification_listener.py`

This is the **listener** half. Used by the CLI runner. Implementation is intentionally near-identical to schwab_cli's listener (per the schwab-side plan D13).

- [ ] `HttpNotificationListener` — same spec as schwab-side plan Task 2b Piece 1. Bind 127.0.0.1:0, embed 8-hex token in path, **the URL path token is the correlation** (no separate state validation against the body — body is opaque bytes). One-shot. 404 on wrong path.
- [ ] `make_notification_listener(transport_type)` factory:
      - `"http"` → `HttpNotificationListener()`.
      - `"sock"` → `NotImplementedError("sock transport deferred to v2")`.
      - other → `ValueError`.
- [ ] No `SockNotificationListener` in v1. Sock framing (length-prefix vs EOF-delim vs line-delim) is unresolved; see open questions.
- [ ] Tests parallel the schwab-side http test list. ~10 tests covering: correct path → accepted; wrong path → 404; correct path with body → body captured verbatim (Content-Type preserved); listener is one-shot.

### Task D — Browser backend: SeleniumBase UC handle

**Files:** `webauto/_seleniumbase_handle.py`, `webauto/_chrome_bootstrap.py`, `tests/test_seleniumbase_handle.py`, `tests/test_chrome_bootstrap.py`

- [ ] Implement `SeleniumBaseHandle(BrowserHandle)` per `2026-04-30-pluggable-auto-login.md` §Task 3 — fill, click, check, check_all, uncheck, wait_for_selector, wait_for_url, wait_for_load, sleep_ms, screenshot, current `url` property.
- [ ] Constructor honors `HEADLESS` / `DEBUG` env vars.
- [ ] Persistent profile under `~/.cache/webauto/chromium-uc/` (or `$XDG_CACHE_HOME/...`) so Schwab's Trust Device cookie survives runs.
- [ ] **Chrome version pinning** (new module `_chrome_bootstrap.py`):
  - `detect_chrome_version() -> int | None` — returns major version. Implementations:
    - macOS: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version` → parse `Google Chrome 124.0.6367.207` → `124`.
    - Linux: `google-chrome --version` / `google-chrome-stable --version` / `chromium --version` (try in order).
    - Windows: read `HKEY_CURRENT_USER\Software\Google\Chrome\BLBeacon\version` via `winreg`; fall back to `(Get-Item ...).VersionInfo` via subprocess.
    - Returns `None` if no binary found or output unparseable.
  - `resolve_chrome_version() -> int` — used by the handle constructor:
    1. If `CHROME_VERSION` env var is set, parse as `int`, return.
    2. Else call `detect_chrome_version()`. If non-None, return.
    3. Else raise `ChromeNotFoundError` with a message pointing to `webauto-cli doctor`.
  - Pass the resolved version to SeleniumBase's `Driver(uc=True, chromium_arg=..., binary_location=..., driver_version=str(version))` (whichever knob is current — SeleniumBase has changed this API a couple of times; pin the kwargs to a tested SeleniumBase version in pyproject).
- [ ] Tests for `_chrome_bootstrap.py`:
  - Mock `subprocess.run` for each OS path; verify parser handles real version-string formats including `Google Chrome 124.0.6367.207` and `Chromium 123.0.6312.86`.
  - `CHROME_VERSION=124` → returns 124 without invoking detection.
  - Detection fails (subprocess raises) → `detect_chrome_version()` returns None; `resolve_chrome_version()` raises `ChromeNotFoundError`.
  - `CHROME_VERSION` malformed (e.g. `CHROME_VERSION=124.0.6367`) → accept the leading-integer parse `124`; reject empty/non-numeric with a clear error.
- [ ] Tests for `SeleniumBaseHandle`: a couple of unit tests with a mock SeleniumBase Driver; integration tests against `https://example.com` for navigation / wait / current_url.

### Task E — Runner (walk list[PageAction], poll for completion)

**Files:** `webauto/_runner.py`, `tests/test_runner.py`

- [ ] **Dispatch model** (correction to the prior plan's forward-walk model): the runner treats `action.actions` as a routing table, not a sequence. Loop:
      1. Read `handle.current_url`.
      2. Find all PageActions whose `url(current_url)` is True AND that are not in the "exhausted-for-this-URL" set.
      3. Sort by `(PageAction.order, declaration_index)` ascending; pick the first.
      4. Run the selected PageAction's steps to completion.
      5. After completion: add it to the exhausted set. If `handle.current_url` changed since step 1, clear the exhausted set (new URL = new dispatch context).
      6. Loop until a step raises `_Done(payload)` (caught here → return payload) or `timeout_seconds` elapses (raise `TimeoutError`).
- [ ] Implications to document:
      - Declaration order is irrelevant when predicates don't overlap (the common case) AND orders are distinct.
      - Catch-all PageActions (`url_any=True`) typically set `order=200`+ so they fire only when specifics have skipped or exhausted.
      - A PageAction whose steps complete *without* changing the URL won't re-fire on that same URL (no infinite loops). To re-fire, the URL must change.
      - Overlapping predicates at the same `order` resolve by declaration order — that's the tiebreak of the tiebreak. If this matters, set explicit `order` numbers.
- [ ] URL-matcher logic in the runner is trivial — `PageAction.url` is always a callable predicate:
      ```python
      def _matches(predicate, current_url) -> bool:
          return predicate(current_url)
      ```
- [ ] Test the matcher dispatch in the runner: PageActions with `url=any_url()`, `url=contains("/x")`, `url=regex(r"/q/\w+")` each dispatch correctly against synthesised URLs.
- [ ] Signature: `run_actions(handle, action: BaseAction, *, timeout_seconds: float | None = None) -> dict` — returns the completion payload. When `timeout_seconds` is `None`, the runner reads `action.timeout_seconds`.
- [ ] Timeout enforcement: wall-clock budget covers the entire walk. Exceeded → raise `TimeoutError`.
- [ ] Important: the runner doesn't POST. `notify` (called inside a step) does the POST and then raises `_Done`. The runner is just the catch site.
- [ ] Tests with a `MockBrowserHandle` scripted to "navigate" through fake pages, plus a fake `BaseAction` whose steps include one that calls `notify({"ok": True})` — verify the emitter recorded the right payload and remaining steps in that PageAction did NOT execute.
- [ ] Tests: action never calls `notify` → `TimeoutError` after the budget. No partial POST.
- [ ] Tests: a step raises a non-`_Done` exception → propagates out of `run_actions` (operational failure path).
- [ ] Tests for dispatch / order-independence:
      - Shuffle PageAction declaration order; verify same flow completes given the same URL sequence (predicates are distinct).
      - `any_url()` PageAction placed at the *start* of the list still fires last (specific matches win).
      - Two PageActions with overlapping specific predicates → first-declared runs; the second is reachable only after the URL changes.
      - PageAction whose steps complete without URL change → not picked again on the same URL (no infinite loop). If `any_url()` exists as the only remaining match, it fires once and is then exhausted too.

### Task F — `init` entry point + notification wiring

**Files:** `webauto/__init__.py` (add `init`), `tests/test_init.py`

- [ ] Implement `init(...)`:
  ```python
  def init(*, url, notification, action, timeout_seconds=None):
      def _emit(data: bytes, content_type: str | None = None) -> None:
          _post_notification(notification, data, content_type=content_type)

      handle = SeleniumBaseHandle()                      # honors HEADLESS / DEBUG / CHROME_VERSION
      token = _notify_ctx.set(_emit)
      try:
          handle.goto(url)
          run_actions(handle, action, timeout_seconds=timeout_seconds)
      except _Done:
          pass                                           # normal completion; _emit already sent
      finally:
          _notify_ctx.reset(token)
          handle.close()
  ```
  Notes:
  - `_emit` is the closure that writes to the wire. `notify` calls it, then raises `_Done`.
  - Framework no longer takes a `state` arg — correlation is via the endpoint URL token, not the body. If a script wants to echo `AUTOMATION_STATE` in its body, it reads it from env itself.
  - When `timeout_seconds` is `None`, `run_actions` falls back to `action.timeout_seconds`.
- [ ] Tests: with a mock handle and a fake `BaseAction` whose steps call `notify({"price": "187.42"})`, plus a fake notification server — verify the server received the JSON-encoded body and `Content-Type: application/json`.
- [ ] Tests: action calls `notify(b"raw bytes", serializer=lambda x: x)` — server receives `b"raw bytes"` with `Content-Type: application/octet-stream` (transport default since no content_type given).
- [ ] Tests: action calls `notify(open("/tmp/x.pdf", "rb").read(), serializer=lambda x: x, content_type="application/pdf")` — server receives the bytes with the explicit content type.
- [ ] Tests: action never calls `notify*` within timeout → `TimeoutError` propagates out of `init`, no POST happens.
- [ ] Tests: `notify` called outside an `init()` context (i.e., from a unit test or REPL) → `RuntimeError`.
- [ ] Tests: action never calls `notify` within timeout → `TimeoutError` propagates out of `init`, no POST happens.
- [ ] Tests: `notify` called outside an `init()` context (i.e., from a unit test or REPL) → `RuntimeError`.

### Task G — CLI runner (`webauto-cli`)

**Files:** `webauto/cli/__main__.py`, `tests/test_cli.py`

- [ ] Use `argparse` (no extra deps). Parse the args listed in the CLI runner spec above.
- [ ] Implement `-a KEY=VALUE` parsing: `argparse.action='append'` over a custom type that splits on the first `=`. Reject entries with no `=` or empty keys.
- [ ] Implement `--env FILE`: dotenv-style parser (KEY=VALUE per line; `#` comments; blank lines OK; strip surrounding `'`/`"` from values). Validated up-front before spawning the subprocess; malformed file → exit 2 with a clear message.
- [ ] Env layering (`_build_script_env`): `{**os.environ, **load_env_file(args.env_file), **apply_a_pairs(args.env_pairs)}`. `-a` last-wins for duplicate keys; `-a` fully overrides matching keys from `--env`.
- [ ] No multi-value encoding. Each `-a KEY=VAL` sets a bare string. If users need a list, they pass `KEY=v1,v2` and split in their script.
- [ ] Implement both modes:
  - **Listener mode** (default): stand up local listener, set `AUTOMATION_NOTIFICATION_ENDPOINT` to its endpoint, wait, print payload.
  - **Remote mode** (`--notification-endpoint` set): set `AUTOMATION_NOTIFICATION_ENDPOINT` to the provided URL/path, no listener, wait for subprocess exit.
- [ ] Output formats (listener mode only):
  - `json` (default): print the raw JSON payload to stdout.
  - `kv`: print `key=value` lines for top-level scalar fields.
  - `raw`: if the payload has exactly one non-`state` field and it's a scalar, print just its value. Otherwise fall back to `json`.
- [ ] Tests use a tiny fixture script that POSTs a canned payload; assert `webauto-cli` spawns it, waits, prints the right JSON.
- [ ] Test `-a` single round-trip: fixture reads `os.environ["FOO"]` and POSTs `{"got": ...}`. `webauto-cli fixture.py -a FOO=bar` → output `{"got": "bar", ...}`.
- [ ] Test `-a` last-wins: `-a SYMBOL=AAPL -a SYMBOL=MSFT` → fixture sees `SYMBOL=MSFT` (bare string, no JSON array).
- [ ] Test `--env` loads file: KEY=VALUE pairs visible to fixture; comments + quoted values handled.
- [ ] Test `-a` overrides `--env`: file says `FOO=from_file`; `-a FOO=from_cli` → fixture sees `from_cli`.
- [ ] Test malformed `--env` file (line with no `=`) → exit 2, clear stderr message.
- [ ] Test malformed `-a` rejection: `-a NOEQUALS` and `-a =value` both exit non-zero with a clear error.
- [ ] Test remote mode: spawn an echo HTTP server, run `webauto-cli fixture.py --notification-endpoint http://127.0.0.1:<port>/sink -a URL=...`, assert the echo server received the payload and CLI's stdout was empty.
- [ ] Test SIGTERM cleanup: with a fixture script that sleeps forever, `webauto-cli --timeout 1` should terminate it within ~6s.
- [ ] Test `--no-state`: state token omitted from env, listener accepts any state in the notification.
- [ ] Add `[project.scripts] webauto-cli = "webauto.cli.__main__:main"` to `pyproject.toml`.
- [ ] Subcommand dispatch: argparse with a `subparsers` group. Reserved names: `doctor`, `bootstrap`. If `argv[1]` is neither, fall back to the run-mode parser. Help text reflects the dispatch rule.

### Task H — OAuth helper submodule (opt-in)

**Files:** `webauto/oauth.py`, `tests/test_oauth.py`

- [ ] Implement `parse_redirect(handle, redirect_uri, expected_state=None) -> dict | None`:
      - If `handle.url` doesn't start with `redirect_uri`, return `None`.
      - Else parse the query string. If `code` present → `{"kind": "code", "code": ...}`. If `error` present → `{"kind": "error", "error": ..., "error_description": ...}`.
      - If `expected_state` is given and the redirect's `state` doesn't match, return `{"kind": "error", "error": "state_mismatch"}`.
- [ ] Implement `notify_redirect(*, redirect_uri=None, expected_state=None)` step factory:
      ```python
      def notify_redirect(*, redirect_uri=None, expected_state=None):
          """Step factory: read current URL, parse OAuth query, call notify().

          If redirect_uri is None, reads handle.url verbatim and parses (the
          PageAction's url_contains already constrained us to the redirect).
          Otherwise validates that handle.url starts with redirect_uri.
          """
          def step(handle, _params=None):
              payload = parse_redirect(handle, redirect_uri or handle.url, expected_state)
              if payload is None:
                  raise RuntimeError(
                      f"notify_redirect: handle.url {handle.url!r} is not at {redirect_uri!r}"
                  )
              notify(payload)                  # POSTs and raises _Done
          return step
      ```
- [ ] Tests for `parse_redirect`: URL not at redirect → None. URL at redirect with `code` → `{"kind": "code", ...}`. URL at redirect with `error` → `{"kind": "error", ...}`. State mismatch → `{"kind": "error", "error": "state_mismatch"}`.
- [ ] Tests for `notify_redirect`: in a stubbed `init()` context, a step using `notify_redirect(redirect_uri="https://cb/")` against a `MockBrowserHandle` whose URL is `"https://cb/?code=X&state=S"` → emitter receives `{"kind": "code", "code": "X", "state": "S"}` and `_Done` is raised.
- [ ] No `OAuthAction` mixin — completion is via `notify_redirect()` as a step, not a method override. Less magic; same shape as any other action.

### Task I — Examples

**Files:** `examples/schwab_login.py`, `examples/scrape_quote.py`, `examples/README.md`

#### Schwab (OAuth example)
- [ ] Real reference action — a `BaseAction` subclass with `PageAction`s for Schwab's login pages (login form, MFA picker, trust device, consent, account select, confirmation). The trailing PageAction matches the redirect URL and runs `oauth.notify_redirect(...)` as its final step.
- [ ] Reads `URL`, `SCHWAB_USERNAME`, `SCHWAB_PASSWORD`, `SCHWAB_REDIRECT_URI` from its OWN env vars (NOT `AUTOMATION_*`) — credential and redirect names are caller-defined, not library-defined. The caller passes them via `-a`.
- [ ] Sketch:
      ```python
      from webauto import init, BaseAction, PageAction, fill, click, wait_visible
      from webauto.oauth import notify_redirect

      class SchwabLogin(BaseAction):
          timeout_seconds = 600

          def __init__(self, redirect_uri, expected_state):
              self.actions = [
                  PageAction(url_contains="/login", steps=[...]),
                  PageAction(url_contains="/mfa",   steps=[...]),
                  # ...more pages...
                  PageAction(url_contains=redirect_uri, steps=[
                      notify_redirect(redirect_uri=redirect_uri,
                                      expected_state=expected_state),
                  ]),
              ]

      if __name__ == "__main__":
          init(
              url=os.environ["URL"],
              notification={"type": os.environ["AUTOMATION_NOTIFICATION_TYPE"],
                            "endpoint": os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"]},
              state=os.environ.get("AUTOMATION_STATE"),
              action=SchwabLogin(
                  redirect_uri=os.environ["SCHWAB_REDIRECT_URI"],
                  expected_state=os.environ.get("AUTOMATION_STATE"),
              ),
          )
      ```
- [ ] Invocation:
      ```
      webauto-cli examples/schwab_login.py \
        -a URL='https://api.schwabapi.com/v1/oauth/authorize?...' \
        -a SCHWAB_USERNAME=alice \
        -a SCHWAB_PASSWORD=hunter2 \
        -a SCHWAB_REDIRECT_URI=https://relay.example.com/callback \
        --notification-type=http
      ```

#### Non-OAuth example
- [ ] `examples/scrape_quote.py` — a `BaseAction` that navigates to a public quote page, waits for the price element, and calls `notify({"symbol": ..., "price": ..., "ts": ...})` from a `custom` step. Demonstrates that nothing about the framework requires OAuth.
- [ ] Sketch:
      ```python
      from webauto import init, notify, BaseAction, PageAction, wait_visible, custom

      class ScrapeQuote(BaseAction):
          actions = [
              PageAction(url_contains="/quote/", steps=[
                  wait_visible(".price"),
                  custom(lambda h: notify({
                      "symbol": h.text(".symbol"),
                      "price":  h.text(".price"),
                      "ts":     datetime.utcnow().isoformat() + "Z",
                  })),
              ]),
          ]
      ```
- [ ] `__main__` block reads `os.environ["URL"]` and `os.environ.get("SYMBOL")`.
- [ ] Invocation:
      ```
      webauto-cli examples/scrape_quote.py \
        -a URL=https://example.com/quote/AAPL \
        -a SYMBOL=AAPL
      ```

#### README
- [ ] `examples/README.md` documents both examples: what `-a` keys each one needs, how to run standalone via `webauto-cli`, and (for Schwab) how to plug into schwab_cli's `auto_login.script` config.

### Task K — `webauto-cli doctor` + `bootstrap`

**Files:** `webauto/cli/_doctor.py`, `tests/test_doctor.py`

Pre-flight check + auto-install for the Chrome/chromedriver toolchain.

- [ ] `webauto-cli doctor` — prints a checklist with PASS / FAIL / WARN per row:
  - **Chrome binary present?** Calls `detect_chrome_version()` from `_chrome_bootstrap.py`. On FAIL, prints the platform-appropriate install command (see below).
  - **Chrome version pinned (`CHROME_VERSION`) vs detected?** WARN if pinned differs from detected by more than one major version; PASS otherwise.
  - **chromedriver cached for that version?** Check SeleniumBase's cache dir. If missing, note that the first run will download it (~10MB).
  - **Profile dir writable?** stat `~/.cache/webauto/chromium-uc/`; PASS if writable or creatable, FAIL with a clear error otherwise.
  - **SeleniumBase importable + version compatible?** import check + `seleniumbase.__version__` against pinned range in pyproject.
  - **Listener ports?** Try binding to `127.0.0.1:0` to confirm loopback works.
  - Exit code: 0 if all PASS, 1 if any FAIL, 0 if only WARN.

  Note: doctor does NOT lint PageAction predicates for overlap. Predicate anchors can be late-bound (`url_contains=p("REDIRECT_URI")`), so the actual URL strings aren't known at startup — any overlap check would have huge blind spots and create a false sense of safety. Predicate conflicts resolve at runtime via `PageAction.order` + the exhaustion-on-no-URL-change mechanism (see Task E).
- [ ] `webauto-cli bootstrap [--yes]` — runs the install commands.
  - macOS: `brew install --cask google-chrome` (require Homebrew; if missing, fall back to the direct DMG download URL with `curl` + `hdiutil`).
  - Linux (apt): `wget -qO - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -; echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list; sudo apt update; sudo apt install -y google-chrome-stable`.
  - Linux (dnf): equivalent for Fedora.
  - Windows: `winget install Google.Chrome` (or chocolatey fallback).
  - Without `--yes`: print the command(s) for the detected OS and exit 0 without running.
  - With `--yes`: actually shell out. Stream stdout/stderr to terminal. Exit non-zero on failure.
  - After install, pre-warm chromedriver: instantiate `SeleniumBaseHandle()` once with a minimal URL (about:blank) to trigger driver download, then close.
- [ ] Tests:
  - Mock subprocess. Verify each OS branch picks the right install command.
  - `doctor` with all PASS → exit 0.
  - `doctor` with Chrome missing → exit 1, output contains the install command.
  - `bootstrap` without `--yes` → prints commands, doesn't run them (verify via mock).
  - `bootstrap --yes` → invokes the expected subprocess.

### Task J — README + smoke tests

**Files:** `README.md`

- [ ] Document the library API. Lead with the non-OAuth case ("here's how to write an action that scrapes a page") to drive home that OAuth isn't the central concept.
- [ ] Document the CLI usage with both worked examples (scrape + OAuth) plus `doctor` / `bootstrap`.
- [ ] Document the env-var contract, including `CHROME_VERSION`.
- [ ] Smoke test (fresh-machine): on a VM without Chrome installed, `webauto-cli doctor` reports Chrome missing → `webauto-cli bootstrap --yes` installs it → `webauto-cli doctor` reports PASS.
- [ ] Smoke test (non-OAuth): `webauto-cli examples/scrape_quote.py -a URL=https://example.com/quote/AAPL -a SYMBOL=AAPL` → prints `{"symbol": "AAPL", "price": "...", ...}` and exits 0.
- [ ] Smoke test (OAuth): `webauto-cli examples/schwab_login.py -a URL='https://api.schwabapi.com/v1/oauth/authorize?...' -a SCHWAB_USERNAME=... -a SCHWAB_PASSWORD=... -a SCHWAB_REDIRECT_URI=https://relay.example.com/callback`, do MFA on phone, assert it prints `{"kind": "code", "code": "...", "state": "..."}` and exits 0.
- [ ] Smoke test (version pin): `CHROME_VERSION=124 webauto-cli examples/scrape_quote.py -a URL=...` works against a Chrome 124 install; with an obviously-wrong pin like `CHROME_VERSION=1`, the launch fails fast with the chromedriver mismatch error rather than a generic timeout.

## Open questions

1. **Why not import a caller's listener instead of duplicating?** Tempting but creates a circular dep when the caller is also a consumer of this library. The protocol is ~150 lines; duplicating is cheap. Refactor into a shared package later if the protocol churns or grows.

2. **`record` mode** — see B7. Low priority, future task.

3. **Alternative browser backends** — Selenium + undetected-chromedriver, raw Playwright, Firefox. Each is ~50 lines implementing `BrowserHandle`. Add when a real need arises.

4. **Distribution** — PyPI? brew? Once at least one real consumer is shipping, decide if `webauto` is `pip install webauto` or stays a "git clone + uv sync" project. Probably PyPI eventually.

5. **Streaming / multiple `notify()` calls?** Today, the first `notify(payload)` POSTs once and ends the run via `_Done`. A future version could allow `notify(payload, done=False)` for streaming items (more Scrapy-like) and `notify(payload, done=True)` to close. Defer until a real use case demands it.

6. **`init` from non-`__main__` callers?** Right now `init` is designed to be the script's terminal call (it launches, runs, posts, exits). If someone wanted to invoke an action in-process, they'd call `run_actions(handle, action, ...)` directly. That's fine — leave `init` alone, document the lower-level entry.

7. **sock transport framing (deferred to v2).** v1 ships http only because http carries framing for free (Content-Length, request boundaries). A `SOCK_STREAM` unix socket needs an explicit framing protocol:
   - **Length-prefixed:** 4-byte big-endian length + payload. Simple, supports arbitrary binary, but client and server must agree on the prefix encoding.
   - **EOF-delimited:** client sends, calls `shutdown(SHUT_WR)`, server reads until EOF. Works for one-shot; couldn't extend to streaming.
   - **HTTP-over-sock:** speak HTTP/1.1 over the unix socket (some libraries do this). Free framing reuse, but adds an HTTP parser dep on the listener side.
   - **netstring** (`<len>:<data>,`): self-describing, simple to parse, well-trodden.
   Need to pick one (probably length-prefixed for binary friendliness or HTTP-over-sock for symmetry with the http transport) before shipping `_post_notification`'s sock branch.

## Acceptance criteria

- [ ] `pip install -e webauto` (or `uv sync` in the project dir) installs cleanly with SeleniumBase as the only browser dep.
- [ ] Library: `from webauto import init, notify, BaseAction, PageAction, fill, click` and the other 9 factories — all importable. `from webauto.oauth import parse_redirect, notify_redirect` also importable.
open- [ ] CLI: `webauto-cli --help` prints the documented usage including subcommands.
- [ ] CLI run mode: `webauto-cli examples/schwab_login.py -a URL='https://...' -a SCHWAB_USERNAME=... -a SCHWAB_PASSWORD=... -a SCHWAB_REDIRECT_URI=... --notification-type=http` opens a browser, drives the configured Schwab login, captures the redirect, prints `{"kind": "code", "code": "...", ...}` to stdout, exits 0.
- [ ] CLI: `--notification-type=sock` rejected at parse time with a clear "not yet implemented" message (v1 ships http only).
- [ ] CLI: `--notification-endpoint` remote mode posts to an external URL and exits without printing the payload.
- [ ] CLI: with a deliberately wrong selector in the script, the script subprocess fails with stderr message and exits non-zero; CLI surfaces that, exits non-zero.
- [ ] CLI: `--timeout 1` on a sleeping fixture script terminates it within 6 seconds.
- [ ] CLI: `--env FILE` loads dotenv values; `-a KEY=...` fully overrides values from the file for the same key.
- [ ] CLI: repeated `-a KEY=V1 -a KEY=V2` → script sees `KEY=V2` (last-wins, bare string — no list encoding).
- [ ] CLI doctor: on a machine with Chrome installed, `webauto-cli doctor` exits 0; on a machine without Chrome, exits 1 and prints the right install command for the OS.
- [ ] CLI bootstrap: `webauto-cli bootstrap` (no `--yes`) prints the install command but doesn't run; with `--yes`, runs it.
- [ ] Chrome version pinning: `CHROME_VERSION=124 webauto-cli ...` pins chromedriver to 124; absent the var, the framework detects the installed Chrome version and pins to that.
- [ ] `pytest` green across all tests in Tasks A–H + K; integration tests in Task J run manually.
- [ ] schwab_cli (per the sibling plan) can point `cfg.auto_login.script` at this project's `webauto-cli` (wrapping `schwab_login.py`) or directly at `examples/schwab_login.py`, and complete auth end-to-end.
