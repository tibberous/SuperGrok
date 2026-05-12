# Grok CLI Chat Debug Service Startup Trace Fix — 2026-05-05

## Problem

`python start.py --chat --debug "Hello"` could appear to hang while the client waited for the resident bridge service to open `127.0.0.1:8767`.

The previous debug output showed repeated connection-refused messages but did not prove whether the child service was still alive, had crashed before binding, or was still starting Qt/WebEngine.

## Fix

The chat launcher now monitors the spawned `--serve-bridge` process while waiting for the port to become ready.

When `--debug` is enabled, the client now prints:

- service wait elapsed seconds
- bridge service PID
- whether the service process is still alive
- connection failure reason
- `logs/bridge_service.log` tail while waiting
- early child exit code if the service crashes before binding
- final `bridge_service.log` tail on timeout

## Result

A real startup failure now looks like a diagnosed error instead of a dead CLI hang. For example, if PySide6 or SQLAlchemy is missing, the client prints the child traceback from `bridge_service.log` and exits cleanly instead of polling forever.

## Recommended debug command

```powershell
python start.py --force-bridge-restart --chat --debug "Hello"
```

Normal successful use remains:

```powershell
python start.py --chat "Hello"
```
