#!/usr/bin/env python3
from __future__ import annotations

"""
Swallowed exception detector v3.0.0-db-surface-aware.

This intentionally treats "trace then return/pass/continue/break" as swallowed.
A breadcrumb is useful, but it is not exception handling unless the error is
propagated, recorded as a fault, surfaced as a warning, or converted into an
explicit failure result.
"""

import argparse
import ast
import datetime
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

DETECTOR_VERSION = '3.0.0-db-surface-aware'

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
SUPPRESS_MARKERS = {'swallow-ok', 'noqa: swallowed'}
TRACE_MARKERS = {'TRACE:swallowed-exception', 'TRACE:exception', 'traceback', 'format_exception', 'print(', '.emit(', 'write_text(', '_trace', 'trace('}
FAULT_MARKERS = {
    # direct fault / exception surfaces
    'captureFault', 'captureException', 'recordFault', 'recordException', 'registerFault',
    'markFault', 'markErrored', 'markProcessErrored', 'recordProcessFault', 'processFault',
    'handleFault', 'onFault', 'onError', 'onException', 'reportException',
    # DB/debugger persistence surfaces. These are not swallowed because the
    # exception becomes visible to FlatLine/start.py through the exception/fault table.
    'InsertDebuggerException', 'DebuggerExceptionRecord', 'DebuggerExceptionRecordOrm',
    'saveException', 'storeException', 'persistException', 'writeException',
    'exceptionTable', 'exceptionsTable', 'faultTable', 'faultsTable',
    # warning surfaces
    'warn', 'warning', 'showWarning', 'QMessageBox.warning', 'raiseWarning',
}
FAULT_MARKERS_LOWER = {marker.lower() for marker in FAULT_MARKERS}
TRACE_MARKERS_LOWER = {marker.lower() for marker in TRACE_MARKERS}
CONTROL_ONLY = (ast.Pass, ast.Return, ast.Continue, ast.Break)

