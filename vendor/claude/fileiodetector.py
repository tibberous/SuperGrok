#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

IGNORED_PARTS = {'vendor', '__pycache__'}
OK_MARKERS = ('file-io-ok', 'lifecycle-file-ok', 'qt-open-ok')
RAW_ATTRS = {'read_text', 'write_text'}
RAW_OPEN_ATTRS = {'open'}
RAW_NAMES = {'open'}
RAW_COPY_ATTRS = {'copy', 'copy2', 'copyfile', 'copytree', 'move'}
RAW_ZIP_NAMES = {'ZipFile'}

def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def line_has_ok(lines: list[str], lineno: int) -> bool:
    start = max(1, lineno - 1)
    end = min(len(lines), lineno + 1)
    snippet = '\n'.join(lines[start - 1:end])
    return any(marker in snippet for marker in OK_MARKERS)


def dotted_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = dotted_name(node.value)  # recursion-ok
        return f'{left}.{node.attr}' if left else node.attr
    return ''


def is_traced_call(node: ast.Call) -> bool:
    name = dotted_name(node.func)
    return name.split('.')[-1] in {
        'tracedReadText', 'tracedWriteText', 'tracedOpen', 'tracedCopy', 'tracedCopy2',
        'tracedCopyFile', 'tracedZipFile', 'managedFileReadText', 'managedFileWriteText',
        'managedFileOpen', 'managedCopy2', 'managedZipFile'
    }


def call_kind(node: ast.Call) -> str:
    if is_traced_call(node):
        return ''
    fn = node.func
    if isinstance(fn, ast.Name) and fn.id in RAW_NAMES:
        return 'RAW_OPEN'
    if isinstance(fn, ast.Name) and fn.id in RAW_ZIP_NAMES:
        return 'RAW_ZIPFILE'
    if isinstance(fn, ast.Attribute):
        if fn.attr in RAW_ATTRS:
            return f'RAW_PATH_{fn.attr.upper()}'
        if fn.attr in RAW_OPEN_ATTRS:
            value_name = dotted_name(fn.value).lower()
            if any(token in value_name for token in ('dialog', 'menu', 'buffer', 'iodevice', 'filedialog')):
                return ''
            return 'RAW_PATH_OPEN'
        if fn.attr in RAW_COPY_ATTRS:
            base = dotted_name(fn.value).lower()
            if base.endswith('shutil') or base == 'shutil':
                return f'RAW_SHUTIL_{fn.attr.upper()}'
    return ''


def function_at(node: ast.AST, parents: dict[int, ast.AST]) -> str:
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
        InsertDebuggerException("fileiodetector.py:88", "handled exception")
        return []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        InsertDebuggerException("fileiodetector.py:93", "handled exception")
        return [(path, getattr(e, 'lineno', 0) or 0, 'SYNTAX_ERROR', '<module>', str(e))]
    parents = build_parent_map(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if line_has_ok(lines, lineno):
            continue
        kind = call_kind(node)
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
                    InsertDebuggerException("fileiodetector.py:118", "handled exception")
                    parts = f.parts
                if any(part in IGNORED_PARTS for part in parts):
                    continue
                yield f
        elif path.exists() and path.suffix == '.py':
            try:
                parts = path.resolve().relative_to(root).parts
            except ValueError:
                InsertDebuggerException("fileiodetector.py:126", "handled exception")
                parts = path.parts
            if not any(part in IGNORED_PARTS for part in parts):
                yield path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Detect raw file I/O that bypasses traced lifecycle wrappers.')
    ap.add_argument('--root', '--base-dir', default='.')
    ap.add_argument('--output', default='fileio.txt')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args(argv)
    root = Path(ns.root).resolve()
    targets = [Path(x).resolve() for x in ns.paths] or [root]
    files = list(iter_py(targets, root))
    findings = []
    for f in files:
        findings.extend(scan_file(f, root))
    findings.sort(key=lambda row: (str(row[0]), row[1]))
    lines = [
        'FILE I/O DETECTOR REPORT',
        '========================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Findings: {len(findings)}',
        '',
    ]
    for f, lineno, kind, func, sample in findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        lines.append(f'{rel}:{lineno}: {kind} [{func}]')
        lines.append(f'  {sample}')
        lines.append('')
    if not findings:
        lines.append('No raw file I/O bypasses found.')
    report = '\n'.join(lines) + '\n'
    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    tracedWriteText(out, report, encoding='utf-8')
    print(report)
    return 1 if findings else 0

if __name__ == '__main__':
    os._exit(int(main()))
