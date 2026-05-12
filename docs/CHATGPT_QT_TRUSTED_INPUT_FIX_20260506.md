# ChatGPT Qt Trusted Input Fallback Fix — 2026-05-06

## Problem

The ChatGPT bridge loaded the authenticated ChatGPT page and found the composer, but the browser automation still timed out after 240 seconds. The logs showed that the editor was filled, the old submit path reported `domSendStarted=true`, and then polling continued with no assistant candidates and no successful conversation response.

The important failure was that `form.requestSubmit()` could clear or disturb the composer without causing ChatGPT's React application to accept the prompt as a real user submit. That made the bridge look active while no real ChatGPT conversation was created.

## Fix

This pass separates ChatGPT input from the Grok submit path more aggressively:

- Removes the ChatGPT `form.requestSubmit()` fallback from the DOM bridge path.
- Keeps retrying the real ChatGPT send button longer before declaring failure.
- Re-fills the composer during ChatGPT submit retries when React has not enabled the send button.
- Adds stronger React-aware textarea events in `assets/js/grok_dom_bridge.js`, including native value setters, `_valueTracker` reset, `beforeinput`, `input`, `change`, `compositionend`, and key events.
- Adds a guarded Qt-level trusted-input fallback using `PySide6.QtTest.QTest`:
  - first checks whether the DOM send button was actually accepted so it does not double-send,
  - focuses the ChatGPT composer from JavaScript,
  - activates the bridge WebEngine view,
  - places the prompt into the clipboard,
  - sends Ctrl+A, Ctrl+V, and Enter through Qt key events,
  - restores the clipboard afterward.

## Operational note

For ChatGPT mode, the visible bridge is still preferred. The Qt trusted-input fallback is designed for the visible ChatGPT bridge path where the user is logged in and the page can accept normal keyboard input.

## Expected behavior

Run:

```powershell
python start.py --gtp --debug --chat "who dis?"
```

If the JavaScript path cannot enable ChatGPT's send button, the bridge waits briefly, probes whether a real DOM button submit was accepted, and then attempts the Qt trusted paste/Enter fallback instead of silently relying on `requestSubmit`. If it still fails, the log should clearly show whether the trusted fallback ran and what the DOM probe saw.
