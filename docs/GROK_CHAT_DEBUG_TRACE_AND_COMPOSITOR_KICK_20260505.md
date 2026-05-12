# Grok CLI Chat Debug Trace and Compositor Kick — 2026-05-05

## Why this pass exists

`python start.py --chat "Hello"` could look like it was hanging because the CLI accepted an async bridge job and then silently polled while the resident QtWebEngine page reported a loaded Grok document but no rendered/composited layout.

The uploaded debug log showed:

- The resident bridge service was listening on `127.0.0.1:8767`.
- The chat job was accepted and repeatedly polled as pending.
- The page reached `https://grok.com/` but `innerTextLength=0` while `textContentLength` was very large.
- The DOM probe warned that the Grok layout was not rendered/composited, so no prompt box or send button could be safely selected.

## Changes

### CLI-side tracing

When launched with `--debug`, `start.py --chat` now prints explicit bridge-client phases to stderr:

- parsed chat command
- ensuring resident bridge service
- service ready / accepted / stale
- sending chat request to the bridge
- chat acknowledgement
- chat-result polling
- active bridge progress when available
- final response or timeout

Normal `--chat` remains plain-text-first: successful Grok answers still print only the answer to stdout.

### Bridge-side progress

The resident bridge now reports active chat progress in `chat-result` pending responses and in `--bridge-status`, including:

- active stage
- reason
- elapsed seconds
- surface probe attempt count
- reload attempt count
- compositor kick attempt count
- layout/render status
- prompt/send button discovery status
- current URL

### Compositor kick

If the Grok document is loaded but not visibly composited, the bridge now tries a compositor kick:

- keeps a real QtWebEngine surface
- restores full opacity instead of near-transparent opacity
- lowers the window behind normal windows
- resizes to a large enough browser surface
- repaints/focuses the WebEngine widget without activating the repair window

This is intended to avoid the previous state where Chromium loaded scripts/config but React never painted the composer.

## Test commands

```powershell
python start.py --force-bridge-restart --chat "Hello" --debug
python start.py --bridge-status
```

Normal usage:

```powershell
python start.py --chat "Hello"
```
