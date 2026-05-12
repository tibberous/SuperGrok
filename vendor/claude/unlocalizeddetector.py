#!/usr/bin/env python3
"""Detects UI strings passed directly to Qt calls without going through the localization system.

Coded by ChatGPT 5.4 Thinking.
"""
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}

OK_MARKERS = ('// localized', 'noqa: unlocalized', 'unlocalized-ok')

# Qt methods that take a user-visible string as their first positional argument
UI_STRING_METHODS = {
    'setText', 'setWindowTitle', 'setPlaceholderText', 'setToolTip',
    'setStatusTip', 'setWhatsThis', 'setTitle', 'setLabel', 'setPrefix',
    'setSuffix', 'addTab', 'insertTab', 'addItem', 'insertItem',
    'setItemText', 'setHeaderData', 'setHorizontalHeaderLabels',
    'setVerticalHeaderLabels', 'showMessage', 'setInformativeText',
    'setDetailedText', 'setText', 'addAction', 'setGroupTitle',
}

# Qt constructors that take a user-visible string as their first positional arg
UI_STRING_CTORS = {
    'QLabel', 'QPushButton', 'QCheckBox', 'QRadioButton', 'QGroupBox',
    'QAction', 'QMenu', 'QToolButton', 'QCommandLinkButton',
    'QMessageBox', 'QInputDialog', 'QFileDialog',
}

# Localization function/method names — calls to these are always fine
LOC_CALL_NAMES = {
    'localizationTokenText', 'localizationKeyText', 'localizationLabelText',
    'localizationButtonText', 'localizationInputText', 'localizationMenuText',
    'localizationFormatText', 'localized', 'providerDisplayNameLocalized',
    'localize', 'Localize', 'loc', 'Loc', 'locLabel', '_localizedText', 'require',
    'tr', '_', 'gettext', 'ngettext',
}

# Short strings that are clearly not UI copy (empty, punctuation, single char, etc.)
def _is_boring_string(s: str) -> bool:
    stripped = s.strip()
    if not stripped:
        return True
    if len(stripped) <= 2:
        return True
    # purely symbolic / numeric
    if all(c in ' .,;:!?-_/\\|()[]{}<>=+*&^%$#@~`\'"' for c in stripped):
        return True
    return False


def _is_loc_call(node: ast.expr) -> bool:
    """Return True if this expression is a localization call or clearly not a raw literal."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in LOC_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in LOC_CALL_NAMES:
        return True
    return False


def _first_arg_is_raw_string(call: ast.Call) -> tuple[bool, str]:
    """Return (flagged, string_value) if the first arg is a raw string literal."""
    if not call.args:
        return False, ''
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return True, first.value
    # f-string with no dynamic parts
    if isinstance(first, ast.JoinedStr) and not any(isinstance(v, ast.FormattedValue) for v in first.values):
        parts = [v.value for v in first.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        return True, ''.join(parts)
    return False, ''


def _has_ok(lines: list[str], lineno: int) -> bool:
    snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
    return any(m in snippet for m in OK_MARKERS)


def _sample(lines: list[str], lineno: int) -> str:
    return lines[lineno - 1].strip()[:120] if 0 < lineno <= len(lines) else ''


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        text = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception:
        InsertDebuggerException("unlocalizeddetector.py:97", "handled exception")
        return []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        InsertDebuggerException("unlocalizeddetector.py:102", "handled exception")
        return []

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

        # method call: widget.setText("Hello")
        if isinstance(func, ast.Attribute):
            call_name = func.attr
            if call_name in UI_STRING_METHODS:
                is_ui_call = True

        # constructor call: QLabel("Hello"), QPushButton("Save")
        elif isinstance(func, ast.Name):
            call_name = func.id
            if call_name in UI_STRING_CTORS:
                is_ui_call = True

        if not is_ui_call:
            continue

        flagged, value = _first_arg_is_raw_string(node)
        if not flagged:
            continue
        if _is_boring_string(value):
            continue

        findings.append((lineno, call_name,
            f"Unlocalized string '{value[:60]}' passed directly to {call_name}(): {_sample(lines, lineno)}"))

    findings.sort(key=lambda x: x[0])
    return findings


def iter_py(paths: list[Path], root: Path) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []

    def enqueue(p: Path) -> None:
        p = p.resolve()
        if p in seen or not p.exists():
            return
        try:
            parts = p.relative_to(root).parts
        except ValueError:
            InsertDebuggerException("unlocalizeddetector.py:156", "handled exception")
            return
        if any(part in SKIP_DIR_NAMES for part in parts):
            return
        seen.add(p)
        out.append(p)

    for raw in paths:
        p = Path(raw).resolve()
        if p.is_file() and p.suffix == '.py':
            enqueue(p)
        elif p.is_dir():
            for child in p.rglob('*.py'):
                enqueue(child)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='Detect unlocalized UI strings passed directly to Qt calls.')
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', required=True)
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root / 'start.py']
    files = iter_py(raw_paths, root)

    all_findings: list[tuple[Path, int, str, str]] = []
    for f in files:
        for lineno, call_name, msg in scan_file(f):
            all_findings.append((f, lineno, call_name, msg))

    all_findings.sort(key=lambda x: (str(x[0]), x[1]))

    out = Path(ns.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    by_call: dict[str, int] = {}
    for _, _, call_name, _ in all_findings:
        by_call[call_name] = by_call.get(call_name, 0) + 1

    report_lines = [
        'UNLOCALIZED STRING DETECTOR REPORT',
        '===================================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Findings: {len(all_findings)}',
    ]
    if by_call:
        report_lines.append('')
        report_lines.append('By call type:')
        for call_name, count in sorted(by_call.items(), key=lambda x: -x[1]):
            report_lines.append(f'  {call_name}(): {count}')
    report_lines.append('')  # noqa: redundant
    report_lines.append('Suppress a finding by adding  # // localized  to the same line.')  # noqa: redundant
    report_lines.append('')  # noqa: redundant

    for f, lineno, _, msg in all_findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        report_lines.append(f'{rel}:{lineno}: {msg}')

    if not all_findings:
        report_lines.append('No unlocalized UI strings found.')

    text = '\n'.join(report_lines) + '\n'
    tracedWriteText(out, text, encoding='utf-8')
    try:
        print(text)
    except UnicodeEncodeError:
        InsertDebuggerException("unlocalizeddetector.py:227", "handled exception")
        enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))
    return 1 if all_findings else 0


if __name__ == '__main__':
    os._exit(int(main()))
