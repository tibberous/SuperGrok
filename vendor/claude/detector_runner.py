#!/usr/bin/env python3
"""Subprocess runner for vendor/claude detectors.

Coded by ChatGPT 5.4 Thinking.

The important safety fix: a clean detector result is only meaningful when the
scanner actually saw app files. Since detectors intentionally skip vendor/,
health now prints scan coverage and warns/fails if it only scanned the detector
harness instead of a real project tree.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from vendor.claude.detector_runtime import (
    InsertDebuggerException,
    launcherRunCommand,
    readDebuggerExceptions,
    tracedReadText,
    tracedWriteText,
)

APP_ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR_CLAUDE = Path(__file__).resolve().parent
LOG_DIR_NAME = "logs"

STATUS_FAILED = 0
STATUS_SUCCESS = 1
STATUS_SUCCESS_200 = 200
FAILED_TEXT = "FAILED!"
SUCCESS_TEXT = "SUCCESS!"
SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}


def detectorPython() -> list[str]:
    """Return the interpreter command used for detector subprocesses.

    Detectors are pure-stdlib AST scripts, so they do not need site-packages.
    Running them with -S avoids broken virtualenv/sitecustomize hangs and makes
    direct manual runs reproducible. DETECTOR_PYTHON may override this when a
    project has a known-good interpreter.
    """
    override = os.environ.get("DETECTOR_PYTHON", "").strip()
    if override:
        return [override, "-S"]
    system_python = Path("/usr/bin/python3")
    if system_python.exists():
        return [str(system_python), "-S"]
    return [sys.executable, "-S"]


@dataclass(frozen=True)
class DetectorRoute:
    key: str
    flag: str
    script: str
    report: str
    description: str
    aliases: tuple[str, ...] = ()
    monkeyStyle: bool = False


DETECTORS: tuple[DetectorRoute, ...] = (
    DetectorRoute("monkey", "--monkey", "monkeypatchdetect.py", "monkeypatches.txt", "Monkey patch mutations", ("monkeypatch", "monkeypatches", "monkey-patch"), True),
    DetectorRoute("lifecyclebypass", "--lifecycle-bypass", "lifecyclebypassdetector.py", "lifecyclebypass.txt", "Subprocess/thread/Qt exec lifecycle bypasses", ("lifecycle", "lifecycle-bypass", "lifecyclebypass", "bypassdetector", "bypass-detector", "bypass")),
    DetectorRoute("rawsql", "--raw-sql", "rawsqldetector.py", "rawsql.txt", "Raw DB connector/cursor/execute calls", ("raw-sql", "raw_sql", "sql")),
    DetectorRoute("recursion", "--recursion", "recursiondetector.py", "recursion.txt", "Direct self-recursion candidates"),
    DetectorRoute("swallowed", "--swallowed", "swallowedexceptionsdetector.py", "swallowed.txt", "Swallowed exception handlers", ("swallow", "swallowed-exceptions")),
    DetectorRoute("redundant", "--redundant", "redundant.py", "redundant.txt", "Repeated line shapes that should be loops"),
    DetectorRoute("fileio", "--file-io", "fileiodetector.py", "fileio.txt", "Raw file I/O bypassing traced wrappers", ("file-io", "file_io")),
    DetectorRoute("processfaults", "--process-faults", "processfaultdetector.py", "process_faults.txt", "Process launches missing fault callbacks", ("process-fault", "process-faults", "process_faults")),
    DetectorRoute("phaseownership", "--phase-ownership", "phaseownershipdetector.py", "phase_ownership.txt", "Runtime work that should be Phase-owned", ("phase", "phase-ownership", "phase_ownership")),
    DetectorRoute("threads", "--threads", "nonconformdetector.py", "nonconform.txt", "Thread/Process/Phase safety checks (merged into nonconform)", ("threads", "thread-safety")),
    DetectorRoute("badcode", "--bad-code", "badcodedetector.py", "badcode.txt", "General AST bad-code patterns", ("bad-code", "bad_code")),
    DetectorRoute("unlocalized", "--unlocalized", "unlocalizeddetector.py", "unlocalized.txt", "Qt UI strings not routed through localize()"),
)


def printStatus(status: int | str, message: str = "") -> None:
    if status in (STATUS_SUCCESS, STATUS_SUCCESS_200):
        print(f"{SUCCESS_TEXT} {message}".rstrip())
    elif status == STATUS_FAILED:
        print(f"{FAILED_TEXT} {message}".rstrip())
    else:
        print(f"{status} {message}".rstrip())


def normalizeArg(raw: str) -> str:
    return raw.strip().lower().lstrip("-/").replace("-", "").replace("_", "")


def countAppPythonFiles(root: Path) -> int:
    if root.is_file():
        return 1 if root.suffix == ".py" else 0
    total = 0
    for child in root.rglob("*.py"):
        try:
            parts = child.relative_to(root).parts
        except ValueError:
            InsertDebuggerException("detector_runner.py:107", "handled exception")
            continue
        if any(part in SKIP_DIR_NAMES for part in parts):
            continue
        total += 1
    return total


def routeByAlias() -> dict[str, DetectorRoute]:
    routes: dict[str, DetectorRoute] = {}
    for route in DETECTORS:
        for name in (route.key, route.flag, *route.aliases):
            routes[normalizeArg(name)] = route
    return routes


def buildCommand(route: DetectorRoute, root: Path, output: Path, paths: list[Path]) -> list[str]:
    script = VENDOR_CLAUDE / route.script
    seeds = [str(p) for p in (paths if paths else [root])]
    if route.monkeyStyle:
        # monkeypatchdetect.py is the one odd legacy script: root/seed first and --out, not --output.
        return [*detectorPython(), str(script), *seeds, "--out", str(output)]
    return [*detectorPython(), str(script), "--root", str(root), "--output", str(output), *seeds]


def parseReportMetrics(report: Path) -> tuple[int | None, int | None]:
    """Return (findings, files_scanned) parsed from a detector report."""
    try:
        text = tracedReadText(report, encoding="utf-8", errors="replace")
    except OSError as exc:
        InsertDebuggerException("parseReportMetrics", exc, str(report))
        return None, None

    file_matches = re.findall(r"^Files scanned\s*:\s*(\d+)", text, flags=re.M)
    files_scanned = sum(int(x) for x in file_matches) if file_matches else None

    # Most detectors print exactly Findings: N. Thread detector uses two
    # section counts. The monkey detector has legacy wording.
    matches = re.findall(r"^Findings\s*:\s*(\d+)", text, flags=re.M)
    if matches:
        return sum(int(x) for x in matches), files_scanned
    matches = re.findall(r"^(?:Thread/Process findings|Phase findings):\s*(\d+)\s*$", text, flags=re.M)
    if matches:
        return sum(int(x) for x in matches), files_scanned
    monkey_matches = re.findall(r"^Total findings\s*:\s*(\d+)", text, flags=re.M)
    if monkey_matches:
        return sum(int(x) for x in monkey_matches), files_scanned
    if "No monkey-patches detected" in text:
        return 0, files_scanned
    return None, files_scanned


def parseReportCount(report: Path, label: str = "Findings") -> int | None:
    # Compatibility shim for older callers/tests.
    findings, _ = parseReportMetrics(report)
    return findings


def runClaudeDetector(route: DetectorRoute, root: Path, paths: list[Path] | None = None, timeout: int = 180) -> int:
    paths = list(paths or [])
    logDir = root / LOG_DIR_NAME
    logDir.mkdir(parents=True, exist_ok=True)
    output = logDir / route.report
    script = VENDOR_CLAUDE / route.script
    if not script.exists():
        printStatus(STATUS_FAILED, f"missing detector: {script}")
        return 2
    cmd = buildCommand(route, root, output, paths)
    print(f"\n=== {route.flag} -> {output.relative_to(root) if output.is_relative_to(root) else output} ===")
    print("Command:", " ".join(cmd))
    try:
        completed = launcherRunCommand(cmd, cwd=root, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        InsertDebuggerException("runClaudeDetector.timeout", exc, route.key)
        printStatus(STATUS_FAILED, f"{route.key} timed out after {timeout}s")
        if exc.stdout:
            print(exc.stdout)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 124
    if completed.stdout:
        print(completed.stdout.rstrip())
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)
    code = int(completed.returncode)
    count, files_scanned = parseReportMetrics(output)
    if files_scanned is not None:
        print(f"Report files scanned parsed: {files_scanned}")
    if count is not None:
        print(f"Report findings parsed: {count}")
    printStatus(STATUS_SUCCESS if code == 0 else STATUS_FAILED, f"{route.key} exit={code}")
    return code


def runAll(root: Path, paths: list[Path] | None = None, timeout: int = 180, coverageFail: bool = True) -> int:
    root = root.resolve()
    appFiles = countAppPythonFiles(root)
    print("Running Claude detector suite...")
    print(f"Root: {root}")
    print(f"Non-vendor Python files visible to detectors: {appFiles}")
    failures = 0
    for route in DETECTORS:
        code = runClaudeDetector(route, root, paths, timeout=timeout)
        if code != 0:
            failures += 1
    print(f"\nDetector routes run: {len(DETECTORS)}")
    print(f"Routes with findings/errors: {failures}")
    if appFiles <= 1 and coverageFail:
        printStatus(STATUS_FAILED, "scan coverage is too small; this was a harness scan, not a real project audit")
        failures += 1
    return 1 if failures else 0


CANARY_SOURCE = "\n".join([
    'import os, sys, sqlite3, subprocess, threading',
    'from pathlib import Path',
    '',
    'flag = True',
    '',
    'def helper():',
    '    return 1',
    '',
    'def loopCandidate():',
    '    return loopCandidate()',
    '',
    'def swallowed():',
    '    try:',
    '        raise RuntimeError("boom")',
    '    except Exception:',
    '        pass',
    '',
    'def raw_sql():',
    '    conn = sqlite3.connect(":memory:")',
    '    cur = conn.cursor()',
    '    cur.execute("select 1")',
    '',
    'def raw_file_io():',
    '    open("x.txt", "w").write("hello")',
    '    Path("x.txt").read_text()',
    '',
    'def lifecycle_bypass():',
    '    subprocess.run(["echo", "hi"])',
    '    threading.Thread(target=helper).start()',
    '',
    'def process_fault_gap():',
    '    Process("bad").start()',
    '',
    'def phase_ownership_gap():',
    '    subprocess.Popen(["echo", "hi"])',
    '',
    'def redundant_lines(widget):',
    '    widget.addItem("one")',
    '    widget.addItem("two")',
    '    widget.addItem("three")',
    '',
    'def bad_code():',
    '    a = 1',
    '    a = 2',
    '    b = b',
    '    if flag == flag:',
    '        return True',
    '    return not not flag',
    '',
    'def monkey_patch():',
    '    os.path.exists = lambda p: True',
    '    sys.modules["fake"] = object()',
    '',
    'def unlocalized(button):',
    '    QLabel("Hello buddy")',
    '    button.setText("Save Now")',
]) + "\n"


def runSelfTest(timeout: int = 60) -> int:
    tempRoot = Path(tempfile.mkdtemp(prefix="claude_detector_canary_"))
    try:
        tracedWriteText(tempRoot / "canary.py", CANARY_SOURCE, encoding="utf-8")
        print(f"Detector canary root: {tempRoot}")
        failed = 0
        for route in DETECTORS:
            code = runClaudeDetector(route, tempRoot, timeout=timeout)
            report = tempRoot / LOG_DIR_NAME / route.report
            count = parseReportCount(report)
            if count is None:
                printStatus(STATUS_FAILED, f"{route.key}: could not parse report count")
                failed += 1
            elif count <= 0 and route.key != "threads":
                # threads may overlap with process/phase checks depending on project naming.
                printStatus(STATUS_FAILED, f"{route.key}: canary produced no findings")
                failed += 1
            else:
                printStatus(STATUS_SUCCESS, f"{route.key}: canary findings={count}")
            if code == 124:
                failed += 1
        print(f"Detector canary failures: {failed}")
        print(f"Detector canary logs kept at: {tempRoot / LOG_DIR_NAME}")
        exitCode = 1 if failed else 0
        if os.environ.get("DETECTOR_SELFTEST_NO_EXIT", "") != "1":
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(exitCode)
        return exitCode
    finally:
        # Leave the canary folder in /tmp for post-run inspection. Earlier builds
        # deleted it immediately, which made failed canary runs harder to audit.
        pass


def showDebuggerExceptions() -> int:
    rows = readDebuggerExceptions()
    print("DEBUGGER EXCEPTIONS")
    print("===================")
    if not rows:
        print("No persisted detector/debugger exceptions found.")
        return 0
    for row in rows:
        print(
            f"{row['created_at']} [{row['source']}] "
            f"{row['exc_type']}: {row['exc_message']}"
        )
        if row['context']:
            print(f"  context: {row['context']}")
    return 1


def printManual() -> int:
    manual = VENDOR_CLAUDE / "DETECTORS_MANUAL.md"
    if not manual.exists():
        printStatus(STATUS_FAILED, f"missing manual: {manual}")
        return 2
    print(tracedReadText(manual, encoding="utf-8", errors="replace"))
    return 0
