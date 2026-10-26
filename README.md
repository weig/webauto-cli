# webauto

Standalone, general-purpose webauto service. Give it a URL + an action script; it drives the browser through your declared steps and POSTs whatever payload your action chose to send to a notification endpoint.

No OAuth assumptions in the framework. OAuth is a one-page helper (`webauto.oauth`) you import only if your script needs it.

## Install

Managed with [uv](https://docs.astral.sh/uv/). Python 3.11+. Runtime deps: `playwright` (the default browser backend, driving the system Chrome), `seleniumbase` (opt-in backend, imported only when selected), `pyyaml` (declarative sites) and `cryptography` (encrypted env files).

### One-liner (recommended)

Latest `main`:

```bash
curl -LsSf https://raw.githubusercontent.com/weig/webauto/main/bin/install.sh | sh
```

Pinned to a tag / release:

```bash
curl -LsSf https://raw.githubusercontent.com/weig/webauto/main/bin/install.sh | sh -s -- --tag v0.2.0
```

Pinned to a branch or commit SHA:

```bash
curl -LsSf https://.../install.sh | sh -s -- --branch my-feature
curl -LsSf https://.../install.sh | sh -s -- --ref a1b2c3d
```

From a release-asset wheel (the GitHub Releases page hosts these):

```bash
curl -LsSf https://.../install.sh | sh -s -- --wheel \
  https://github.com/weig/webauto/releases/download/v0.2.0/webauto-0.2.0-py3-none-any.whl
```

Override the upstream entirely (fork, mirror, internal hosting):

```bash
BROWSER_AUTOMATION_REPO=https://github.com/me/fork.git \
BROWSER_AUTOMATION_REF=my-branch \
  curl -LsSf https://.../install.sh | sh
```

The installer ensures `uv` is present, runs `uv tool install` against the chosen source, and verifies with `webauto-cli doctor`. After this, `webauto-cli` is on `PATH` from any directory. To upgrade later, re-run the same one-liner (the installer always uses `--force`, which upgrades in place).

### From a local checkout (for development)

```bash
git clone <repo> && cd webauto
uv sync                                # creates .venv + installs everything
uv run webauto-cli doctor           # confirm Chrome + chromedriver + ports
uv run webauto-cli bootstrap --yes  # install Chrome if doctor flagged it
```

Or `uv tool install --editable .` to expose `webauto-cli` globally while keeping the source live for edits.

## Quickstart — write an action

```python
# my_scrape.py
import os
from dataclasses import dataclass
from typing import ClassVar

from webauto import (
    init, notify, BaseAction, PageAction,
    wait_visible, custom,
)

@dataclass
class Params:
    URL: str

class Scrape(BaseAction):
    Params: ClassVar[type] = Params

    def __init__(self, params=None):
        super().__init__(params)
        self.actions = [
            PageAction(url_contains="/quote/", steps=[
                wait_visible(".price"),
                custom(lambda h: notify({"url": h.url})),
            ]),
        ]

if __name__ == "__main__":
    action = Scrape()
    init(
        url=action.params.URL,
        notification={
            "type":     os.environ["AUTOMATION_NOTIFICATION_TYPE"],
            "endpoint": os.environ["AUTOMATION_NOTIFICATION_ENDPOINT"],
        },
        action=action,
    )
```

Run it:

```bash
webauto-cli my_scrape.py -a URL=https://example.com/quote/AAPL
```

`webauto-cli` stands up a local listener, sets `AUTOMATION_NOTIFICATION_*` for the script, spawns it, prints the captured JSON when `notify(...)` posts, and exits.

## Concepts

### `BaseAction` + `PageAction`s as a routing table

An action is a list of `PageAction`s. Each `PageAction` has a URL predicate and a list of steps. The runner reads `handle.url`, picks the matching `PageAction` with lowest `order` (default `100`), and runs its steps. After steps complete, the `PageAction` is marked exhausted-for-this-URL; URL change clears the exhausted set.

**Order matters only when predicates overlap.** Non-overlapping predicates can be declared in any order. Catch-all `url_any=True` PageActions conventionally set `order=200`+ so they only fire after specifics.

### URL matchers

```python
PageAction(url_contains="/login",      steps=[...])       # substring
PageAction(url_startswith="https://",  steps=[...])       # prefix
PageAction(url_equals="https://h/y",   steps=[...])       # exact
PageAction(url_regex=r"/quote/[A-Z]+", steps=[...])       # re.search
PageAction(url_any=True, order=200,    steps=[...])       # always (catch-all)
```

For complex matching: `PageAction(url=<predicate>, ...)` accepts any `Callable[[str], bool]`.

### Params dataclass + `get_parameter(...)`

Declare inputs as a `@dataclass`; the framework auto-builds it from `os.environ`:

```python
@dataclass
class MyParams:
    URL: str                              # required
    USERNAME: str
    PASSWORD: str
    MODE: str = "client"                  # optional, with default
    APP: Optional[str] = None             # optional, may be None
    PORT: int = 8080                      # parsed as int
    DEBUG: bool = False                   # truthy parse: 1/true/yes/on

class MyAction(BaseAction):
    Params: ClassVar[type] = MyParams
```

Each env value is a bare string at the wire level. If you need a list, declare the field as `str` and split it yourself (e.g. `params.SYMBOLS.split(",")` after passing `-a SYMBOLS=AAPL,MSFT`).

Reference parameters inside steps via `self.get_parameter("NAME")` — returns a zero-arg lambda resolved at step-execution time. Useful for keeping secrets out of module-scope globals.

### `notify(data, *, serializer=None, content_type=None)`

Default: JSON-encodes + sends `application/json`. Override for raw control:

```python
notify({"price": "187.42"})                                 # default JSON
notify(b"<raw PDF>", serializer=lambda x: x,
       content_type="application/pdf")
notify(my_obj, serializer=msgpack.packb,
       content_type="application/msgpack")
```

After sending, `notify` raises an internal sentinel that the runner catches → `init()` closes the browser → exit 0.

### OAuth helpers (opt-in)

```python
from webauto.oauth import notify_redirect

# Drop into your final PageAction:
PageAction(url_contains=self.params.REDIRECT_URI, steps=[
    notify_redirect(redirect_uri=self.params.REDIRECT_URI,
                    expected_state=self.params.STATE),
]),
```

`notify_redirect` reads `handle.url`, parses the OAuth query params, and calls `notify(payload)` with `{"kind": "code", ...}` or `{"kind": "error", ...}`.

### Browser backends

Playwright is the default. SeleniumBase (UC mode, useful against bot checks) is opt-in:

```python
init(url=..., action=action)                          # Playwright
init(url=..., action=action, browser="seleniumbase")  # SeleniumBase
```

or set `WEBAUTO_BROWSER=seleniumbase` for the process. The explicit argument wins over the env var. The Schwab examples pass `browser="seleniumbase"`.

Selectors work the same on both: XPath when they start with `/` or `(`, `text=...` for a substring match, CSS otherwise. To reach into an iframe, write `<frame selector> >> <element selector>` (e.g. `//iframe[@id='WAUTP002'] >> //input[@name='PI_ORI_PASSWORD']`): the left part must match exactly one iframe, the right part is resolved inside it (one level). A missing frame counts as 0 / not visible; actions raise. This `>>` only means "enter this iframe" — it is not Playwright's locator chaining, and the split is at the first ` >> `, so keep a literal ` >> ` out of `text=` values. On SeleniumBase, `wait_for_selector` does not wait for a frame that has not appeared yet; wait on the iframe first (or use `verify`, which re-checks until its timeout). On Playwright, `fill`/`click` are strict — a selector matching more than one element raises instead of acting on the first — and screenshots mask password inputs.

### Verification steps

`verify_all` checks a page's structure before you act on it. Every expectation is evaluated and reported; it raises `VerificationFailed` (listing each check as ✓/✗ with expected vs found counts) only after all of them ran, so put it in front of the steps it guards:

```python
from webauto import expect, verify_all, click_if_unique, unexpected_page

PageAction(url_contains="/logon", steps=[
    verify_all(
        expect("login form", "//form[@id='loginForm'][count(.//input|.//button)=3]"),
        expect("user field", "//input[@id='UserName']"),
        expect("rows", ".row", count=3),          # default count is 1
        screenshot="/tmp/login_failed.png",       # taken only on failure
    ),
    fill("//input[@id='UserName']", p("USERNAME")),
    click("//button[@id='btnLogonSubmit']"),
]),
PageAction(url_any=True, order=1000, steps=[unexpected_page(grace_seconds=10)]),
```

- `unexpected_page` is a catch-all step: it gives a transient URL `grace_seconds` to move on, then raises `UnexpectedPage` instead of idling until the global timeout.
- `click_if_unique(selector)` clicks only when exactly one element matches — meant for cleanup.
- `BaseAction.on_failure` is a list of steps `init()` runs (best effort) when any step raises, before closing the browser; their errors never mask the original one.

### Declarative sites (YAML)

A site file describes pages, selectors and steps without writing an action class. See the docstring of `webauto/_site.py` for the full schema.

```yaml
name: wbct
start_url: https://example.com/logon
params: [USERNAME, PASSWORD]
on_failure:
  - click_if_unique: "//a[@href='/logout']"
pages:
  - name: login
    url_contains: /logon
    steps:
      - verify:
          login form: "//form[@id='loginForm'][count(.//input|.//button)=3]"
      - fill: {selector: "//input[@id='UserName']", value: $USERNAME}
      - fill: {selector: "//input[@id='Password']", value: $PASSWORD}
      - click: "//button[@id='btnLogonSubmit']"
      - wait_page_change: 30
  - name: home
    url_regex: '/\?_='
    steps:
      - verify:
          logged in: "//div[@class='headerUserSite']"
      - click: "//a[@href='/logout']"
      - done
```

```python
from webauto import run_site
run_site("sites/wbct.yaml", {"USERNAME": "...", "PASSWORD": "..."}, screenshot_dir="shots/")
```

A catch-all `unexpected_page` (10 s grace; `unexpected_page: false` to disable) is appended automatically. Missing params fail before the browser starts.

## CLI

```
webauto-cli SCRIPT [OPTIONS]         (run an action script)
webauto-cli doctor                   (environment health check)
webauto-cli bootstrap [--yes]        (install Chrome + warm chromedriver)

  --notification-type {http}            v1 ships http only; sock = v2
  --notification-endpoint ENDPOINT      if set: remote mode (no local listener)
  --env FILE                            load KEY=VALUE pairs from a dotenv file
  -a KEY=VALUE                          env var passed to the script.
                                        Repeatable; last-wins for duplicate
                                        keys. Overrides --env file values.
                                        Each value is a bare string — if you
                                        need a list, pass KEY=v1,v2 and split
                                        in your script.
  --state TOKEN | --no-state            correlation token (default: random)
  --timeout SECONDS                     outer subprocess budget (default 600)
  --output {json|raw|kv}                listener-mode payload format
```

### Env precedence

Later layers override earlier ones:

1. `os.environ` (inherited from the CLI process).
2. `--env FILE` (dotenv-style; comments and blank lines OK; surrounding quotes stripped).
3. `-a KEY=VALUE` (each `-a` overrides the same key from the file; repeated `-a` for the same key: last-wins).

### Listener mode vs remote mode

- **Listener mode** (default): CLI stands up a local HTTP listener, sets `AUTOMATION_NOTIFICATION_ENDPOINT` to its endpoint, prints the received payload to stdout.
- **Remote mode** (`--notification-endpoint http://...`): CLI sets the env var to the supplied URL; script POSTs there directly; CLI just orchestrates execution and reports subprocess exit code.

## Wire protocol

Env vars the framework sets (consumed by your script):

| Name | Value |
|---|---|
| `AUTOMATION_NOTIFICATION_TYPE` | `"http"` (v1) |
| `AUTOMATION_NOTIFICATION_ENDPOINT` | full URL |
| `AUTOMATION_STATE` | optional correlation token; framework does NOT inject it into the body |
| `HEADLESS` | headless by default; set `HEADLESS=0` (or `false`/`no`/`off`) for a visible window |
| `DEBUG` | same — verbose logging |
| `CHROME_VERSION` | SeleniumBase only: pin chromedriver to this Chrome major version; if unset, framework auto-detects |
| `WEBAUTO_BROWSER` | `playwright` (default) or `seleniumbase`; `init(browser=...)` overrides it |
| `WEBAUTO_CDP_URL` | Playwright only: attach to an already running Chrome (`http://127.0.0.1:9222`) instead of launching one; that browser is left open |
| `WEBAUTO_PW_CHANNEL` | Playwright only: browser channel to launch (default `chrome`, the system install) |

`-a KEY=VALUE` env-passthroughs are entirely script-defined; the framework never reads or interprets them.

Output: the bytes your script passed to `notify(data, ...)`. JSON by default; anything else if you supplied a custom serializer.

## Encrypting `.env` files

Plaintext `.env` files are convenient but a casual file leak (Time Machine, cloud backup, IDE indexer) exposes credentials. `webauto-cli` ships a symmetric encryption mode that keeps secrets out of any single file.

**Threat model**: prevent casual disclosure. Two artifacts in separate places — *both* are needed to recover credentials. Not designed against root-level or full-disk attackers.

| Artifact | Default path | Notes |
|---|---|---|
| Fernet key | `~/.config/webauto-cli/private_key` | chmod 600; back this up somewhere safe |
| Encrypted `.env` | wherever you put it (e.g. `~/.config/schwab_cli/auto_login.env`) | chmod 600; safe to sync to other machines on its own |

Override the key path via `WEBAUTO_KEY_PATH` env var or `--key-file PATH` flag.

### One-time setup

```bash
# 1. Generate a key (refuses to overwrite — back this up before re-running).
webauto-cli secrets keygen

# 2. Write your plaintext .env once.
cat > /tmp/plain.env <<EOF
URL=https://api.schwabapi.com/...
USERNAME=alice
PASSWORD=hunter2
REDIRECT_URI=https://relay.example.com/callback
EOF

# 3. Encrypt and move to its final home.
webauto-cli secrets encrypt /tmp/plain.env --out ~/.config/schwab_cli/auto_login.env

# 4. Shred the plaintext.
rm -P /tmp/plain.env   # or `shred -u` on Linux
```

### Running

`--env FILE` **auto-detects** encrypted vs plaintext. No new flag:

```bash
webauto-cli examples/schwab_auth.py --env ~/.config/schwab_cli/auto_login.env
```

If the file looks like a Fernet token (`gAAAAA...`), it gets decrypted with the key. If it's plain `KEY=VALUE`, it's parsed as-is.

### Maintenance

```bash
webauto-cli secrets decrypt ~/.config/schwab_cli/auto_login.env   # print to stdout (inspect / edit)
webauto-cli secrets path                                          # show resolved key path
```

To rotate the key:
1. `webauto-cli secrets decrypt <enc>` → save plaintext.
2. `webauto-cli secrets keygen --force` → new key (orphans every old encrypted file).
3. `webauto-cli secrets encrypt <plain> --out <enc>` → re-encrypt with the new key.
4. Shred the plaintext.

### What's protected, what isn't

- **Protected**: someone who copies *just* the encrypted env file (or *just* the key file). E.g. selectively sharing a project dir, accidentally committing the env, cloud backups that capture one tree but not the other.
- **Not protected**: a process running as your user can read both files (no OS-level isolation). For stronger guarantees, pair this with macOS Keychain / 1Password / `pass` to store the password separately — see the previous design discussion.

## Development

```bash
uv sync                   # installs runtime + dev deps (the [dependency-groups] dev group)
uv run pytest             # 217 tests, ~20s (the Playwright handle tests need a local Chrome)
```

## Plan + design rationale

See `docs/plan/2026-05-14-webauto.md` for the full architectural plan and decision log.