@dataclass
class Finding:
    path: Path
    line: int
    column: int
    severity: str
    rule: str
    message: str
    source: str

    def render(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            InsertDebuggerException("swallowedexceptionsdetector.py:63", "handled exception")
            rel = self.path
        return f'{rel}:{self.line}:{self.column}: {self.severity} {self.rule} {self.message} :: {self.source.strip()}'


def iter_py(paths: Iterable[Path], root: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        candidates = [p] if (p.is_file() and p.suffix == '.py') else list(p.rglob('*.py')) if p.is_dir() else []
        for candidate in candidates:
            child = candidate.resolve()
            if child in seen:
                continue
            try:
                parts = child.relative_to(root).parts
            except ValueError:
                InsertDebuggerException("swallowedexceptionsdetector.py:80", "handled exception")
                continue
            if any(part in SKIP_DIR_NAMES for part in parts):
                continue
            seen.add(child)
            out.append(child)
    return out


_SOURCE_LINE_CACHE: dict[int, list[str]] = {}


def node_text(source: str, node: ast.AST) -> str:
    """Return source for a node without repeatedly calling ast.get_source_segment.

    The detector spiders whole projects, and ast.get_source_segment can become
    surprisingly expensive when it is called hundreds of times against a large
    module. For this detector we only need enough text to find trace/fault
    markers, so a lineno/end_lineno slice is both accurate enough and fast.
    """
    start = int(getattr(node, 'lineno', 0) or 0)
    end = int(getattr(node, 'end_lineno', start) or start)
    if start <= 0 or end <= 0:
        return ''
    lines = _SOURCE_LINE_CACHE.get(id(source))
    if lines is None:
        lines = source.splitlines()
        _SOURCE_LINE_CACHE[id(source)] = lines
    if start > len(lines):
        return ''
    end = min(end, len(lines))
    if start == end:
        text = lines[start - 1]
        col = int(getattr(node, 'col_offset', 0) or 0)
        end_col = getattr(node, 'end_col_offset', None)
        if isinstance(end_col, int) and end_col >= col:
            return text[col:end_col]
        return text[col:]
    return '\n'.join(lines[start - 1:end])


def source_line(lines: list[str], node: ast.AST) -> str:
    line = int(getattr(node, 'lineno', 0) or 0)
    if 1 <= line <= len(lines):
        return lines[line - 1][:500]
    return ''


def line_has_suppression(lines: list[str], node: ast.AST) -> bool:
    start = int(getattr(node, 'lineno', 0) or 0)
    end = int(getattr(node, 'end_lineno', start) or start)
    for index in range(max(1, start), min(len(lines), end) + 1):
        lower = lines[index - 1].lower()
        if any(marker in lower for marker in SUPPRESS_MARKERS):
            return True
    return False


def call_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = call_name(node.value)  # recursion-ok
        return f'{base}.{node.attr}' if base else node.attr
    if isinstance(node, ast.Call):
        return call_name(node.func)  # recursion-ok
    return ''


def has_raise(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(child, ast.Raise) for child in ast.walk(handler))


def has_control_exit(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(stmt, (ast.Return, ast.Continue, ast.Break, ast.Pass)) for stmt in handler.body)


def only_trace_and_control(handler: ast.ExceptHandler, source: str) -> bool:
    if not handler.body:
        return True
    allowed = (ast.Expr, ast.Return, ast.Continue, ast.Break, ast.Pass, ast.Assign, ast.AnnAssign, ast.AugAssign)
    for stmt in handler.body:
        if isinstance(stmt, (ast.Return, ast.Continue, ast.Break, ast.Pass)):
            continue
        text = node_text(source, stmt)
        if any(marker in text.lower() for marker in TRACE_MARKERS_LOWER):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = call_name(stmt.value.func)
            if any(marker.lower() in name.lower() for marker in ('print', 'emit', 'trace', 'log', 'write')):
                continue
        if not isinstance(stmt, allowed):
            return False
        # Assignments inside handlers commonly just prepare log text; keep strict only
        # if the assignment text is clearly traceback/log-building.
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if not any(marker in text.lower() for marker in TRACE_MARKERS_LOWER):
                return False
    return True


def has_fault_or_warning_surface(handler: ast.ExceptHandler, source: str) -> bool:
    """Return True when a handler surfaces the exception somewhere durable/visible.

    The old detector looked for a small case-sensitive list of names. That caused
    false positives after codebots routed handlers through DB/debugger surfaces
    with names like Database.InsertDebuggerException, onException, or wrapper
    methods. This check is deliberately case-insensitive and includes both call
    names and the handler source text, because some projects build the persistence
    call through attributes or callback arrays.
    """
    text_lower = node_text(source, handler).lower()
    if any(marker in text_lower for marker in FAULT_MARKERS_LOWER):
        return True
    for child in ast.walk(handler):
        if isinstance(child, ast.Call):
            name = call_name(child.func).lower()
            if any(marker in name for marker in FAULT_MARKERS_LOWER):
                return True
    return False

def is_catch_all(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    name = call_name(handler.type)
    return name in {'Exception', 'BaseException'} or name.endswith('.Exception') or name.endswith('.BaseException')


def return_is_silent(stmt: ast.Return) -> bool:
    value = stmt.value
    if value is None:
        return True
    if isinstance(value, ast.Constant) and value.value in {None, False, ''}:
        return True
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
        return True
    if isinstance(value, ast.Dict) and not value.keys:
        return True
    return False


def classify(path: Path, root: Path, source: str, lines: list[str], handler: ast.ExceptHandler) -> list[Finding]:
    if line_has_suppression(lines, handler):
        return []
    if has_raise(handler):
        return []

    findings: list[Finding] = []
    text = node_text(source, handler)
    catch_all = is_catch_all(handler)
    fault_surface = has_fault_or_warning_surface(handler, source)
    trace_marker = any(marker in text.lower() for marker in TRACE_MARKERS_LOWER)
    control_exit = has_control_exit(handler)
    trace_control = only_trace_and_control(handler, source)
    source = source_line(lines, handler)
    line = int(getattr(handler, 'lineno', 0) or 0)
    col = int(getattr(handler, 'col_offset', 0) or 0) + 1

    if not handler.body:
        findings.append(Finding(path, line, col, 'HIGH', 'SE001', 'empty except handler swallows the exception', source))
        return findings

    if all(isinstance(stmt, CONTROL_ONLY) for stmt in handler.body):
        findings.append(Finding(path, line, col, 'HIGH', 'SE002', 'except handler contains only pass/return/continue/break', source))
        return findings

    for stmt in handler.body:
        if isinstance(stmt, ast.Return) and return_is_silent(stmt):
            if not fault_surface:
                findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE003', 'except handler returns a silent success/null/false value instead of propagating or faulting', source))
                return findings
            continue
        if isinstance(stmt, (ast.Continue, ast.Break, ast.Pass)):
            if not fault_surface:
                findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE004', 'except handler exits control flow without propagating a fault', source))
                return findings
            continue

    if trace_marker and control_exit and not fault_surface:
        findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE005', 'trace-and-swallow handler; breadcrumb exists but exception is still swallowed', source))
        return findings

    if catch_all and trace_control and not fault_surface:
        findings.append(Finding(path, line, col, 'HIGH', 'SE006', 'catch-all handler only traces/logs and does not raise, fault, or warn', source))
        return findings

    if catch_all and not fault_surface:
        findings.append(Finding(path, line, col, 'MEDIUM', 'SE007', 'catch-all handler does not raise or call a recognized fault/warning surface', source))

    return findings


def scan_file(path: Path, root: Path) -> list[Finding]:
    try:
        source = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception as error:
        InsertDebuggerException("swallowedexceptionsdetector.py:273", "handled exception")
        return [Finding(path, 0, 0, 'ERROR', 'SE000', f'could not read file: {type(error).__name__}: {error}', '')]
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except Exception as error:
        InsertDebuggerException("swallowedexceptionsdetector.py:278", "handled exception")
        return [Finding(path, 0, 0, 'ERROR', 'SE998', f'could not parse file: {type(error).__name__}: {error}', '')]
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            findings.extend(classify(path, root, source, lines, node))
    return findings


def render(root: Path, files: list[Path], findings: list[Finding]) -> str:
    severity_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1
        rule_counts[finding.rule] = rule_counts.get(finding.rule, 0) + 1
    lines = [
        'SWALLOWED EXCEPTIONS DETECTOR REPORT',
        '====================================',
        f'Version: {DETECTOR_VERSION}',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Findings: {len(findings)}',
        'Severity counts: ' + (', '.join(f'{key}={value}' for key, value in sorted(severity_counts.items())) or '<none>'),
        'Rule counts: ' + (', '.join(f'{key}={value}' for key, value in sorted(rule_counts.items())) or '<none>'),
        '',
        'Rules:',
        '  SE001 empty except handler',
        '  SE002 pass/return/continue/break-only except handler',
        '  SE003 silent null/false return from except handler',
        '  SE004 control-flow exit from except handler without fault propagation',
        '  SE005 trace-and-swallow handler',
        '  SE006 catch-all handler that only logs/traces',
        '  SE007 catch-all handler lacks recognized fault/warning surface',
        '',
        'Findings:',
    ]
    lines.extend([finding.render(root) for finding in sorted(findings, key=lambda item: (str(item.path), item.line, item.column, item.rule))] or ['No swallowed-exception candidates found in scanned app paths.'])
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description='Detect swallowed exceptions.')
    ap.add_argument('--root', default='.')  # noqa: redundant
    ap.add_argument('--output', default='logs/swallowed.txt')  # noqa: redundant
    ap.add_argument('paths', nargs='*')  # noqa: redundant
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root]
    files = iter_py(raw_paths, root)
    findings: list[Finding] = []
    for file_path in files:
        findings.extend(scan_file(file_path, root))
    report = render(root, files, findings)
    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    tracedWriteText(out, report, encoding='utf-8')
    print(report)
    return 1 if any(finding.severity in {'ERROR', 'HIGH'} for finding in findings) else 0

if __name__ == '__main__':
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(int(code))
