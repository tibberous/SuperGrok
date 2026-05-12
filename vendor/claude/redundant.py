#!/usr/bin/env python3
"""Detects runs of consecutive near-identical lines that should be a loop."""
from __future__ import annotations
import argparse, ast, datetime, os, re, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

MIN_RUN = 3

_STR_RE = re.compile(r"(\"\"\".*?\"\"\"|\'\'\'.*?\'\'\'|\"(?:[^\"\\]|\\.)*\"|\'(?:[^\'\\]|\\.)*\')", re.DOTALL)
_NUM_RE = re.compile(r'\b\d+(\.\d+)?\b')
_NAME_RE = re.compile(r'\b[A-Za-z_]\w*\b')

_KEYWORDS = {
    'self', 'cls', 'True', 'False', 'None', 'and', 'or', 'not', 'in',
    'is', 'if', 'else', 'for', 'return', 'yield', 'with', 'as', 'from',
    'import', 'raise', 'try', 'except', 'finally', 'pass', 'break',
    'continue', 'del', 'lambda', 'class', 'def', 'global', 'nonlocal',
    'assert', 'str', 'int', 'bool', 'float', 'list', 'dict', 'set',
    'tuple', 'len', 'range', 'print', 'type', 'isinstance', 'hasattr',
    'getattr', 'setattr', 'async', 'await',
}

# Shapes that are always boring even if they repeat N times
_SKIP_SHAPE_PREFIXES = (
    'import <ID>',
    'from <ID> import',
)

def _shape(line: str) -> str:
    s = line.strip()
    if not s or s.startswith('#'):
        return ''
    if 'noqa: redundant' in s:
        return ''
    s = _STR_RE.sub('<S>', s)
    s = _NUM_RE.sub('<N>', s)
    def replace_name(m: re.Match) -> str:
        w = m.group(0)
        return w if w in _KEYWORDS else '<ID>'
    s = _NAME_RE.sub(replace_name, s)
    return s


def _shape_is_interesting(shape: str) -> bool:
    """Require the shape to have structural meat — a call, subscript, or attribute access."""
    if any(shape.startswith(p) for p in _SKIP_SHAPE_PREFIXES):
        return False
    # Must contain a call, subscript, or attribute operator to be worth flagging.
    if 'mapped_column(' in shape or shape.startswith('from <ID>.<ID> import') or shape.startswith('from <ID> import'):
        return False
    static_prefixes = (
        '<<ID>>:', '<ID>.<ID>:', '<ID>.<ID>.<ID>:', '(<', '((<', '.<ID>.', '<ID>.<ID>,',
        '<ID>(self.<ID>', '<ID>(<ID>.<ID>', '<ID>.<ID> /', '<ID>.<ID> -',
    )
    if shape.startswith(static_prefixes):
        return False
    if shape.endswith('),') or shape.endswith('), (<<ID>>, <<ID>>, <<ID>>),'):
        return False
    return bool('(' in shape or '[' in shape or '.' in shape)


def _func_at(lines: list[str], idx: int) -> str:
    for i in range(idx, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith('def ') or stripped.startswith('async def '):
            return stripped.split('def ', 1)[1].split('(', 1)[0].strip()
    return '<module>'


def scan(path: Path, min_run: int = MIN_RUN) -> list[tuple[int, int, int, str, str, str]]:
    """Return list of (start_line, end_line, match_count, func, shape, sample) for each run."""
    if path.name in {'vulture_whitelist.py', 'data.py'}:
        return []
    try:
        raw_lines = tracedReadText(path, encoding='utf-8', errors='replace').splitlines()
    except Exception:
        InsertDebuggerException("redundant.py:82", "handled exception")
        return []

    shapes = [_shape(line) for line in raw_lines]
    findings = []
    i = 0
    while i < len(shapes):
        s = shapes[i]
        if not s or not _shape_is_interesting(s):
            i += 1
            continue
        j = i + 1
        while j < len(shapes):
            if shapes[j] == s:
                j += 1
            elif not shapes[j] and j + 1 < len(shapes) and shapes[j + 1] == s:
                # allow one blank line gap inside a run
                j += 2
            else:
                break
        match_count = sum(1 for k in range(i, j) if shapes[k] == s)
        if match_count >= min_run:
            func = _func_at(raw_lines, i)
            sample = raw_lines[i].strip()[:180]
            findings.append((i + 1, j, match_count, func, s[:120], sample))
            i = j
        else:
            i += 1
    return findings


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
                InsertDebuggerException("redundant.py:125", "handled exception")
                pass
        pkg = base.joinpath(*parts) / '__init__.py'
        if pkg.exists():
            resolved = pkg.resolve()
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                InsertDebuggerException("redundant.py:133", "handled exception")
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
            InsertDebuggerException("redundant.py:160", "handled exception")
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
            InsertDebuggerException("redundant.py:186", "handled exception")
            pass

    return out


def _safe_print(text: str) -> None:
    enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
    try:
        print(text)
    except UnicodeEncodeError:
        InsertDebuggerException("redundant.py:196", "handled exception")
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))


def main() -> int:
    ap = argparse.ArgumentParser(description='Detect repetitive sequential lines that should be a loop.')
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', required=True)
    ap.add_argument('--min-run', type=int, default=MIN_RUN, help='Minimum consecutive matching lines to flag (default: 3)')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args()

    min_run = ns.min_run
    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root / 'start.py']
    files = iter_py(raw_paths, root)

    findings: list[tuple[Path, int, int, int, str, str, str]] = []
    for f in files:
        for start, end, count, func, shape, sample in scan(f, min_run=min_run):
            findings.append((f, start, end, count, func, shape, sample))

    # Worst offenders first
    findings.sort(key=lambda x: -x[3])

    out = Path(ns.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    report_lines = [
        'REDUNDANT CODE DETECTOR REPORT',
        '==============================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Min run length: {min_run}',
        f'Findings: {len(findings)}',
        '',
    ]
    for f, start, end, count, func, shape, sample in findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        report_lines.append(f'{rel}:{start}-{end}  ({count} matching lines)  [{func}]')  # noqa: redundant
        report_lines.append(f'  shape: {shape}')  # noqa: redundant
        report_lines.append(f'  first: {sample}')  # noqa: redundant
        report_lines.append('')

    if not findings:
        report_lines.append('No repetitive line runs found.')

    text = '\n'.join(report_lines) + '\n'
    tracedWriteText(out, text, encoding='utf-8')
    _safe_print(text)
    return 1 if findings else 0


if __name__ == '__main__':
    os._exit(int(main()))
