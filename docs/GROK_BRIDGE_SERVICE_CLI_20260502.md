# SuperGrok Bridge Service CLI Notes — 2026-05-02

## Goal

SuperGrok Bridge now supports a resident local command service so Grok can stay loaded, warmed up, and logged in between command-line calls.

The intended fast path is:

```powershell
python start.py --serve-bridge --offscreen
python start.py --chat grok "message" --offscreen
```

The one-shot client command will first try the resident service at `127.0.0.1:8767`. If the service is not running, it automatically starts:

```powershell
python start.py --serve-bridge --bridge-port 8767 --offscreen
```

Then it retries the chat request against the service.

## Login behavior

Grok can take a long time to load and may require a login refresh. If the hidden/offscreen service cannot find the prompt box, run the service visibly once:

```powershell
python start.py --serve-bridge --show-bridge
```

Log into Grok in that persistent profile, then keep the service running. Future CLI requests can use:

```powershell
python start.py --chat grok "message" --offscreen
```

The Grok profile is still persisted under `data/grok_profile` unless `--profile-dir` is supplied.

## CLI routes added

```text
--serve-bridge / --bridge-service
--chat grok "message"
--chat grok deployment "message"
--bridge-status
--bridge-port N
--chat-timeout N
--no-chat-service-start
--show-bridge
```

`--debugger-query-surfaces` now advertises:

```text
heartbeat poll vardump accepts-proxy bridge-service chat
```

## Architecture

`start.py` is now the lightweight client/launcher. It parses `--chat`, sends a JSON-line request to the resident service, and prints only the answer on success.

`supergrok_bridge/app.py` owns the actual browser process. The resident service is implemented with Qt `QTcpServer`, not a Python background thread, so Grok DOM operations stay on the Qt main loop. The service accepts JSON-line requests:

```json
{"action":"status"}
{"action":"chat","target":"grok","message":"hello","timeoutSeconds":240}
{"action":"shutdown"}
```

The service reuses the live `QWebEnginePage`, injects the same DOM-send script used by the GrokChat dialog, polls for the visible response, and writes a JSON-line response back to the CLI client.

## What is proven in this container

- AST parse passed for all Python files.
- `py_compile` passed for all Python files.
- `python start.py --debugger-query-surfaces` prints the new service/chat surfaces.
- `python start.py --debugger-vardump` reports the bridge service port.
- `python start.py --comport --detector-timeout 25` exits 0 with no findings.
- `python start.py --chat grok "hi" --no-chat-service-start` cleanly reports service unavailable instead of launching the GUI.

## Not proven here

This container does not have PySide6 or SQLAlchemy installed, so a full QtWebEngine offscreen service run was not proven here. The runtime dependency installer remains in `start.py` for the target machine.
