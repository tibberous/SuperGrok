# Grok CLI chat hidden service fix - 2026-05-05

Goal: `python start.py --chat "Hello"` should behave like a provider CLI call: send a message through the resident Grok bridge and print only the Grok answer to stdout. It must not open the normal SuperGrok UI just because the user omitted `--offscreen`.

Fix:

- Chat-spawned bridge services now force `--offscreen` unless `--show-bridge` is explicitly provided.
- On Windows, chat-spawned service mode defaults to `--offscreen-mode offscreen-window`, which keeps a real QtWebEngine/Chromium surface but moves it off-screen instead of using fragile `QT_QPA_PLATFORM=offscreen`.
- The bridge child is launched with Windows hidden-process startup flags (`CREATE_NO_WINDOW` plus hidden `STARTUPINFO`) so no helper console flashes.
- `--show-bridge` remains the explicit repair/login path.

Useful commands:

```powershell
python start.py --chat "Hello"
python start.py --chat grok "Hello"
python start.py --chat "Hello" --debug
python start.py --serve-bridge --show-bridge
python start.py --bridge-status
```

Latest FlatLine/Xdummy note:

The vendored FlatLine reference supports `xvfb`, `xdummy`, and `xpra` capture engines. Those are Linux/X11 virtual-display modes. They are real display/window surfaces, not fake Qt offscreen widgets, and they are useful when SuperGrok is run under Linux or a FlatLine parent that owns the virtual display. On native Windows the closest reliable equivalent is the hidden/off-screen native QtWebEngine window surface.
