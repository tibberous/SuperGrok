# SuperGrok full request/response logging hardening — 2026-05-05

This pass makes the main debug log useful when the Grok bridge fails.

Changes:

- `start.py` now resets `debug.log`, `session.log`, `data/traffic.log`, and `logs/bridge_service.log` per run unless `SUPERGROK_KEEP_LOGS=1` is set.
- `start.py` now logs every bridge client request, response, timeout, socket error, received raw text, and traceback into `debug.log` and `session.log`.
- `supergrok_bridge/app.py` now mirrors every browser/network traffic event into `debug.log` in addition to `data/traffic.log`.
- The bridge command server now logs every client-to-bridge command and bridge-to-client response with full JSON body content.
- Qt WebEngine request metadata now attempts to capture request headers when the PySide/Qt build exposes them.
- JavaScript fetch/XHR hooks now capture request/response bodies, response headers, fetch/XHR errors, global `window.error`, and `unhandledrejection` events.

Notes:

- Browser-restricted headers such as raw Cookie and Authorization are still redacted or inaccessible by design.
- Qt's interceptor generally cannot expose response bodies. The JavaScript fetch/XHR hook captures bodies when the request flows through page-level fetch/XHR.
