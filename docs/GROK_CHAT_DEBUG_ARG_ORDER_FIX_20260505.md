# Grok CLI chat debug argument ordering fix — 2026-05-05

## Problem

`python start.py --chat --debug "Hello"` failed in argparse before SuperGrok could enter the chat route:

```text
start.py: error: argument --chat: expected at least one argument
```

That happened because `--chat` used `nargs="+"`. If another option such as `--debug` appeared immediately after `--chat`, argparse treated `--chat` as missing its value and exited before the bridge-client debug tracing could run.

## Fix

`--chat` now accepts zero or more direct values, then SuperGrok performs a post-parse normalization pass. If `--chat` was present and argparse sees leftover bare tokens, those tokens are appended to the chat payload.

Supported forms now include:

```powershell
python start.py --chat "Hello"
python start.py --chat --debug "Hello"
python start.py --chat grok --debug "Hello"
python start.py --chat grok deployment --debug "Hello"
```

When `--debug` is present, the bridge client prints a trace line showing the normalized chat argv before sending the request.

## Expected behavior

Normal successful chat should still print only Grok's answer to stdout. Debug traces go to stderr.
