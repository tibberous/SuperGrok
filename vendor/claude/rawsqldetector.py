#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
ALLOW_MARKERS = {'raw-sql-ok', 'noqa: raw-sql', 'Google API .execute', 'google api'}
RAW_CONNECT_NAMES = {'sqlite3.connect', 'pymysql.connect', 'MySQLdb.connect', 'mysql.connector.connect'}
RAW_ATTRS = {'cursor', 'execute', 'executemany', 'executescript'}


def dottedName(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return '.'.join(reversed(parts))


def _lineHasAllow(lines: list[str], lineno: int) -> bool:
    snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
    return any(marker in snippet for marker in ALLOW_MARKERS)


def _resolveImport(moduleName: str, fromFile: Path, root: Path) -> Path | None:
    parts = moduleName.split('.')
    for base in (fromFile.parent, root):
        candidate = base.joinpath(*parts).with_suffix('.py')
        if candidate.exists():
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError as exc:
                InsertDebuggerException('_resolveImport.relative', exc, str(resolved))
        package = base.joinpath(*parts) / '__init__.py'
        if package.exists():
            resolved = package.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError as exc:
                InsertDebuggerException('_resolveImport.package_relative', exc, str(resolved))
    return None


def _importsFromAst(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def iterPy(paths: list[Path], root: Path) -> list[Path]:
    seen: set[Path] = set()
    queue: list[Path] = []

    def enqueue(path: Path) -> None:
        resolvedPath = path.resolve()
        if resolvedPath in seen or not resolvedPath.exists():
            return
        try:
            parts = resolvedPath.relative_to(root).parts
        except ValueError as exc:
            InsertDebuggerException('iterPy.relative', exc, str(resolvedPath))
            return
        if any(part in SKIP_DIR_NAMES for part in parts):
            return
        seen.add(resolvedPath)
        queue.append(resolvedPath)

    for rawPath in paths:
        path = Path(rawPath).resolve()
        if path.is_file() and path.suffix == '.py':
            enqueue(path)
        elif path.is_dir():
            for childPath in path.rglob('*.py'):
                enqueue(childPath)

    files: list[Path] = []
    while queue:
        filePath = queue.pop(0)
        files.append(filePath)
        try:
            source = tracedReadText(filePath, encoding='utf-8', errors='replace')
            tree = ast.parse(source, filename=str(filePath))
        except Exception as exc:
            InsertDebuggerException('rawsql.iterPy.parse_imports', exc, str(filePath))
            continue
        for moduleName in _importsFromAst(tree):
            resolved = _resolveImport(moduleName, filePath, root)
            if resolved:
                enqueue(resolved)
    return files


def scan(path: Path) -> list[tuple[int, str, str]]:
    try:
        source = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception as exc:
        InsertDebuggerException('rawsql.scan.read', exc, str(path))
        return [(0, 'READ', str(exc))]
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        InsertDebuggerException('rawsql.scan.parse', exc, str(path))
        return [(getattr(exc, 'lineno', 0) or 0, 'PARSE', str(exc))]
    rows: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if _lineHasAllow(lines, lineno):
            continue
        name = dottedName(node.func)
        rule = ''
        if name in RAW_CONNECT_NAMES:
            rule = 'RAW_CONNECT'
        elif isinstance(node.func, ast.Attribute) and node.func.attr in RAW_ATTRS:
            lowered = name.lower()
            if any(token in lowered for token in ('service.', '.service.', '.request.', 'gmail', 'calendar', 'google')):
                continue
            rule = f'RAW_{node.func.attr.upper()}'
        if rule:
            sample = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
            rows.append((lineno, rule, sample[:220]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--output', required=True)
    parser.add_argument('paths', nargs='*')
    namespace = parser.parse_args()
    root = Path(namespace.root).resolve()
    rawPaths = [Path(path).resolve() for path in namespace.paths] if namespace.paths else [root / 'start.py']
    files = iterPy(rawPaths, root)
    findings: list[tuple[Path, int, str, str]] = []
    for filePath in files:
        for lineNumber, rule, message in scan(filePath):
            findings.append((filePath, lineNumber, rule, message))
    out = Path(namespace.output)
    out.parent.mkdir(parents=True, exist_ok=True)  # file-io-ok
    lines = [
        'RAW SQL DETECTOR REPORT',
        '=======================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Findings: {len(findings)}',
        '',
    ]
    for filePath, lineNumber, rule, message in findings:
        rel = os.path.relpath(filePath, root) if str(filePath).startswith(str(root)) else str(filePath)
        lines.append(f'{rel}:{lineNumber}: {rule} {message}')
    if not findings:
        lines.append('No raw SQL connector/cursor/execute candidates found in scanned app paths.')
    text = '\n'.join(lines) + '\n'
    tracedWriteText(out, text, encoding='utf-8')
    print(text)
    return 1 if findings else 0


if __name__ == '__main__':
    os._exit(int(main()))
