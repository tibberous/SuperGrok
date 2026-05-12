# Grok CLI Send Button Selector Hardening — 2026-05-03

The Windows test showed the Grok page was loaded and logged in, and the prompt box was visible, but the pre-send DOM probe selected the `aria-label="Attach"` button as the best form button. That made `python start.py --chat "Test" --offscreen` fail before the bridge typed the message.

This pass changes the bridge behavior:

- The readiness probe now gates on whether the prompt/editor is available and the page does not look like login/captcha/auth.
- The readiness probe still logs all send-button diagnostics, but it no longer fails just because the send arrow is not visible before text is typed.
- After the message is inserted, the send script re-probes the form for up to ~10 seconds while Grok swaps/enables the real send control.
- Known non-send controls are scored down and ignored, including Attach, Upload/File, Model Select, Dictation, Voice/Microphone, Sidebar, Search, Project, History, Menu, and Settings.
- A trustworthy send button is preferred when it is in the same form, type `submit`, has `data-testid="chat-submit"`, or has send/submit/arrow/up wording.
- If no trustworthy send button appears after retries, the bridge emits a warning and tries an Enter-key fallback rather than clicking Attach.
- `debug.log` records the probe, candidates, ignored non-send buttons, and final fallback path.
- `session.log` continues to record chat request/response payloads.

The important rule is: do not click a generic visible `form button` before text is typed. Grok’s visible form buttons can be Attach/Model/Dictation before the actual send surface appears.
