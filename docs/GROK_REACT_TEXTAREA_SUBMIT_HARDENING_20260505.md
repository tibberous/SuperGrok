# Grok React Textarea Submit Hardening — 2026-05-05

The latest debug run showed the resident bridge was alive, accepted the CLI job, loaded Grok, and eventually reached `after-fill typed ok`, but the job stayed in `poll-response` with an empty preview. That means the bridge was no longer hung in service startup; the likely failure was that Grok's React-controlled textarea was assigned text in a way that did not fully update the app state, so no real chat submission/response stream followed.

This pass hardens the actual DOM input path:

- Textarea/input fills now use the native HTMLTextAreaElement/HTMLInputElement `value` setter before dispatching input/change events.
- Prompt fills now dispatch `beforeinput`, `input`, `change`, and a keyup nudge so React/ProseMirror listeners see the edit.
- Contenteditable fills use selection + `execCommand('insertText')` plus the same event dispatch.
- The send script verifies the editor actually contains the requested message and records actual/expected text length in trace output.
- Response polling now reports editor text length, whether the original prompt is still stuck in the editor, and the click/submit method.
- If the prompt remains in the editor with no visible response and no network stream, the job returns a clear `submit-not-accepted` error instead of looking like an indefinite hang.

The goal is simple: `python start.py --chat --debug "Hello"` should either send and print Grok's answer, or fail with the exact phase that blocked it.
