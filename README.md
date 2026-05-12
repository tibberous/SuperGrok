# SuperGrok Bridge

**One desktop window for every chat AI. CLI for headless prompts. Real logged-in sessions, no API keys.**

SuperGrok Bridge is a PySide6 + Qt WebEngine shell that hosts the web versions of **Grok**, **ChatGPT**, **Gemini**, and **Claude** inside a single split-pane desktop window with a persistent per-provider cookie profile. From the command line you can fire a prompt at any of them and get the answer back on stdout — using your own logged-in session, not a paid API.

> This project is intentionally built around your normal logged-in browser session. It does **not** bypass login, CAPTCHA, subscription checks, rate limits, or any access controls. You log in once through the visible bridge window; your cookies persist; the CLI reuses them.

## Highlights

- 🤖 **Four providers in one shell** — Grok, ChatGPT (incl. `--gpt`/`--gtp` aliases), Gemini, Claude.
- 💬 **CLI chat** — `python start.py --chat "hello"` returns the answer to stdout. Attach files with `--attach`.
- 🧠 **Resident bridge service** — `--serve-bridge` keeps the QtWebEngine warm and logged-in for instant CLI responses.
- 🎨 **Polished UI** — provider-aware toolbar/title, dark theme, visible resizable splitter handles, Prism-highlighted **View Source** with one-click plain fallback.
- 🛠️ **Built-in DevTools** — Chromium DevTools dock per pane, plus remote debugging on `http://127.0.0.1:9222`.
- 🪟 **Offscreen modes** — render-but-invisible Chromium surface on Windows; Xvfb/Xdummy/Xpra on Linux.
- 🧹 **Lifecycle-safe** — PID-tracked subprocesses, TTL watchdog, no orphan Chromium children.

## Author & contact

