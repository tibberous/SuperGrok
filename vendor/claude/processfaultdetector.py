#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

IGNORED_PARTS = {'vendor', '__pycache__'}
OK_MARKERS = ('process-fault-ok', 'phase-process-ok')
REQUIRED = {'onError', 'onException', 'onFault'}

def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def line_has_ok(lines, lineno):
    snippet = '\n'.join(lines[max(0, lineno-2):min(len(lines), lineno+1)])
    return any(marker in snippet for marker in OK_MARKERS)


def chain_methods(node):
    methods = []
    found_process = False
    cur = node
    while isinstance(cur, ast.Call):
        fn = cur.func
        if isinstance(fn, ast.Attribute):
            methods.append(fn.attr)
            cur = fn.value
            continue
        if isinstance(fn, ast.Name) and fn.id == 'Process':
            found_process = True
        break
    return found_process, set(methods)


def function_at(node, parents):
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return '<module>'


def scan_file(path: Path, root: Path):
    try:
        text = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception:
        InsertDebuggerException("processfaultdetector.py:57", "handled exception")
        return []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        InsertDebuggerException("processfaultdetector.py:62", "handled exception")
        return [(path, getattr(e, 'lineno', 0) or 0, 'SYNTAX_ERROR', '<module>', str(e))]
    parents = build_parent_map(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in {'spawn', 'start'}):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if line_has_ok(lines, lineno):
            continue
        found, methods = chain_methods(node)
        if not found:
            continue
        missing = sorted(REQUIRED - methods)
        if missing:
            sample = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
            findings.append((path, lineno, 'MISSING_PROCESS_FAULT_CALLBACKS', function_at(node, parents), ','.join(missing), sample[:220]))
    return findings


def iter_py(paths, root: Path):
    for rawPath in paths:
        path = Path(rawPath)
        if path.is_dir():
            for f in path.rglob('*.py'):
                try:
                    parts = f.resolve().relative_to(root).parts
                except ValueError:
                    InsertDebuggerException("processfaultdetector.py:92", "handled exception")
                    parts = f.parts
                if any(part in IGNORED_PARTS for part in parts):
                    continue
                yield f
        elif path.exists() and path.suffix == '.py':
            try:
                parts = path.resolve().relative_to(root).parts
            except ValueError:
                InsertDebuggerException("processfaultdetector.py:100", "handled exception")
                parts = path.parts
            if not any(part in IGNORED_PARTS for part in parts):
                yield path


def main(argv=None):
    ap = argparse.ArgumentParser(description='Detect Process(...).spawn() calls without fault callbacks.')
    ap.add_argument('--root', '--base-dir', default='.')
    ap.add_argument('--output', default='process_faults.txt')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args(argv)
    root = Path(ns.root).resolve()
    targets = [Path(x).resolve() for x in ns.paths] or [root]
    files = list(iter_py(targets, root))
    findings = []
    for f in files:
        findings.extend(scan_file(f, root))
    findings.sort(key=lambda row: (str(row[0]), row[1]))
    lines = ['PROCESS FAULT DETECTOR REPORT', '=============================', '', f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}', f'Root: {root}', f'Files scanned: {len(files)}', f'Findings: {len(findings)}', '']
    for f, lineno, kind, func, missing, sample in findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        lines.append(f'{rel}:{lineno}: {kind} missing={missing} [{func}]')
        lines.append(f'  {sample}')
        lines.append('')
    if not findings:
        lines.append('No process spawn fault callback gaps found.')
    report = '\n'.join(lines) + '\n'
    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    tracedWriteText(out, report, encoding='utf-8')
    print(report)
    return 1 if findings else 0

if __name__ == '__main__':
    os._exit(int(main()))
