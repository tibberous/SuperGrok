# FlatLine / Claude Bundle Integration - 2026-05-02

This pass vendors the uploaded Claude/FlatLine detector bundle in `vendor/claude` and keeps the exact source archive as `vendor/claude/claude_latest_flatline_debugger_20260502.zip`.

## CLI routes added or refreshed

- `python start.py --health`
- `python start.py --certify`
- `python start.py --detector-selftest`
- `python start.py --phase-hooks`
- `python start.py --nonconform`
- `python start.py --comport`
- `python start.py --manual`
- Existing detector routes remain: raw SQL, swallowed exceptions, recursion, file I/O, process faults, phase ownership, bad code, unlocalized UI strings, and monkey-patch detection.

## Whitepaper review

The vendored bundle includes two whitepapers:

1. `triodesktop_threadzero_process_whitepaper_friendly_letter.txt`
   - Main rule: if work can hang, crash, block, install, probe tools, touch Qt, or run long, it should be phase/process-owned rather than hidden in a thread.
   - SuperGrok already uses `QProcess` for ToolCall work and `QTimer` for polling, not Python `threading.Thread` workers.
   - The launcher dependency install path was tightened through `managedSubprocessRun(...)` with a timeout and visible trace/error handling.

2. `locked_file_process_cleanup_whitepaper.txt`
   - Main rule: Windows relaunch should not reuse static redirected logs while stale child processes are still alive.
   - `start.py` now has `Tasks.taskkill(pid)` plus command-line-targeted stale SuperGrok process cleanup before GUI launch.
   - Cleanup is Windows-only, avoids killing every `python.exe`, skips the current PID, and can be bypassed with `--no-stale-process-kill`.

## Still not claimed

This pass does not turn SuperGrok into the full TrioDesktop parent/child FlatLine debugger. It wires the current SuperGrok launcher to the newest detector bundle, keeps the uploaded bundle in the repo, advertises the existing heartbeat/poll/vardump surfaces, and implements the whitepaper-relevant process cleanup safeguards that fit this app.

## Validation notes after cleanup

- The Claude detector runner now starts each detector in its own process group/session and kills the detector process tree on timeout, which fixed an intermittent self-test hang around the final detector route.

- AST + `py_compile` passed across all 54 Python files in the repo, including vendored detectors.
- `python start.py --debugger-query-surfaces` returns `heartbeat poll vardump accepts-proxy`.
- `python start.py --debugger-vardump` reports the vendored Claude bundle and both whitepapers as visible.
- `python start.py --manual` prints the vendored detector manual.
- `python start.py --detector-selftest --detector-timeout 4` exits 0; canary findings are expected, and route failures are 0.
- Focused detector cleanup passed: `monkey`, `phase-hooks`, `comport`, `swallowed`, `file-io`, `raw-sql`, `process-faults`, `phase-ownership`, `bad-code`, `lifecycle-bypass`, `unlocalized`, and `recursion` all exit 0 on the SuperGrok scan root.
- `python start.py --certify --detector-timeout 25` currently exits 1 because `redundant.py` reports style/redundancy findings and the full `nonconform` route expects TrioDesktop/CutiePy canonical symbols. The project-specific `comport` route suppresses those Trio-specific symbol requirements and passes. This is recorded as a detector-fit issue, not hidden as a false pass.