**Trenton Tompkins** — [trentontompkins@gmail.com](mailto:trentontompkins@gmail.com) — `724-431-5207` — [github.com/tibberous](https://github.com/tibberous)

> 📞 **Need help on your next project? Call me at 724-431-5207 for a free consultation!**

*Codex by Claude Opus 4.7 and ChatGPT 5.5.*

## Local config

Per-machine secrets and overrides live in `config.ini` (gitignored). Copy `config.ini.example` to `config.ini` and fill in any values you need (GitHub PAT for automation, bridge port, profile dir override, etc.). The repo ships only `config.ini.example`; your real `config.ini` never gets committed.

---

## Run

```powershell
pip install -r requirements.txt
python start.py --debug
```

Useful flags:

```powershell
python start.py --url https://grok.com/
python start.py --remote-debug-port 9222
python start.py --profile-dir .local_profile
python start.py --offscreen
```

## Current shell

- Left column: `QWebEngineView` chat transcript.
- Under left column: native prompt textbox plus Send button.
- Right column: real live `QWebEngineView` loading `https://grok.com/`.
- Right-click either web pane for:
  - Copy
  - View Source
  - Dev Inspect with Chromium DevTools
  - Open DevTools Dock
- View Source opens a Prism-highlighted modal with:
  - right-click Copy
  - Copy All
  - Save As
- Chromium remote debugging is enabled by default on port `9222` unless started with `--remote-debug-port 0`.

## jQuery

`assets/jquery/jquery.min.js` is loaded only into the left local chat pane. It is not injected into `grok.com`.

The repo currently includes a tiny fallback jQuery-compatible shim because this sandbox cannot fetch CDN payloads. To replace it with the official jQuery 3.7.1 file on your machine, run:

```powershell
python tools/fetch_jquery.py
```

## Prism

Prism assets are vendored under `assets/prism/` for the View Source modal. The repo currently includes fallback Prism stubs. To replace them with official Prism files, run:

```powershell
python tools/fetch_prism.py
```

## Flatline

Drop the newest Flatline debugger files into `flatline/`. The current `start.py` is intentionally simple and has a single integration point named `tryLoadFlatlineDebugger()`.

## FlatLine surface contract

`start.py` now answers the launcher probe directly:

```powershell
python start.py --debugger-query-surfaces
python start.py --debugger-vardump
```

Advertised surfaces are `heartbeat`, `poll`, `vardump`, and `accepts-proxy`. The app emits a Qt-event-loop heartbeat once per second into `data/supergrok_bridge_debugger.sqlite3` while the main window is alive. `poll` is backed by the existing Grok chat poll timer plus the ToolCall process watchdog; `vardump` includes current URL, runtime flags, advertised surfaces, and a process-table snapshot. DB-backed `debugger-exec-command` / `debugger-cron-command` are intentionally not advertised yet because this branch has ToolCall subprocess execution through the UI, not the FlatLine DB command queue.


## Script runner

JavaScript files in `scripts/*.js` appear in the top-left script list at startup. Double-click a script to run it in the live Grok page. The runner captures `console.log/info/warn/error`, return values, and exceptions into the Debug Output pane.

Starter scripts:
- `scripts/alert_hello.js`
- `scripts/console_eggplant.js`

## v4 Grok address/search bar

The live Grok pane now has a `URL/Search` bar above it. Paste any `grok.com`, `accounts.x.ai`, or Google OAuth URL there and press Enter/Go to navigate inside the same `QWebEngineProfile` session. The chat/debug logs now shorten long OAuth query strings as `?…` so the UI does not flood itself with huge login URLs.


## Claude detector CLI

The active static detector suite lives in `vendor/claude/`.

Run the full suite and exit before Qt/WebEngine loads:

```powershell
python start.py --monkey
```

Run selected detectors:

```powershell
python start.py --claude-detectors raw-sql swallowed-exceptions
python start.py --lifecycle-bypass
python start.py --monkeypatch
python start.py --raw-sql
python start.py --recursion
python start.py --redundant
python start.py --swallowed
```

Help/man:

```powershell
python start.py --help
python start.py man
python start.py --man
python start.py --debugger-menu
```

Reports:

- Combined latest: `reports/claude_detectors_report_latest.txt`
- Legacy alias latest: `reports/clod_monkey_report_latest.txt`
- Per-detector logs: `logs/lifecyclebypass.txt`, `logs/monkeypatch.txt`, `logs/rawsql.txt`, `logs/recursion.txt`, `logs/redundant.txt`, `logs/swallowed.txt`

The old `/vendor/clod` runner is now only a compatibility wrapper. The duplicate old monkey-patch detector was removed; the canonical monkey-patch detector is `vendor/claude/monkeypatchdetect.py`.

## FlatLine / Claude detector bundle

The latest uploaded Claude/FlatLine detector bundle is vendored in `vendor/claude` and the exact zip is kept at `vendor/claude/claude_latest_flatline_debugger_20260502.zip`.

Useful routes:

```bash
python start.py --debugger-query-surfaces
python start.py --debugger-vardump
python start.py --health
python start.py --certify
python start.py --detector-selftest
python start.py --phase-hooks
python start.py --nonconform
python start.py --comport
python start.py --manual
```

Whitepaper-driven launcher safeguards now include `Tasks.taskkill(pid)`, Windows command-line-targeted stale SuperGrok process cleanup before relaunch, matching `auto.ps1` watcher cleanup via `taskkill /PID <pid> /T /F`, and detector process-tree cleanup when a vendored detector times out. Use `--no-stale-process-kill` to bypass the `start.py` cleanup path.

Current detector note: `--comport` passes the project-specific architecture contract; full `--certify` currently exits 1 because `nonconform` still contains TrioDesktop/CutiePy required-symbol checks and `redundant` is advisory/noisy on Qt/SQLAlchemy source shapes.

## Resident Grok bridge service and command-line chat

Grok is slow to load and can require login refreshes, so the launcher now supports a resident local bridge service. Keep the Qt/WebEngine Grok session alive, then use command-line calls against it:

```powershell
python start.py --serve-bridge --offscreen
python start.py --chat grok "hello from the CLI" --offscreen
```

`--chat` first tries the resident service on `127.0.0.1:8767`. If it is not running, `start.py` auto-starts `--serve-bridge` and retries. To check the resident service:

```powershell
python start.py --bridge-status
```

If Grok needs login again, start the bridge visibly once, log in, and leave it running:

```powershell
python start.py --serve-bridge --show-bridge
```

Then the hidden/offscreen calls can reuse the same persisted profile:

```powershell
python start.py --chat grok "message" --offscreen
```

Advanced flags:

```powershell
python start.py --chat grok deployment "message" --chat-timeout 300 --bridge-port 8767
python start.py --chat grok "message" --no-chat-service-start
```

The FlatLine surface probe now includes `bridge-service` and `chat` in addition to `heartbeat poll vardump accepts-proxy`.


### Reloadable resident Grok bridge

The resident bridge is now source-aware. `--serve-bridge` replaces an older bridge on the same port before binding, and `--chat grok "message" --offscreen` checks whether the live service loaded the same `start.py` and `supergrok_bridge/*.py` files currently on disk. If the source signature is stale or missing, the launcher shuts down the old bridge, starts a fresh one, and then sends the chat request.

Useful commands:

```bash
python start.py --bridge-status
python start.py --chat grok "hello" --offscreen
python start.py --chat grok "hello" --offscreen --force-bridge-restart
```

Use `--no-bridge-source-check` only for debugging when you intentionally want to talk to an older resident process.

## CLI chat hardening and logs

The command-line bridge is designed for other apps to call directly:

```bash
python start.py --serve-bridge --offscreen
python start.py --chat grok "Hello from another app" --offscreen
```

On success, `--chat` prints only Grok's answer to stdout. Operational details go to stderr and `debug.log`.

The resident bridge now probes the Grok DOM before sending: prompt box, send button, enabled state, and send/arrow SVG/icon shape. It retries, reloads once, retries again, and then shows the full bridge window when Grok needs login/captcha/error-page repair.

Debug files:

- `debug.log` — general trace/warnings/print tee.
- `session.log` — Grok browser request/response traffic captured from fetch/XHR, including URL, redacted headers, request body, response headers/body, and timing.

## 2026-05-03 relaunch hardening

The resident Grok bridge is reload-safe: new `--chat` calls compare the running service source signature with the current `start.py`/`supergrok_bridge/*.py` files and restart stale services before sending. Windows process cleanup now protects the current process ancestry so a service spawned by `--chat` cannot kill its waiting CLI parent. `taskkill /PID <pid> /T /F` output is always printed for stale cleanup. See `docs/GROK_BRIDGE_RELAUNCH_HARDENING_20260503.md`.
## Grok CLI Hidden Service Mode

`python start.py --chat "Hello"` starts or reuses the resident bridge invisibly by default. The child service is launched with `--offscreen` even when the caller omits it, so the command behaves like a provider CLI route: stdout is reserved for the Grok answer, and `--show-bridge` is the explicit login/repair mode.

## ChatGPT Browser Mode

The bridge now supports ChatGPT as an alternate browser target:

```powershell
python start.py --chatgpt "Hello"
python start.py --chatgtp "Hello"
python start.py --chat chatgpt "Hello"
python start.py --chatgpt "Summarize this" --file README.md
```

For manual login or repair, run:

```powershell
python start.py --serve-bridge --target chatgpt --url https://chatgpt.com/ --show-bridge
```

See `docs/CHATGPT_MODE_20260505.md` for details.

## ChatGPT login bridge mode

ChatGPT web mode uses a real persistent bridge browser session. First run one of these and log in normally:

```powershell
python start.py --gpt
python start.py --chatgpt
python start.py --chatgtp
```

Then leave the bridge running and send CLI messages:

```powershell
python start.py --chatgpt "Hello"
python start.py --gpt "Hello"
python start.py --chat chatgpt "Hello"
```

ChatGPT cookies are saved under `data/chatgpt_profile/`; Grok remains under `data/grok_profile/`. Use `--offscreen` only after the ChatGPT session is already logged in.

### ChatGPT / GTP bridge aliases

Use a visible login bridge once:

```powershell
python start.py --gpt
# or
python start.py --gtp
```

After logging in, these all route to ChatGPT, not Grok:

```powershell
python start.py --gpt --chat "Hello"
python start.py --gtp --chat "Hello"
python start.py --chatgpt --chat "Hello"
python start.py --chatgtp --chat "Hello"
python start.py --chatgpt "Hello"
python start.py --chatgtp "Hello"
python start.py --chat chatgpt "Hello"
```

The intentionally misspelled `--gtp` and `--chatgtp` aliases are kept because they are easy to type while testing. ChatGPT uses `data/chatgpt_profile/`; Grok uses `data/grok_profile/`.

### ChatGPT bridge response capture note

`--gpt`, `--gtp`, `--chatgpt`, and `--chatgtp` route to the persistent ChatGPT bridge profile at `data/chatgpt_profile/`. If ChatGPT changes its React DOM or network stream again, run with `--debug`: request/response payloads are mirrored into `debug.log`, and ChatGPT DOM polling now records candidate message previews plus periodic HTML snapshots for selector repair.

## ChatGPT Qt trusted input fallback

ChatGPT mode now avoids the old `form.requestSubmit()` fallback because ChatGPT's React composer can clear or ignore synthetic submits without creating a conversation. If the normal DOM send path cannot enable the real ChatGPT send button, the visible bridge attempts a Qt trusted-input fallback: focus composer, paste the prompt, and press Enter through Qt key events.

Preferred test command:

```powershell
python start.py --gtp --debug --chat "who dis?"
```

For first login or selector repair, run the visible bridge:

```powershell
python start.py --gtp
```

