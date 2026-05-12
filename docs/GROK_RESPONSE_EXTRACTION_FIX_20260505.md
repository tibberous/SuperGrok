# Grok CLI response extraction hardening — 2026-05-05

Problem observed from `python start.py --chat --debug "Hello"`:

- The bridge service started.
- Grok loaded.
- DOM surfaces became ready.
- The prompt was sent.
- The CLI returned `Refer to the following content:`.

That proved the command-line path was finally reaching Grok, but the DOM extractor accepted a generic wrapper/sidebar/history title as the answer instead of waiting for the real assistant text or the captured `/responses` network stream.

Fixes in this pass:

1. Generic answer candidates are rejected:
   - `Refer to the following content:`
   - `What do you want to know?`
   - `New Chat`
   - `Fast`, `Private`, `Imagine`
   - sign-in/signup chrome
   - Grok terms/footer chrome

2. DOM message extraction now prefers the `main` content area and skips common navigation/sidebar/history/button/link chrome.

3. The CLI job ignores legacy generic DOM `complete` events and keeps waiting for a better DOM answer or the network-stream answer.

4. Network-stream completions are also filtered so a generic wrapper cannot prematurely complete the CLI request.

Expected behavior:

```powershell
python start.py --force-bridge-restart --chat --debug "Hello"
```

The CLI should no longer accept `Refer to the following content:` as a successful answer. If Grok sends a real response through DOM or `/responses`, that text should be printed. If the bridge cannot find a real answer, debug output should continue showing the active stage instead of pretending the wrapper text was correct.
