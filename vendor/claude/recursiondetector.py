#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, datetime, os, re, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}


def _resolve_import(module_name: str, from_file: Path, root: Path) -> Path | None:
    parts = module_name.split('.')
    for base in (from_file.parent, root):
        candidate = base.joinpath(*parts).with_suffix('.py')
        if candidate.exists():
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                InsertDebuggerException("recursiondetector.py:24", "handled exception")
                pass
        pkg = base.joinpath(*parts) / '__init__.py'
        if pkg.exists():
            resolved = pkg.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                InsertDebuggerException("recursiondetector.py:32", "handled exception")
                pass
    return None


def _imports_from_ast(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def iter_py(paths: list[Path], root: Path) -> list[Path]:
    seen: set[Path] = set()
    queue: list[Path] = []

    def enqueue(p: Path) -> None:
        p = p.resolve()
        if p in seen or not p.exists():
            return
        try:
            parts = p.relative_to(root).parts
        except ValueError:
            InsertDebuggerException("recursiondetector.py:59", "handled exception")
            return
        if any(part in SKIP_DIR_NAMES for part in parts):
            return
        seen.add(p)
        queue.append(p)

    for raw in paths:
        p = Path(raw).resolve()
        if p.is_file() and p.suffix == '.py':
            enqueue(p)
        elif p.is_dir():
            for child in p.rglob('*.py'):
                enqueue(child)

    out: list[Path] = []
    while queue:
        f = queue.pop(0)
        out.append(f)
        try:
            source = tracedReadText(f, encoding='utf-8', errors='replace')
            tree = ast.parse(source, filename=str(f))
            for module_name in _imports_from_ast(tree):
                resolved = _resolve_import(module_name, f, root)
                if resolved:
                    enqueue(resolved)
        except Exception:
            InsertDebuggerException("recursiondetector.py:85", "handled exception")
            pass

    return out
def scan(path):
    rows=[]
    try:
        source=tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception as e:
        InsertDebuggerException("recursiondetector.py:93", "handled exception")
        return [(0,'READ',str(e))]
    try:
        tree=ast.parse(source, filename=str(path))
    except Exception as e:
        InsertDebuggerException("recursiondetector.py:97", "handled exception")
        return [(0,'PARSE',str(e))]
    lines=source.splitlines()
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if 'recursive' in func.name.lower():
            continue
        for node in ast.walk(func):
            if node is func:
                continue
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func.name:
                line=lines[getattr(node,'lineno',1)-1].strip() if 0 < getattr(node,'lineno',0) <= len(lines) else ''
                if 'recursion-ok' not in line:
                    rows.append((getattr(node,'lineno',0) or 0,'DIRECT_RECURSION',f'{func.name}: {line[:220]}'))
    return rows
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); ap.add_argument('--output',required=True); ap.add_argument('paths',nargs='*')
    ns=ap.parse_args(); root=Path(ns.root).resolve()
    raw_paths=[Path(x).resolve() for x in ns.paths] if ns.paths else [root / 'start.py']
    files=iter_py(raw_paths, root)
    findings=[]
    for f in files:
        for ln,rule,msg in scan(f): findings.append((f,ln,rule,msg))
    out=Path(ns.output); out.parent.mkdir(parents=True,exist_ok=True)
    lines=['RECURSION DETECTOR REPORT','=========================','',f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',f'Root: {root}',f'Files scanned: {len(files)}',f'Findings: {len(findings)}','']
    for f,ln,rule,msg in findings:
        rel=os.path.relpath(f,root) if str(f).startswith(str(root)) else str(f); lines.append(f'{rel}:{ln}: {rule} {msg}')
    if not findings: lines.append('No direct self-recursion candidates found in scanned app paths.')
    tracedWriteText(out, '\n'.join(lines)+'\n', encoding='utf-8'); print('\n'.join(lines)); return 1 if findings else 0
if __name__ == '__main__':
    os._exit(int(main()))
