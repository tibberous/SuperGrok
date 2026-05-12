# Grok CLI Chat Service Hang Fix - 2026-05-05

## Problem

`python start.py --chat "Hello"` could appear to hang even after the resident bridge service started. The debug log showed two separate failure modes:

1. A visible/debug `start.py --debug` process from the watcher could run broad stale-process cleanup and kill the resident `--serve-bridge` process tree while the CLI was waiting for it.
2. Long-running chat replies held a `QTcpSocket` open until Grok completed. PySide could delete that socket before the async Grok response returned, producing `RuntimeError: libshiboken: Internal C++ object (PySide6.QtNetwork.QTcpSocket) already deleted.`
3. The JavaScript button filter used escaped word-boundary regexes, so `aria-label="Attach"` could accidentally score as a send button.

## Fix

- Broad stale cleanup is no longer automatic on normal/debug app starts.
- Bridge replacement is role-scoped and only targets Python processes containing `--serve-bridge`.
- PowerShell helper processes are excluded from stale-process matching.
- `--chat` now submits async jobs to the resident bridge and polls `chat-result` instead of holding one long-lived socket open.
- The bridge service stores chat results by job id.
- Direct socket chat remains backward-compatible but is guarded against deleted sockets.
- JavaScript button exclusion regexes now use real word boundaries, so Attach/Upload/Model/Voice controls are excluded correctly.

## Expected CLI behavior

```powershell
python start.py --chat "Hello"
```

The command should keep stdout reserved for Grok's plain-text response. Debug and warning information belongs in `debug.log` and stderr.
