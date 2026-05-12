# SuperGrok Bridge CLI Hardening Pass — 2026-05-03

Goal: make `python start.py --chat grok "message" --offscreen` usable as a plain command-line bridge for other applications while keeping the resident Grok browser warm, logged in, and debuggable.

## CLI contract

```powershell
python start.py --serve-bridge --offscreen
python start.py --chat grok "Write a tiny haiku about logs" --offscreen
```

`--chat` prints only Grok's answer to stdout on success. Operational warnings/errors go to stderr and `debug.log`.

## Resident service behavior

The bridge service stays open as a Qt/WebEngine process so Grok can finish login/authentication/redirects once. CLI calls connect to the service over localhost JSON-lines and reuse the already-loaded page.

The service still checks the loaded source signature. If the running service loaded old Python files, `--chat` restarts it before sending the prompt.

## DOM hardening

A CLI chat now performs a surface probe before sending:

1. Check for a visible prompt/text area.
2. Check for a visible send button.
3. Check whether the send button is enabled.
4. Check whether the send button looks like a send/arrow button and contains an SVG/icon.
5. Warn loudly when jQuery is absent; native DOM probing is used instead.
6. Retry several times because Grok can be slow.
7. Reload the Grok page once if the surfaces still are not ready.
8. Retry again after reload.
9. If surfaces are still missing, show the full bridge window so the user can log in, solve captcha, or see the error page.

The send path itself rechecks the prompt box and button after filling the prompt, so it does not blindly click a random button.

## Logs

Two root logs are intentionally created:

- `debug.log` — general operational trace, warnings, lifecycle output, DOM probe failures, button state warnings, bridge command request/response summaries, print/stdout/stderr tee.
- `session.log` — Grok browser network traffic only, captured from JavaScript fetch/XMLHttpRequest hooks. Entries include URL, method, redacted headers, request body, response headers, response body, status, timing, and page context.

Sensitive header/key names are redacted in the JavaScript capture layer by default.

## Human repair fallback

When Grok appears to be on a login page, auth redirect, captcha, blank page, or a changed DOM, the service calls `revealForHumanRepair(...)` and returns a CLI error with a hint:

```powershell
python start.py --serve-bridge --show-bridge
```

After the user logs into Grok in the persistent profile, regular offscreen `--chat` calls should reuse that authenticated session.
