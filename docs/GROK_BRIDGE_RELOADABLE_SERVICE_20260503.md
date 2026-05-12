# Grok Bridge Reloadable Resident Service

## Goal

`python start.py --chat grok "message" --offscreen` should benefit from a warm, logged-in Grok browser process without accidentally talking to an old Python process after the repo was updated.

## Behavior added

- The resident bridge captures a loaded-source signature at process startup.
- The signature covers `start.py` and every Python file under `supergrok_bridge/`.
- `python start.py --bridge-status` shows both the resident service signature and the current on-disk signature, plus `sourceMatchesCurrent`.
- `python start.py --chat grok "message" --offscreen` checks the resident bridge before sending.
- If the signatures differ, or if an older bridge does not report a signature, the launcher shuts the old bridge down and starts a fresh service before sending.
- `python start.py --serve-bridge --offscreen` also replaces any old resident bridge on the same port before it binds.

## Useful commands

```powershell
python start.py --serve-bridge --offscreen
python start.py --chat grok "hello" --offscreen
python start.py --bridge-status
python start.py --chat grok "hello" --offscreen --force-bridge-restart
```

## Escape hatches

```powershell
python start.py --chat grok "hello" --offscreen --no-bridge-source-check
python start.py --chat grok "hello" --offscreen --no-chat-service-start
python start.py --serve-bridge --offscreen --no-stale-process-kill
```

`--no-bridge-source-check` is only for debugging. Normal use should keep the source check enabled so code updates reload the resident service.

## Relaunch rule

The bridge may stay warm for login/session speed, but it is not trusted blindly. The client has to prove the resident server loaded the same source files that are currently on disk. If it cannot prove that, it replaces the resident bridge.
