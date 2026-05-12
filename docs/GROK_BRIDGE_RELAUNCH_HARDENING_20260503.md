# SuperGrok bridge relaunch hardening — 2026-05-03

Goal: keep the Grok WebEngine bridge warm as a resident service, but make every new CLI/app run safe to relaunch, update, and debug.

## CLI contract

```powershell
python start.py --serve-bridge --offscreen
python start.py --chat grok "message" --offscreen
python start.py --bridge-status
```

`--chat` talks to the resident `127.0.0.1:8767` bridge and prints only the Grok answer to stdout on success. Diagnostics, warnings, repair hints, taskkill output, and bridge status go to stderr/debug.log.

## Reload safety

The resident service captures a source signature at startup for `start.py` and `supergrok_bridge/*.py`. The CLI compares that signature against the current files before sending a chat. If the service is stale, `--chat` asks it to shut down, waits, force-kills by PID if needed, starts a fresh resident bridge, then sends the prompt.

## Process cleanup hardening

Windows stale cleanup remains command-line-targeted. It never kills by process name alone. This pass added protected PID handling so a new `--serve-bridge` instance cannot accidentally kill the current `--chat` parent process that is waiting for it. The protected set includes the current PID plus the current process ancestry where Windows exposes it through CIM.

`taskkill /PID <pid> /T /F` output is now always printed with success/failure status for stale cleanup, so the console does not stop at a silent `taskkill` command.

## DOM hardening

The CLI chat job checks Grok surfaces before sending:

- prompt/contenteditable/textarea surface exists
- send button exists
- send button is enabled
- send button looks like send/submit/up-arrow
- send button contains an SVG/icon when expected

If surfaces are missing, it retries, reloads Grok once, retries again, then reveals the full bridge window for login/captcha/error repair.

## Logging

`debug.log` receives operational logs, warnings, DOM probes, retries, taskkill results, and bridge command traces.

`session.log` receives Grok/network/session data only: Qt request metadata, JavaScript fetch/XHR captures, and the CLI chat request/answer record. This makes it possible to debug what was sent to Grok and what came back without digging through general startup noise.
