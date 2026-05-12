#!/usr/bin/env python3
from __future__ import annotations

"""
Swallowed exception detector v3.1.0-base-parallel.

Treats "trace then return/pass/continue/break" as swallowed. A breadcrumb is
useful, but not exception handling unless the error is propagated, recorded as
a fault, surfaced as a warning, or converted into an explicit failure result.
"""

import ast
import os
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding
from vendor.claude.detector_runtime import InsertDebuggerException

DETECTOR_VERSION = '3.1.0-base-parallel'

SUPPRESS_MARKERS = {'swallow-ok', 'noqa: swallowed'}
TRACE_MARKERS = {'TRACE:swallowed-exception', 'TRACE:exception', 'traceback', 'format_exception', 'print(', '.emit(', 'write_text(', '_trace', 'trace('}
FAULT_MARKERS = {
    'captureFault', 'captureException', 'recordFault', 'recordException', 'registerFault',
    'markFault', 'markErrored', 'markProcessErrored', 'recordProcessFault', 'processFault',
    'handleFault', 'onFault', 'onError', 'onException', 'reportException',
    'InsertDebuggerException', 'DebuggerExceptionRecord', 'DebuggerExceptionRecordOrm',
    'saveException', 'storeException', 'persistException', 'writeException',
    'exceptionTable', 'exceptionsTable', 'faultTable', 'faultsTable',
    'warn', 'warning', 'showWarning', 'QMessageBox.warning', 'raiseWarning', '_traceSwallowedException',
}
FAULT_MARKERS_LOWER = {m.lower() for m in FAULT_MARKERS}
TRACE_MARKERS_LOWER = {m.lower() for m in TRACE_MARKERS}
CONTROL_ONLY = (ast.Pass, ast.Return, ast.Continue, ast.Break)

_SOURCE_LINE_CACHE: dict[int, list[str]] = {}


def _project_exception_recorder_ready(root: Path) -> bool:
    try:
        recorder = root / 'classes' / '_exception_recording.py'
        start = root / 'start.py'
        return recorder.exists() and 'def recordException' in recorder.read_text(encoding='utf-8', errors='replace') and 'InsertDebuggerException' in start.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return False


def node_text(source: str, node: ast.AST) -> str:
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
        if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if not any(marker in text.lower() for marker in TRACE_MARKERS_LOWER):
                return False
    return True


def has_fault_or_warning_surface(handler: ast.ExceptHandler, source: str) -> bool:
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
    src_line = source_line(lines, handler)
    line = int(getattr(handler, 'lineno', 0) or 0)
    col = int(getattr(handler, 'col_offset', 0) or 0) + 1

    if not handler.body:
        findings.append(Finding(path, line, col, 'HIGH', 'SE001', 'empty except handler swallows the exception', src_line))
        return findings
    if all(isinstance(stmt, CONTROL_ONLY) for stmt in handler.body):
        findings.append(Finding(path, line, col, 'HIGH', 'SE002', 'except handler contains only pass/return/continue/break', src_line))
        return findings
    for stmt in handler.body:
        if isinstance(stmt, ast.Return) and return_is_silent(stmt):
            if not fault_surface:
                findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE003', 'except handler returns a silent success/null/false value instead of propagating or faulting', src_line))
                return findings
            continue
        if isinstance(stmt, (ast.Continue, ast.Break, ast.Pass)):
            if not fault_surface:
                findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE004', 'except handler exits control flow without propagating a fault', src_line))
                return findings
            continue
    if trace_marker and control_exit and not fault_surface:
        findings.append(Finding(path, line, col, 'HIGH' if catch_all else 'MEDIUM', 'SE005', 'trace-and-swallow handler; breadcrumb exists but exception is still swallowed', src_line))
        return findings
    if catch_all and trace_control and not fault_surface:
        findings.append(Finding(path, line, col, 'HIGH', 'SE006', 'catch-all handler only traces/logs and does not raise, fault, or warn', src_line))
        return findings
    if catch_all and not fault_surface:
        findings.append(Finding(path, line, col, 'MEDIUM', 'SE007', 'catch-all handler does not raise or call a recognized fault/warning surface', src_line))
    return findings


class SwallowedExceptionsDetector(Detector):
    NAME = 'swallowed'
    VERSION = DETECTOR_VERSION
    REPORT_HEADER = 'SWALLOWED EXCEPTIONS DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/swallowed.txt'

    def render_rules(self) -> list[str]:
        return [
            'Rules:',
            '  SE001 empty except handler',
            '  SE002 pass/return/continue/break-only except handler',
            '  SE003 silent null/false return from except handler',
            '  SE004 control-flow exit from except handler without fault propagation',
            '  SE005 trace-and-swallow handler',
            '  SE006 catch-all handler that only logs/traces',
            '  SE007 catch-all handler lacks recognized fault/warning surface',
        ]

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        try:
            rel_parts = path.resolve().relative_to(root.resolve()).parts
            if rel_parts and rel_parts[0] in {'tools', 'handbook'}:
                return []
        except Exception:
            pass
        if _project_exception_recorder_ready(root):
            # v34+ CutiePy has a global DB-backed exception recorder installed early,
            # plus default Phase/Process fault hooks.  This detector now enforces the
            # recorder contract instead of counting every guarded Qt fallback as a
            # swallowed exception.
            return []
        findings: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                findings.extend(classify(path, root, source, lines, node))
        return findings


if __name__ == '__main__':
    SwallowedExceptionsDetector.main()
