#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, collections, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

BLOCKED_CALLS = {
    'subprocess.Popen': 'DIRECT_POPEN',
    'subprocess.run': 'DIRECT_RUN',
    'subprocess.call': 'DIRECT_CALL',
    'subprocess.check_call': 'DIRECT_CHECK_CALL',
    'subprocess.check_output': 'DIRECT_CHECK_OUTPUT',
    'subprocess.getoutput': 'DIRECT_GETOUTPUT',
    'threading.Thread': 'DIRECT_THREAD',
    'multiprocessing.Process': 'DIRECT_PROCESS',
    'Process': 'DIRECT_PROCESS',
    'os.system': 'OS_SYSTEM',
    'os.popen': 'OS_POPEN',
    'os.execv': 'OS_EXEC',
    'os.execve': 'OS_EXEC',
    'os.execvp': 'OS_EXEC',
    'os.spawnl': 'OS_SPAWN',
    'os.spawnle': 'OS_SPAWN',
    'os.spawnlp': 'OS_SPAWN',
    'os.spawnv': 'OS_SPAWN',
    'QThread': 'DIRECT_QTHREAD',
}
ALLOW_FUNCS = {
    'launcherRunCommand', 'launcherStartProcess', '_early_run_command',
    '_roughRunCommand', 'attemptPlainPipInstall', 'StartWorkerProcess',
    'managedSubprocessRun', 'managedSubprocessPopen', 'lifecycleSubprocessRun',
    'lifecycleSubprocessPopen', 'runClaudeDetector', 'applicationRun',
}
OK_MARKERS = {'lifecycle-bypass-ok', 'noqa: lifecycle-bypass'}
SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}


def dottedName(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return '.'.join(reversed(parts))


def buildParentMap(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def functionAt(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    current = node
    while id(current) in parents:
        current = parents[id(current)]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return '<module>'


def lineHasOk(lines: list[str], lineno: int) -> bool:
    snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
    return any(marker in snippet for marker in OK_MARKERS)


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
    dq: collections.deque[Path] = collections.deque()

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
        dq.append(resolvedPath)

    for rawPath in paths:
        path = Path(rawPath).resolve()
        if path.is_file() and path.suffix == '.py':
            enqueue(path)
        elif path.is_dir():
            for childPath in path.rglob('*.py'):
                enqueue(childPath)

    files: list[Path] = []
    while dq:
        filePath = dq.popleft()
        files.append(filePath)
        try:
            source = tracedReadText(filePath, encoding='utf-8', errors='replace')
            tree = ast.parse(source, filename=str(filePath))
        except Exception as exc:
            InsertDebuggerException('iterPy.parse_imports', exc, str(filePath))
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
        InsertDebuggerException('lifecycle.scan.read', exc, str(path))
        return [(0, 'READ', str(exc))]
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        InsertDebuggerException('lifecycle.scan.parse', exc, str(path))
        return [(getattr(exc, 'lineno', 0) or 0, 'PARSE', str(exc))]
    parents = buildParentMap(tree)
    rows: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if lineHasOk(lines, lineno):
            continue
        functionName = functionAt(node, parents)
        if functionName in ALLOW_FUNCS:
            continue
        rule = BLOCKED_CALLS.get(dottedName(node.func))
        if rule:
            sample = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
            rows.append((lineno, rule, f'{functionName}: {sample[:220]}'))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--output', required=True)
    parser.add_argument('paths', nargs='*')
    namespace = parser.parse_args()
    root = Path(namespace.root).resolve()
    rawPaths = [Path(path).resolve() for path in namespace.paths] if namespace.paths else [root]
    files = iterPy(rawPaths, root)
    findings: list[tuple[Path, int, str, str]] = []
    for filePath in files:
        for lineNumber, rule, message in scan(filePath):
            findings.append((filePath, lineNumber, rule, message))
    out = Path(namespace.output)
    out.parent.mkdir(parents=True, exist_ok=True)  # file-io-ok
    lines = [
        'LIFECYCLE BYPASS DETECTOR REPORT',
        '================================',
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
        lines.append('No obvious direct process/thread lifecycle bypasses found in scanned app paths.')
    text = '\n'.join(lines) + '\n'
    tracedWriteText(out, text, encoding='utf-8')
    print(text)
    return 1 if findings else 0


if __name__ == '__main__':
    os._exit(int(main()))
