# Grok CLI Chat Attach Button and Network Stream Fix — 2026-05-05

## Problem

`python start.py --chat --debug "Hello"` could appear to hang after the resident bridge accepted the job. The debug trace proved the bridge reached DOM send/polling, but the JavaScript scorer could still mistake Grok's `Attach` button for a send button because the positive `up` token matched CSS such as `group/attach-button`. The job then clicked Attach, saw no visible answer, and kept polling.

## Fix

The send-button scorer now separates semantic metadata from CSS class text, hard-excludes attach/upload/model/dictation/voice/settings controls, and no longer treats the raw token `up` as proof that a button is a send arrow. If no trustworthy send button appears, it falls back to Enter instead of clicking Attach.

The CLI job can also now finish from Grok's captured `/responses` network stream. DOM polling remains as a fallback, but the fetch/XHR response body is the authoritative answer source when available.

## Expected debug clues

A healthy run should show one of these completion paths:

- `source=network-response-stream`
- a DOM `complete` event with a non-empty answer

If Grok is blocked or not rendered, the trace should identify the stage: service startup, page load, surface probe, button probe, DOM send, or response polling.
