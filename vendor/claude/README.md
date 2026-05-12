# Claude diagnostic detectors

Permanent home for Claude-attributed static diagnostic helpers used by SuperGrok Bridge.

All detectors are report-only. They do not edit files and do not launch the Qt application. The normal entrypoint is `start.py`, so the detectors can run even when PySide6/WebEngine is broken.

## Main CLI

Run all detectors, print to console, write reports, and exit:

```bash
python start.py --monkey
```

Run selected detectors:

```bash
python start.py --claude-detectors raw-sql swallowed-exceptions
python start.py --lifecycle-bypass
python start.py --monkeypatch
python start.py --raw-sql
python start.py --recursion
python start.py --redundant
python start.py --swallowed
```

Help/man:

```bash
python start.py --help
python start.py man
python start.py --man
python start.py --debugger-menu
```

## Reports

Combined reports:

- `reports/claude_detectors_report_<timestamp>.txt`
- `reports/claude_detectors_report_latest.txt`
- `reports/clod_monkey_report_latest.txt` compatibility alias

Per-detector logs:

- `logs/lifecyclebypass.txt`
- `logs/monkeypatch.txt`
- `logs/rawsql.txt`
- `logs/recursion.txt`
- `logs/redundant.txt`
- `logs/swallowed.txt`

## Detectors

### lifecyclebypassdetector.py

Flags direct `subprocess.Popen`, `subprocess.run`, `threading.Thread`, and `os.system` calls outside approved lifecycle wrappers.

### monkeypatchdetect.py

Spiders/import-walks and flags constructs that mutate objects defined elsewhere: attribute assignments on imported names, `setattr`/`delattr` on external targets, `sys.modules` injection, `mock.patch`, `wrapt`, dynamic `exec`/`eval`, protocol swaps, and more.

### rawsqldetector.py

Flags raw sqlite/mysql connectors, cursor usage, and direct execute candidates.

### recursiondetector.py

Flags direct self-recursion candidates.

### redundant.py

Detects runs of 3+ consecutive near-identical lines that should usually become a loop/key-walk.

### swallowedexceptionsdetector.py

Flags exception handlers that trace/log and then silently continue, return null/false, or skip a recognized fault/warning surface. See `SWALLOWED_EXCEPTION_DETECTOR_WHITEPAPER.md`.

## Compatibility note

The old `/vendor/clod/run_clod_reports.py` remains as a wrapper, but the active detector suite is `/vendor/claude`. The duplicate old monkey-patch detector was removed; keep `vendor/claude/monkeypatchdetect.py` as canonical.
