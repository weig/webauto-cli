# Examples

Three reference action scripts.

## `scrape_quote.py` — non-OAuth

Drives a quote page, builds a payload dict from the URL + symbol, and calls `notify(...)`. Demonstrates that the framework has no OAuth assumptions.

```bash
webauto-cli examples/scrape_quote.py \
  -a URL=https://example.com/quote/AAPL \
  -a SYMBOL=AAPL
```

Output to stdout:

```json
{"symbol": "AAPL", "url": "https://example.com/quote/AAPL", "ts": "2026-05-14T18:00:00Z"}
```

## `schwab_auth.py` vs `schwab_auth_code_relay.py` — two Schwab OAuth flows

Both files drive the **same** Schwab login → MFA → consent → accounts → confirmation page sequence (identical selectors and `PageAction` routing). They differ **only in the last `PageAction`** — what happens once the browser lands on the redirect URL:

| File | Flow | Final step | Use when |
|---|---|---|---|
| `schwab_auth.py` | **code_flow** (a.k.a. *client*) | `notify(parse_url(...))` — parses `?code=...&state=...` from the URL and forwards via the notification channel | Caller picks up the code from the framework's notification (listener mode or `--notification-endpoint`) |
| `schwab_auth_code_relay.py` | **code_relay** | `notify({"status": "redirect_reached"})` — minimal "I'm done" so the framework unwinds cleanly | The relay endpoint at `REDIRECT_URI` captures the code server-side and exposes a separate storage URL the caller polls |

In both cases the framework still requires a `notify()` to unwind cleanly — the only difference is whether the payload carries the captured code or just an arrival signal.

### Common page sequence

| # | URL fragment | What the steps do |
|---|---|---|
| 1 | `/login-one-step` | Fill username + password; click submit; wait for URL change |
| 2 | `/authenticators` | Click the "Schwab App" MFA option; wait for `/mobile_approve` |
| 3 | `/mobile_approve` | Wait up to 2 minutes for the user to tap the push notification |
| 4 | `/devicetag/remember` | Tick "yes, remember"; click continue (only first time on this profile) |
| 5 | `/third-party-auth/cag` | Tick accept-terms; click continue; accept the modal (Angular hydration wait before each click) |
| 6 | `/third-party-auth/account` | Tick every account checkbox; click continue |
| 7 | `/third-party-auth/confirmation` | Click "Done" (id is literally `cancel-btn`); browser redirects to relay |
| 8 | `REDIRECT_URI` substring | **differs per file — see table above** |

URL-routed dispatch means MFA branches (steps 2, 3, 4) **skip themselves** when not applicable — if the trust-device cookie is already set, Schwab jumps straight from `/mobile_approve` to `/third-party-auth/cag` and `PageAction #4` never fires.

### Run it

Either example reads the same env-var set:

```env
URL=https://api.schwabapi.com/v1/oauth/authorize?client_id=...&redirect_uri=https://relay.example.com/callback&response_type=code&scope=readonly
USERNAME=alice
PASSWORD=hunter2
REDIRECT_URI=https://relay.example.com/callback
```

```bash
# Plaintext .env:
webauto-cli examples/schwab_auth.py            --env .schwab.env
webauto-cli examples/schwab_auth_code_relay.py --env .schwab.env

# Or encrypted (recommended — see project README → "Encrypting .env files"):
webauto-cli examples/schwab_auth.py            --env ~/.config/schwab_cli/auto_login.env
webauto-cli examples/schwab_auth_code_relay.py --env ~/.config/schwab_cli/auto_login.env
```

Stdout:

```jsonc
// schwab_auth.py — captured URL/code/state/session:
{"base_url": "https://relay.example.com/callback",
 "code": "C0.b2c1...", "session": "...", "state": "..."}

// schwab_auth_code_relay.py — minimal arrival signal:
{"status": "redirect_reached"}
```

### Plugging into schwab_cli

Set `auto_login.script` in schwab_cli's config to the absolute path of whichever script matches your auth flow. schwab_cli spawns it with the same wire-protocol env vars `webauto-cli` uses; for **code_flow** it reads the captured code from the notification, for **code_relay** it polls the relay's storage API after the script exits successfully.

### Caveats — when steps may need extending

Schwab's Angular forms listen to specific event sequences (`input`/`change`/`blur`) that native Selenium clicks don't always fire correctly. Our `SeleniumBaseHandle` already JS-dispatches these for `fill` / `check` / `check_all` and escalates `click` through three strategies (native → JS click → synthesized MouseEvent for shadow-DOM crossing). The current Schwab UI works end-to-end with the bundled handle.

If Schwab updates the UI and a step starts failing, you have two options:

- Replace the failing step with `custom(...)` that calls `handle.execute_script(...)` directly to do the JS dispatch yourself.
- Or extend `SeleniumBaseHandle` with a new method + matching step factory in `_factories.py`.

Selectors live at the top of each script and at `schwab_cli/browser/selectors.py` in the upstream `schwab_cli` repo — that's the place to refresh them when the UI changes.

## Editing for a new site

1. Define a `@dataclass` of inputs.
2. Subclass `BaseAction`, set `Params: ClassVar[type]`.
3. In `__init__`, after `super().__init__(params)`, build `self.actions = [PageAction(...), ...]`.
4. Final `PageAction` ends with a step that calls `notify(...)` — full payload or minimal signal, your call.
5. Run `webauto-cli <script.py> --env .env`.
