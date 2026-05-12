# ChatGPT Response Capture Fix — 2026-05-05

This pass fixes the first real ChatGPT browser-bridge failure after target routing began working.

## Symptom

`python start.py --gtp --debug --chat "who dis?"` correctly selected:

- `target=chatgpt`
- `url=https://chatgpt.com/`
- visible bridge mode
- prompt found
- send button found
- DOM send started

…but the CLI kept polling forever because response capture was still too Grok-shaped.

## Fixes

- Expanded ChatGPT response stream detection beyond the old `/backend-api/conversation` assumption.
- Treat ChatGPT network events as candidate assistant streams when the parser finds assistant-authored text, even if the URL has changed again.
- Added support for ChatGPT web patch streams using `p` / `v` fields that point into `message/content/parts`.
- Added support for historical ChatGPT SSE `data: {...}` packets with `message.author.role = assistant` and `content.parts`.
- Added support for Responses-style `output_text` packets.
- Added ChatGPT-specific DOM response selectors:
  - `[data-message-author-role="assistant"]`
  - `[data-testid^="conversation-turn-"]`
  - ChatGPT markdown containers
  - `main article` fallback
- Added richer polling traces to `debug.log`, including candidate response previews, body previews, and periodic HTML snapshots.
- Kept Grok routing and Grok response parsing intact.

## Notes

The bridge already writes request and response payloads through the JavaScript fetch/XHR hook into `data/traffic.log` and mirrors them into `debug.log`. This pass makes stalled ChatGPT DOM polling more useful by also putting visible DOM/source snapshots into the trace stream, so future selector repairs can be done from the log without manually copying React HTML.
