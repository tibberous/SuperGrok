#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

IGNORED_PARTS = {'vendor', '__pycache__'}
OK_MARKERS = ('phase-ownership-ok', 'qt-main-thread-ok', 'detector-runner-ok')
BLOCKED_NAME_CALLS = {'subprocess.run', 'subprocess.Popen', 'subprocess.call', 'multiprocessing.Process', 'threading.Thread'}
BLOCKED_ATTRS = {'exec', 'exec_', 'start'}

def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def dotted_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = dotted_name(node.value)  # recursion-ok
        return f'{left}.{node.attr}' if left else node.attr
    return ''


def function_at(node, parents):
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return '<module>'


def in_allowed_function(node, parents):
    func = function_at(node, parents)
    return func in {
        'managedSubprocessRun', 'managedSubprocessPopen', 'launcherStartProcess', 'lifecycleSubprocessRun',
        'lifecycleSubprocessPopen', 'lifecycleSubprocessCall', 'runClaudeDetector', 'runPyrite', 'runFrameworkTool', 'runtimeQtExecPhase', 'applicationRun'
    }


def line_has_ok(lines, lineno):
    snippet = '\n'.join(lines[max(0, lineno-2):min(len(lines), lineno+1)])
    return any(marker in snippet for marker in OK_MARKERS)


def scan_file(path: Path, root: Path):
    try:
        text = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception:
        InsertDebuggerException("phaseownershipdetector.py:59", "handled exception")
        return []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        InsertDebuggerException("phaseownershipdetector.py:64", "handled exception")
        return [(path, getattr(e, 'lineno', 0) or 0, 'SYNTAX_ERROR', '<module>', str(e), '')]
    parents = build_parent_map(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if line_has_ok(lines, lineno) or in_allowed_function(node, parents):
            continue
        name = dotted_name(node.func)
        kind = ''
        if name.split('.')[-1] == 'runQtBlockingCall':
            continue
        if name in BLOCKED_NAME_CALLS:
            kind = f'DIRECT_{name}'
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {'exec', 'exec_'}:
            receiver = dotted_name(node.func.value).lower()
            if any(token in receiver for token in ('dialog', 'menu', 'application', 'app')):
                kind = f'DIRECT_QT_{node.func.attr.upper()}'
        if not kind:
            continue
        sample = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
        findings.append((path, lineno, kind, function_at(node, parents), sample[:220]))
    return findings


def iter_py(paths, root: Path):
    for rawPath in paths:
        path = Path(rawPath)
        if path.is_dir():
            for f in path.rglob('*.py'):
                try:
                    parts = f.resolve().relative_to(root).parts
                except ValueError:
                    InsertDebuggerException("phaseownershipdetector.py:98", "handled exception")
                    parts = f.parts
                if any(part in IGNORED_PARTS for part in parts):
                    continue
                yield f
        elif path.exists() and path.suffix == '.py':
            try:
                parts = path.resolve().relative_to(root).parts
            except ValueError:
                InsertDebuggerException("phaseownershipdetector.py:106", "handled exception")
                parts = path.parts
            if not any(part in IGNORED_PARTS for part in parts):
                yield path


def main(argv=None):
    ap = argparse.ArgumentParser(description='Detect direct runtime work that should be lifecycle phase owned.')
    ap.add_argument('--root', '--base-dir', default='.')
    ap.add_argument('--output', default='phase_ownership.txt')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args(argv)
    root = Path(ns.root).resolve()
    targets = [Path(x).resolve() for x in ns.paths] or [root]
    files = list(iter_py(targets, root))
    findings = []
    for f in files:
        findings.extend(scan_file(f, root))
    findings.sort(key=lambda row: (str(row[0]), row[1]))
    lines = ['PHASE OWNERSHIP DETECTOR REPORT', '===============================', '', f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}', f'Root: {root}', f'Files scanned: {len(files)}', f'Findings: {len(findings)}', '']
    for f, lineno, kind, func, sample in findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        lines.append(f'{rel}:{lineno}: {kind} [{func}]')
        lines.append(f'  {sample}')
        lines.append('')
    if not findings:
        lines.append('No direct lifecycle/phase ownership gaps found.')
    report = '\n'.join(lines) + '\n'
    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    tracedWriteText(out, report, encoding='utf-8')
    print(report)
    return 1 if findings else 0

if __name__ == '__main__':
    os._exit(int(main()))
