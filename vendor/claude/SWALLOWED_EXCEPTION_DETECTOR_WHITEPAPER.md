# Whitepaper for Claude: Upgrade the Swallowed Exceptions Detector

## Problem

The old `swallowedexceptionsdetector.py` was too shallow. It only detected handlers whose first few body lines were literally one of:

- `pass`
- `return`
- `return None`
- `continue`
- `break`

That missed the exact failure mode that hurt PyEncoder: handlers that print a breadcrumb such as `[TRACE:swallowed-exception]` and then silently continue, return `None`, return `False`, break, or otherwise avoid registering a real fault.

A trace line is not exception handling. A trace line is only a breadcrumb. If the app does not raise, record a fault, warn the user, update lifecycle/process state, or return an explicit typed failure, the exception is still swallowed.

## Concrete Evidence

After replacing the old text-only detector with an AST-based v2 detector, the same staged PyEncoder `start.py` scan produced:

- 134 swallowed-exception candidates in `start.py` alone
- 115 high-severity findings
- 19 medium-severity findings

The old detector reported 0 because it did not understand trace-and-swallow handlers.

## Required Detector Behavior

The detector must flag all of these patterns:

```python
try:
    risky()
except Exception:
    pass
```

```python
try:
    risky()
except Exception as error:
    print(f'[TRACE:swallowed-exception] {error}', file=sys.stderr, flush=True)
```

```python
try:
    risky()
except Exception as error:
    print(f'[TRACE:exception] {error}', file=sys.stderr, flush=True)
    return None
```

```python
try:
    risky()
except Exception as error:
    debugger.emit(str(error), exception_log)
    continue
```

```python
try:
    risky()
except Exception as error:
    trace(error)
    return False
```

The important idea is simple:

> A logged exception is still swallowed unless it is converted into a visible failure path.

## What Counts as a Visible Failure Path

The detector should treat these as safer than a swallow:

- `raise` or bare `raise`
- `raise SomeError(...) from error`
- lifecycle fault registration, such as `captureFault(...)`
- exception registration, such as `captureException(...)`
- process status update, such as `markErrored(...)` or equivalent
- user-facing warning, such as `warn(...)`, `warning(...)`, `QMessageBox.warning(...)`, or a project-specific warning bus
- explicit typed failure object/result, not `None`, `False`, or an empty structure

A handler that only writes to stderr, writes a log line, or calls `emit(...)` should still be flagged unless it also performs one of the above failure-path actions.

## Recommended Rule Set

The v2 detector added these rules:

- `SE001` — empty except handler
- `SE002` — pass/return/continue/break-only except handler
- `SE003` — silent null/false return from an except handler
- `SE004` — control-flow exit from an except handler without fault propagation
- `SE005` — trace-and-swallow handler
- `SE006` — catch-all handler that only logs/traces
- `SE007` — catch-all handler lacking a recognized fault/warning surface

## Why AST Is Required

The old line scanner looked at a few text lines after `except`. That approach fails when:

- the handler builds a log message before returning
- the handler calls a helper that hides the swallow
- the handler is nested
- the handler uses multiline calls
- the handler catches `Exception as swallowed_error` and then only logs
- the handler has one harmless-looking line before `return None`

AST traversal allows the detector to examine the entire `ExceptHandler` body and classify its behavior rather than guessing from a short text window.

## Severity Guidance

High severity:

- catch-all `except Exception` or bare `except`
- handler only logs/traces
- handler returns `None`, `False`, empty list/dict/tuple/set
- handler continues or breaks out of the flow
- handler variable is named `swallowed_error`

Medium severity:

- non-catch-all handler does not raise or fault
- handler logs a domain-specific warning but does not set lifecycle/process state

Low severity should be avoided for this project. Swallowed exceptions are production stability bugs, not style nits.

## PyEncoder-Specific Requirement

PyEncoder should not intentionally die on recoverable errors. It should also not silently continue after serious errors. The proper behavior is:

1. trace the event,
2. record a lifecycle/process fault,
3. warn/degrade in the UI or debugger,
4. keep the parent process alive when possible,
5. never erase the exception context.

For OpenShot/native/media paths, this is especially important because native crashes already make debugging hard. Python exceptions in those paths must leave structured evidence.

## Implementation Notes

The upgraded detector should live permanently at:

```text
vendor/claude/swallowedexceptionsdetector.py
```

It should be wired to:

```text
python start.py --swallowed
python start.py --claude-detectors
```

The detector should write a text report and return a nonzero exit code when high-severity findings exist.

## Bottom Line

Claude should stop treating "printed a trace" as a fixed swallowed exception. It is only fixed when the exception is either propagated or converted into an explicit fault/warning/failure surface.
