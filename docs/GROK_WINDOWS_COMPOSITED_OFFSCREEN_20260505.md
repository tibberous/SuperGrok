# Grok Windows Composited Offscreen Fix - 2026-05-05

## Problem

`python start.py --chat "Hello"` correctly started the resident bridge without opening the full app, but Grok still returned `prompt box not found`.

The diagnostic payload showed a key clue: the page URL and title were `https://grok.com/` / `Grok`, but `bodyPreview` was mostly boot/config text. That means Chromium had loaded scripts, but the visible layout/composer had not been composited. In this state `document.body.innerText` is effectively empty and the bridge falls back to `textContent`, so visible selector probes cannot find the prompt box.

## Fix

For Windows `--offscreen-mode offscreen-window`, the bridge now uses a real native Chromium/QtWebEngine surface on the desktop, makes it nearly transparent, and sends it behind normal windows. It no longer moves the window to `32000,32000` for Windows service mode.

Why: true hidden/minimized/far-offscreen windows can prevent QtWebEngine/Chromium from producing a normal rendered layout. The Grok DOM exists, but the app shell/composer may not hydrate into visible text boxes/buttons.

## Behavior

- `python start.py --chat "Hello"` still auto-starts the resident service.
- The resident service still does not show the normal bridge UI.
- The hidden service uses an almost transparent composited window so Grok can render a real browser page.
- If repair is needed, `--show-bridge` or automatic repair reveal restores opacity and normal position.

## Debug improvement

The surface probe now reports:

- `layoutRendered`
- `innerTextLength`
- `textContentLength`
- viewport dimensions
- `promptCandidateCount`
- `buttonCandidateCount`

If `innerTextLength` is near zero while `textContentLength` is large, the problem is not login or selector failure. It is a rendering/compositing problem.

## Linux note

The vendored FlatLine reference supports real X11 offscreen display engines: `xvfb`, `xdummy`, and `xpra`. Those are virtual display servers. They are useful on Linux, but on Windows the closest practical equivalent is a real native QtWebEngine surface kept effectively invisible.
