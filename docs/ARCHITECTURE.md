# Architecture

## Goal

Embed Grok.com in a Qt WebEngine pane and control it from a left-side chat shell using injected DOM JavaScript.

## Main objects

- `start.py`: launcher, remote debugging switch, Flatline placeholder hook.
- `supergrok_bridge.app.SuperGrokBridgeWindow`: main split-pane window.
- `ChatPane`: left-side local chat transcript web engine plus prompt/send row.
- `ManagedWebView`: common QWebEngineView wrapper with right-click Copy, View Source, and DevTools actions.
- `GrokPageController`: owns DOM automation snippets and page callbacks for the live Grok page.
- `SourceDialog`: Prism-highlighted source viewer with Copy, Copy All, and Save As.

## Layout

```text
+-----------------------------------+-------------------------------------------+
| Left: local QWebEngine chat pane  | Right: live QWebEngine grok.com pane      |
|                                   |                                           |
| [messages rendered as HTML]       | [real website / logged-in browser state]  |
|                                   |                                           |
| prompt textbox        [Send]      |                                           |
+-----------------------------------+-------------------------------------------+
```

## Automation strategy

Use Qt WebEngine first, not Selenium/Playwright, because the browser is embedded. Selenium/Playwright are better when they own an external browser instance.

The DOM bridge uses fallback selectors instead of hard-coded CSS classes:

- `textarea`
- `[contenteditable="true"]`
- `[role="textbox"]`
- `button[aria-label*="send" i]`
- keyboard Enter fallback

## DevTools

Qt WebEngine is Chromium-based. `start.py` sets `QTWEBENGINE_REMOTE_DEBUGGING` when `--remote-debug-port` is used, and the app also exposes embedded DevTools docks per inspected page when supported by the installed PySide6 build.

Right-click either the chat pane or the live Grok pane and choose `Dev Inspect with Chromium DevTools` to open the inspector. The toolbar also has a `Remote DevTools URL` action for `http://127.0.0.1:<port>`.

## jQuery boundary

jQuery is loaded only into the left local chat page. The live Grok page is intentionally kept dependency-free so the app does not inject jQuery or other third-party libraries into Grok's production page.

## ToolCall fence rule

ToolCall parsing intentionally ignores single-backtick inline text. Grok must emit explicit triple-backtick command blocks for commands to be considered:

```toolcall
 dir
```

Accepted fence labels are blank, `toolcall`, `tool`, `bash`, `sh`, `shell`, `cmd`, `bat`, `powershell`, `ps1`, and `zsh`. Other code fences such as `python` are ignored.

## Debugger surfaces

The codebot pass added a minimal FlatLine child-surface contract without importing Qt for probe mode. `python start.py --debugger-query-surfaces` prints `heartbeat poll vardump accepts-proxy` and exits. Runtime heartbeat rows are written to `data/supergrok_bridge_debugger.sqlite3` by `SuperGrokBridgeWindow.emitDebuggerHeartbeatSurface()` on a one-second timer. The heartbeat row stores a compact vardump plus the recent process-table snapshot so the launcher can tell whether Qt is alive and whether the ToolCall watchdog is still polling.

The app does not yet advertise `debugger-exec-command` or `debugger-cron-command`; those should only be exposed after a real DB command processor is added.
