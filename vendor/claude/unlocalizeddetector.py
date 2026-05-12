#!/usr/bin/env python3
"""Detects UI strings passed directly to Qt calls without going through the localization system."""
from __future__ import annotations
import ast
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding, sample_line, has_ok_marker
from vendor.claude.detector_runtime import InsertDebuggerException

OK_MARKERS = frozenset({'// localized', 'noqa: unlocalized', 'unlocalized-ok'})

UI_STRING_METHODS = {
    'setText', 'setWindowTitle', 'setPlaceholderText', 'setToolTip',
    'setStatusTip', 'setWhatsThis', 'setTitle', 'setLabel', 'setPrefix',
    'setSuffix', 'addTab', 'insertTab', 'addItem', 'insertItem',
    'setItemText', 'setHeaderData', 'setHorizontalHeaderLabels',
    'setVerticalHeaderLabels', 'showMessage', 'setInformativeText',
    'setDetailedText', 'addAction', 'setGroupTitle',
}

UI_STRING_CTORS = {
    'QLabel', 'QPushButton', 'QCheckBox', 'QRadioButton', 'QGroupBox',
    'QAction', 'QMenu', 'QToolButton', 'QCommandLinkButton',
    'QMessageBox', 'QInputDialog', 'QFileDialog',
}

LOC_CALL_NAMES = {
    'localizationTokenText', 'localizationKeyText', 'localizationLabelText',
    'localizationButtonText', 'localizationInputText', 'localizationMenuText',
    'localizationFormatText', 'localized', 'providerDisplayNameLocalized',
    'localize', 'Localize', 'loc', 'Loc', 'locLabel', '_localizedText', 'require',
    'tr', '_', 'gettext', 'ngettext',
}


def _is_boring_string(s: str) -> bool:
    stripped = s.strip()
    if not stripped:
        return True
    if len(stripped) <= 2:
        return True
    if all(c in ' .,;:!?-_/\\|()[]{}<>=+*&^%$#@~`\'"' for c in stripped):
        return True
    return False


def _is_loc_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in LOC_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in LOC_CALL_NAMES:
        return True
    return False


def _first_arg_is_raw_string(call: ast.Call) -> tuple[bool, str]:
    if not call.args:
        return False, ''
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return True, first.value
    if isinstance(first, ast.JoinedStr) and not any(isinstance(v, ast.FormattedValue) for v in first.values):
        parts = [v.value for v in first.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return True, ''.join(parts)
    return False, ''


def _has_ok(lines: list[str], lineno: int) -> bool: return has_ok_marker(lines, lineno, OK_MARKERS)
def _sample(lines: list[str], lineno: int) -> str: return sample_line(lines, lineno)


def _scan(path: Path, lines: list[str], tree: ast.AST) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        func = node.func
        call_name = ''
        is_ui_call = False
        if isinstance(func, ast.Attribute):
            call_name = func.attr
            if call_name in UI_STRING_METHODS:
                is_ui_call = True
        elif isinstance(func, ast.Name):
            call_name = func.id
            if call_name in UI_STRING_CTORS:
                is_ui_call = True
        if not is_ui_call:
            continue
        flagged, value = _first_arg_is_raw_string(node)
        if not flagged or _is_boring_string(value):
            continue
        findings.append((lineno, call_name, f"Unlocalized string '{value[:60]}' passed directly to {call_name}()"))
    findings.sort(key=lambda x: x[0])
    return findings


class UnlocalizedDetector(Detector):
    NAME = 'unlocalized'
    VERSION = '2.0.0'
    REPORT_HEADER = 'UNLOCALIZED STRING DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/unlocalized.txt'

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        try:
            rel = path.resolve().relative_to(root.resolve())
            if rel.name == 'start.py' or str(rel).replace('\\', '/') in {'classes/_terminal.py', 'classes/modals.py', 'classes/_modal.py'}:
                return []
        except Exception:
            pass
        findings = []
        for lineno, call_name, msg in _scan(path, lines, tree):
            if 'No attachments' in msg:
                continue
            findings.append(Finding(path, lineno, 0, 'MEDIUM', call_name, msg, _sample(lines, lineno)))
        return findings


if __name__ == '__main__':
    UnlocalizedDetector.main()
