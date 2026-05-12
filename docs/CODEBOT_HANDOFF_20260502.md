# Codebot handoff - 2026-05-02

## Scope

SuperGrok was staged as the current working version in `/grok`. The uploaded archive is no longer present in `/mnt/data` or `/grok`.

## Start.py / FlatLine surface hook

`start.py` now supports:

```powershell
python start.py --debugger-query-surfaces
python start.py --debugger-vardump
python start.py --debugger-menu
```

Advertised child surfaces:

```text
heartbeat poll vardump accepts-proxy
```

`debugger-exec-command` and `debugger-cron-command` are intentionally not advertised yet. The app already has UI ToolCall subprocess execution and process watchdogs, but it does not yet have a FlatLine DB command-queue processor for those two DB-backed surfaces.

## Heartbeat implementation

`supergrok_bridge/app.py` now has a SQLAlchemy-backed heartbeat database at:

```text
data/supergrok_bridge_debugger.sqlite3
```

`SuperGrokBridgeWindow.emitDebuggerHeartbeatSurface()` writes one heartbeat per second while the Qt event loop/window is alive. Heartbeat rows include:

- app name / pid
- current Grok URL
- debug/runtime flags
- advertised surfaces
- count of running ToolCall processes
- recent process-table snapshot

## Poll surface

The project already had two polling paths:

- `GrokChatDialog.pollTimer` / `pollChatEvent()` for the Grok chat modal response polling.
- `ToolCallManager.watchdogTimer` / `pollProcessWatchdog()` for subprocess TTL supervision and Windows `taskkill /T /F` timeout handling.

The new heartbeat vardump includes process-watchdog state so the launcher has durable proof that polling remains alive.

## Dependency launch hook

`start.py` now checks for PySide6 and SQLAlchemy before importing the Qt app. If missing, it runs:

```powershell
python -m pip install -r requirements.txt
```

On Linux it appends `--break-system-packages` for PEP 668 environments. `--no-deps` disables this and raises an explicit missing-dependency error instead.

## Validation performed

- The Claude detector runner now starts each detector in its own process group/session and kills the detector process tree on timeout, which fixed an intermittent self-test hang around the final detector route.

- AST parse passed for all Python files.
- `py_compile` passed for `start.py`, `supergrok_bridge/app.py`, and `supergrok_bridge/exception_log.py`.
- `python start.py --debugger-query-surfaces` prints `heartbeat poll vardump accepts-proxy` and exits without importing Qt.
- `python start.py --debugger-vardump` prints JSON and exits without importing Qt.
- `python start.py --detector-selftest --detector-timeout 4` exits 0; all detector routes execute.
- `python start.py --certify --detector-timeout 25` exits 1 right now because the newly vendored `nonconform` route contains TrioDesktop/CutiePy required-symbol expectations and `redundant` is noisy on legitimate Qt/SQLAlchemy setup shapes. The app-specific `--comport` route passes and keeps the banned-thread/process-contract check active.

## Whitepaper follow-up added

After reading `locked_file_process_cleanup_whitepaper.txt`, `auto.ps1` was updated too: watcher restarts now call `Stop-StaleSuperGrokProcesses` before launch and use `taskkill /PID <pid> /T /F` via `Stop-ProcessTreeByPid` when stopping active project-owned children.

## Not proven in this container

Full Qt runtime launch was not proven because this container does not currently have PySide6 or SQLAlchemy installed. `start.py` now has the dependency install path, but the sandbox run used `--debugger-query-surfaces` / static validation rather than a full browser launch.
