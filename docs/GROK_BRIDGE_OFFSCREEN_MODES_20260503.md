# SuperGrok Bridge offscreen modes — 2026-05-03

Goal: keep Grok logged in and warm as a resident bridge while allowing command-line clients to send a prompt and receive plain text.

## Recommended Windows mode

Use:

```powershell
python start.py --serve-bridge --offscreen --offscreen-mode offscreen-window
python start.py --chat "Hello" --offscreen
```

On Windows, `--offscreen` now defaults to `offscreen-window`, which creates a real native QtWebEngine/Chromium surface and moves it far off the visible desktop. This is closer to Xdummy in spirit than `QT_QPA_PLATFORM=offscreen`: Chromium still has a real window/surface, but the user does not see it.

## Modes

- `offscreen-window`: real native Qt window moved to coordinates `(32000, 32000)`. Best Windows default for Grok login/DOM reliability.
- `hidden`: show then hide. Clean, but some WebEngine pages throttle or fail to paint when fully hidden.
- `minimized`: native minimized window. Useful fallback if offscreen coordinates behave oddly.
- `qt`: forces `QT_QPA_PLATFORM=offscreen`. Good for smoke tests, but not recommended for Grok login/DOM automation.
- `xvfb`, `xdummy`, `xpra`: Linux/FlatLine capture names from the latest FlatLine reference. The full FlatLine parent owns actually starting those X11 display servers.

## Repair path

If the DOM probe cannot find the prompt box or send button after retries/reload, the bridge changes itself back to visible mode, moves to `(120,120)`, and shows the window so the user can log in or fix a captcha/error page.
