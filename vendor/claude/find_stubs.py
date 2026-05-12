#!/usr/bin/env python3
"""Detect stub functions/methods in CutiePy source files.

Mark a stub as intentional with a trailing comment:  # stub-ok
or add it to vendor/claude/stubs_ignore.txt as  filename:funcname

Usage:
    python start.py --stubs
    python vendor/claude/find_stubs.py [file ...]
"""
from __future__ import annotations
import ast, sys
from pathlib import Path

DEFAULT_TARGETS = [
    'cutiepy.py', 'cutiepy.headless.py',
    'classes/_config.py', 'classes/_lifecycle.py',
    'classes/_themes.py', 'classes/_localization.py', 'classes/_updates.py',
]

IGNORE_FILE = Path(__file__).parent / 'stubs_ignore.txt'

def load_ignore_list() -> set[str]:
    """Load file:funcname pairs from stubs_ignore.txt."""
    ignores: set[str] = set()
    if IGNORE_FILE.exists():
        for line in IGNORE_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                ignores.add(line.lower())
    return ignores

def is_stub_ok(src_lines: list[str], lineno: int) -> bool:
    """Return True if the function's def line has a # stub-ok comment."""
    line = src_lines[lineno - 1] if 0 < lineno <= len(src_lines) else ''
    return '# stub-ok' in line

def classify_body(body):
    real = []
    for i, s in enumerate(body):
        if i == 0 and isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
            continue  # skip docstring
        real.append(s)
    if not real: return 'pass_only'
    if len(real) == 1:
        s = real[0]
        if isinstance(s, ast.Pass): return 'pass_only'
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is ...: return 'ellipsis_only'
        if isinstance(s, ast.Return):
            v = s.value
            if v is None: return 'return_none'
            if isinstance(v, ast.Constant):
                if v.value is None:  return 'return_none'
                if v.value is False: return 'return_false'
                if v.value == 0:     return 'return_zero'
                if v.value == '':    return 'return_empty_str'
            if isinstance(v, ast.Dict) and not v.keys:  return 'return_empty_dict'
            if isinstance(v, ast.List) and not v.elts:  return 'return_empty_list'
            if isinstance(v, ast.Tuple) and not v.elts: return 'return_empty_tuple'
            if isinstance(v, ast.Name) and v.id in ('None', 'EMPTY_STRING'): return 'return_none'
            if isinstance(v, ast.Attribute) and v.attr == 'EMPTY_STRING': return 'return_empty_string_const'
    return None

def scan_file(path: Path, ignores: set[str]) -> list[tuple[int, str, str]]:
    try:
        src = path.read_text(encoding='utf-8', errors='replace')
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        print(f'  PARSE ERROR {path}: {e}', file=sys.stderr)
        return []
    src_lines = src.splitlines()
    rel_name = path.name
    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = classify_body(node.body)
            if not kind:
                continue
            if is_stub_ok(src_lines, node.lineno):
                continue
            key = f'{rel_name}:{node.name}'.lower()
            if key in ignores:
                continue
            results.append((node.lineno, node.name, kind))
    results.sort()
    return results

def run(base, targets):
    ignores = load_ignore_list()
    total = 0
    for rel in targets:
        p = Path(base) / rel
        if not p.exists():
            print(f'  MISSING: {rel}', file=sys.stderr)
            continue
        hits = scan_file(p, ignores)
        if hits:
            print(f'\n=== {rel} ({len(hits)} stubs) ===')
            for lineno, name, kind in hits:
                print(f'  line {lineno:>6}: {name}  [{kind}]')
            total += len(hits)
    print(f'\nTotal stubs found: {total}')
    return total

if __name__ == '__main__':
    base = Path(__file__).resolve().parent.parent.parent
    targets = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_TARGETS
    sys.exit(0 if run(base, targets) == 0 else 1)


def scan_peewee(path: Path) -> list[tuple[int, str]]:
    """Find lines importing or using Peewee directly."""
    hits = []
    try:
        src = path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        if 'peewee' in stripped.lower():
            hits.append((i, line.rstrip()))
        elif any(pat in stripped for pat in (
            '.Model.select()', 'pw.Model',
            'DoesNotExist', 'PeeweeException',
        )):
            hits.append((i, line.rstrip()))
    return hits

def run_peewee_scan(base, targets):
    total = 0
    for rel in targets:
        p = Path(base) / rel
        if not p.exists():
            continue
        hits = scan_peewee(p)
        if hits:
            print(f'\n=== PEEWEE: {rel} ({len(hits)} hits) ===')
            for lineno, text in hits:
                print(f'  line {lineno:>6}: {text}')
            total += len(hits)
    if total:
        print(f'\nTotal Peewee references: {total}')
    else:
        print('\nNo Peewee references found.')
    return total
