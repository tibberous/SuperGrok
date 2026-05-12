#!/usr/bin/env python3
"""
Nonconformance detector.

Three checks:
  1. PREFIX POLLUTION — names inside a class that redundantly repeat the
     app/class name prefix (e.g. a method named getCutiePyFoo inside CutiePy).
     Uses pyphen to syllabically decompose camelCase segments so that e.g.
     "CutiePy" -> ["cu", "tie", "py"] and can
     be matched against identifier segments regardless of casing.

  2. REQUIRED SYMBOLS — verifies that a set of canonical classes, functions,
     and method calls exist somewhere in the scanned project. Missing symbols
     are flagged so you know if a rename silently dropped a core surface.

  3. BANNED CONSTRUCTS — enforces that Thread (threading.Thread or the app's
     Thread wrapper) is never used directly. All async work must go through
     Process. Also flags direct use of threading.Thread.
"""
from __future__ import annotations
import argparse, ast, datetime, os, subprocess, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
OK_MARKER = 'noqa: nonconform'
THREAD_OK_MARKER = 'thread-ok'
_OK_SET = frozenset({OK_MARKER})

# Names that are banned — use Process instead
BANNED_THREAD_NAMES = {'Thread', 'threading'}

# Import patterns that are banned
BANNED_THREAD_IMPORTS = {
    ('threading', None),         # import threading
    ('threading', 'Thread'),     # from threading import Thread
    ('classes._thread', 'Thread'),  # from classes._thread import Thread
}

# ── pyphen bootstrap ──────────────────────────────────────────────────────────

def _ensure_pyphen():
    try:
        import pyphen  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install', 'pyphen'],
            check=True, capture_output=True,
        )
        import pyphen  # noqa: F401
        return True
    except Exception:
        return False

_PYPHEN_OK = _ensure_pyphen()

def _syllables(word: str) -> list[str]:
    """Return lowercase syllables for word, or [word.lower()] if pyphen unavailable."""
    if not _PYPHEN_OK or not word:
        return [word.lower()]
    try:
        import pyphen
        dic = pyphen.Pyphen(lang='en_US')
        return [s.lower() for s in dic.inserted(word).split('-') if s]
    except Exception:
        return [word.lower()]


# ── camelCase / PascalCase splitter ───────────────────────────────────────────

import re as _re

_CAMEL_RE = _re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+')

def _split_identifier(name: str) -> list[str]:
    """Split a camelCase/PascalCase/snake_case identifier into lowercase tokens."""
    parts = name.replace('__', '_').split('_')
    tokens: list[str] = []
    for part in parts:
        tokens.extend(m.group(0).lower() for m in _CAMEL_RE.finditer(part))
    return [t for t in tokens if t]


def _syllable_set(name: str) -> set[str]:
    """All lowercase syllables across all tokens in an identifier."""
    result: set[str] = set()
    for token in _split_identifier(name):
        result.update(_syllables(token))
    return result


# ── App-name syllables to treat as "prefix pollution" ─────────────────────────
# Pulled from: CutiePy, cutiepy, 
# We flag members whose names contain ALL syllables of a banned prefix.

APP_PREFIXES: list[tuple[str, frozenset[str]]] = []

def _register_prefix(label: str, *words: str) -> None:
    syl: set[str] = set()
    for w in words:
        syl.update(_syllables(w))
    if syl:
        APP_PREFIXES.append((label, frozenset(syl)))

_register_prefix('CutiePy',     'cutie', 'py')




def _has_prefix_pollution(member_name: str, class_name: str) -> str | None:
    """Return the matched prefix label if member_name redundantly contains it, else None."""
    class_syls = _syllable_set(class_name)
    member_syls = _syllable_set(member_name)
    for label, prefix_syls in APP_PREFIXES:
        # only flag if the class itself already carries the prefix
        if not prefix_syls.issubset(class_syls):
            continue
        # and the member name ALSO carries it (redundant)
        if prefix_syls.issubset(member_syls):
            return label
    return None


# ── Required symbols ──────────────────────────────────────────────────────────
# (kind, name, description)
# kind: 'class' | 'function' | 'call' | 'attr'

REQUIRED_SYMBOLS: list[tuple[str, str, str]] = [
    # Core fault surface
    ('function', 'recordException',              'Core fault recorder — every except block should use this'),
    ('function', 'InsertDebuggerException',      'DB fault insert — required on Database model'),
    # Process / thread wrappers
    ('class',    'StartProcess',                 'Managed process wrapper in start.py'),
    ('class',    'StartPhase',                   'Lifecycle phase wrapper in start.py'),
    ('class',    'StartDaemon',                  'Daemon wrapper in start.py'),
    ('function', 'managedSubprocessRun',         'Only legal subprocess.run wrapper'),
    ('function', 'lifecycleSubprocessRun',       'Lifecycle-owned subprocess.run wrapper'),
    # Lifecycle
    ('function', 'registerPhase',                'Phase registration on appLifeCycle'),
    ('call',     'appLifeCycle',                 'Application lifecycle controller instance'),
    ('class',    'ApplicationLifeCycleController','Main lifecycle controller class'),
    # Qt thread safety
    ('function', 'runQtBlockingCall',            'Qt main-thread marshalling wrapper'),
    # Dependency management
    ('class',    'Dependency',                   'Single dependency descriptor'),
    ('class',    'Dependencies',                 'Dependency collection manager'),
    # UI base classes
    ('class',    'DialogBase',                   'Base class all dialogs must inherit'),
    ('class',    'BrowserLifecycleController',   'Qt browser lifecycle manager'),
    ('class',    'LocalizedWidget',              'Mixin for localized Qt widgets'),
    # Localization
    ('function', 'localize',                     'Single localization entry point — replaces typed variants'),
    # Thread / process runtime
    ('class',    'Thread',                       'threading.Thread or wrapper'),
    ('class',    'Phase',                        'Runtime Phase class in classes/'),
    ('class',    'Process',                      'Runtime Process class in classes/'),
    # SQLAlchemy surface
    ('function', 'ormColumn',                    'ORM column accessor helper'),
    # Color / theme
    ('class',    'Color',                        'App color/theme class'),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sample(lines: list[str], lineno: int) -> str: return sample_line(lines, lineno, 100)
def _has_ok(lines: list[str], lineno: int) -> bool: return has_ok_marker(lines, lineno, _OK_SET)


def _member_names(classdef: ast.ClassDef) -> list[tuple[str, int]]:
    """Return (name, lineno) for all direct method defs and class-level assignments."""
    names: list[tuple[str, int]] = []
    for node in classdef.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append((node.name, node.lineno))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append((t.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append((node.target.id, node.lineno))
    return names


# ── Check 1: prefix pollution ─────────────────────────────────────────────────

def check_prefix_pollution(tree: ast.AST, lines: list[str], path: Path) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        class_name = node.name
        for member_name, lineno in _member_names(node):
            if _has_ok(lines, lineno):
                continue
            if member_name.startswith('_'):
                continue  # private/dunder — skip
            matched = _has_prefix_pollution(member_name, class_name)
            if matched:
                findings.append((lineno, 'PREFIX_POLLUTION',
                    f"'{member_name}' inside '{class_name}' redundantly repeats the '{matched}' app prefix"))
    return findings


# ── Check 2: banned constructs (Thread) ──────────────────────────────────────

def check_banned_constructs(tree: ast.AST, lines: list[str], path: Path) -> list[tuple[int, str, str]]:
    """Flag any use of Thread/threading — all async work must use Process."""
    findings: list[tuple[int, str, str]] = []

    def _has_thread_ok(lineno: int) -> bool:
        snippet = '\n'.join(lines[max(0, lineno - 3):min(len(lines), lineno + 1)])
        return THREAD_OK_MARKER in snippet

    for node in ast.walk(tree):
        # Flag banned imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ('threading',) or alias.name.startswith('threading.'):
                    lineno = node.lineno
                    if not _has_thread_ok(lineno):
                        findings.append((lineno, 'BANNED_THREAD',
                            f"'import {alias.name}' — use Process, not Thread/threading"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if mod in ('threading', 'classes._thread') or mod.startswith('threading.'):
                for alias in node.names:
                    lineno = node.lineno
                    if not _has_thread_ok(lineno):
                        findings.append((lineno, 'BANNED_THREAD',
                            f"'from {mod} import {alias.name}' — use Process, not Thread/threading"))
        # Flag Thread(...) constructor calls
        elif isinstance(node, ast.Call):
            fn = node.func
            name = ''
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = fn.attr
            if name == 'Thread':
                lineno = getattr(node, 'lineno', 0)
                if not _has_thread_ok(lineno):
                    findings.append((lineno, 'BANNED_THREAD',
                        "Thread(...) — use Process(...) for all async work"))
        # Flag threading.Thread attribute access
        elif isinstance(node, ast.Attribute):
            if node.attr == 'Thread' and isinstance(node.value, ast.Name) and node.value.id == 'threading':
                lineno = getattr(node, 'lineno', 0)
                if not _has_thread_ok(lineno):
                    findings.append((lineno, 'BANNED_THREAD',
                        "threading.Thread — use Process(...) for all async work"))

    return findings


# ── Check 4: missing return on some paths ────────────────────────────────────

def _path_always_returns(stmts: list[ast.stmt]) -> bool:
    """True if every execution path through stmts ends with return/raise."""
    if not stmts:
        return False
    for stmt in reversed(stmts):
        if isinstance(stmt, (ast.Return, ast.Raise)):
            return True
        if isinstance(stmt, ast.If):
            if stmt.orelse and _path_always_returns(stmt.body) and _path_always_returns(stmt.orelse):  # recursion-ok
                return True
        if isinstance(stmt, ast.Try):
            bodies = [stmt.body] + [h.body for h in stmt.handlers]
            if all(_path_always_returns(b) for b in bodies):  # recursion-ok
                return True
        break
    return False


def _func_has_explicit_return(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            return True
    return False


def check_inconsistent_return(tree: ast.AST, lines: list[str], path: Path) -> list[tuple[int, str, str]]:
    """Flag functions that return a value on some paths but fall off the end on others."""
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _func_has_explicit_return(node):
            continue
        if _path_always_returns(node.body):
            continue
        lineno = node.lineno
        if _has_ok(lines, lineno):
            continue
        findings.append((lineno, 'INCONSISTENT_RETURN',
            f"'{node.name}' returns a value on some paths but falls off the end on others"))
    return findings


# ── Check 5: over-exposed state (public self.x never read externally) ─────────

def check_state_exposure(tree: ast.AST, lines: list[str], path: Path) -> list[tuple[int, str, str]]:
    """Flag public self.x attributes that are only ever accessed as self.x (should be _x)."""
    findings: list[tuple[int, str, str]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        # Collect all attrs set as self.x in __init__
        init_attrs: dict[str, int] = {}
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == '__init__':
                for node in ast.walk(method):
                    if (isinstance(node, ast.Assign)):
                        for t in node.targets:
                            if (isinstance(t, ast.Attribute)
                                    and isinstance(t.value, ast.Name)
                                    and t.value.id == 'self'
                                    and not t.attr.startswith('_')):
                                init_attrs[t.attr] = node.lineno
        if not init_attrs:
            continue
        # Check if any attr is accessed externally (via something other than self.x)
        # Collect all attribute reads in the whole file
        external_reads: set[str] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and not (isinstance(node.value, ast.Name) and node.value.id == 'self')):
                external_reads.add(node.attr)
        for attr, lineno in init_attrs.items():
            if attr in external_reads:
                continue
            if _has_ok(lines, lineno):
                continue
            findings.append((lineno, 'STATE_EXPOSURE',
                f"'{cls.name}.{attr}' is public but never accessed externally — prefix with _ to make it private"))
    return findings


# ── Check 6: async/await misuse ───────────────────────────────────────────────

def check_async_misuse(tree: ast.AST, lines: list[str], path: Path) -> list[tuple[int, str, str]]:
    """Flag async defs that never await, and asyncio.sleep(0) yield hacks."""
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        lineno = node.lineno
        if _has_ok(lines, lineno):
            continue
        has_await = any(isinstance(n, ast.Await) for n in ast.walk(node))
        if not has_await:
            findings.append((lineno, 'ASYNC_NO_AWAIT',
                f"'async def {node.name}' never awaits anything — remove async or add an await"))
        # asyncio.sleep(0) inside any async func = yield-control hack
        for n in ast.walk(node):
            if not isinstance(n, ast.Await):
                continue
            call = n.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else '')
            if name == 'sleep' and call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == 0:
                sl = getattr(n, 'lineno', lineno)
                if not _has_ok(lines, sl):
                    findings.append((sl, 'ASYNC_SLEEP_ZERO',
                        f"'await asyncio.sleep(0)' is a yield-control hack — use a Phase or Process instead"))
    return findings


# ── Check 3: required symbols ─────────────────────────────────────────────────

def collect_defined_symbols(files: list[Path]) -> dict[str, set[str]]:
    """Return sets of found class names, function names, and call names across all files."""
    found: dict[str, set[str]] = {'class': set(), 'function': set(), 'call': set(), 'attr': set()}
    for path in files:
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
            tree = ast.parse(text, filename=str(path))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef,)):
                found['class'].add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found['function'].add(node.name)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    found['call'].add(node.func.id)
                    found['function'].add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    found['call'].add(node.func.attr)
                    found['attr'].add(node.func.attr)
    return found


def check_required_symbols(found: dict[str, set[str]]) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for kind, name, description in REQUIRED_SYMBOLS:
        present = False
        if kind == 'class':
            present = name in found['class']
        elif kind == 'function':
            present = name in found['function'] or name in found['call'] or name in found['attr']
        elif kind == 'call':
            present = name in found['call'] or name in found['function'] or name in found['attr']
        elif kind == 'attr':
            present = name in found['attr']
        if not present:
            missing.append((name, description))
    return missing


# ── File iteration ────────────────────────────────────────────────────────────

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description='Detect naming nonconformances and missing required symbols.')
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', required=True)
    ap.add_argument('--no-prefix-check', action='store_true', help='Skip prefix pollution check')
    ap.add_argument('--no-symbol-check', action='store_true', help='Skip required-symbol check')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root]
    files = iter_py(raw_paths, root)

    pollution_findings: list[tuple[Path, int, str, str]] = []
    banned_findings: list[tuple[Path, int, str, str]] = []
    missing_symbols: list[tuple[str, str]] = []

    for f in files:
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
            lines = text.splitlines()
            tree = ast.parse(text, filename=str(f))
        except Exception:
            continue
        if not ns.no_prefix_check:
            for lineno, code, msg in check_prefix_pollution(tree, lines, f):
                pollution_findings.append((f, lineno, code, msg))
        for lineno, code, msg in check_banned_constructs(tree, lines, f):
            banned_findings.append((f, lineno, code, msg))

    if not ns.no_symbol_check:
        found = collect_defined_symbols(files)
        missing_symbols = check_required_symbols(found)

    pollution_findings.sort(key=lambda x: (str(x[0]), x[1]))
    banned_findings.sort(key=lambda x: (str(x[0]), x[1]))

    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)

    total = len(pollution_findings) + len(banned_findings) + len(missing_symbols)

    report_lines = [
        'NONCONFORMANCE DETECTOR REPORT',
        '==============================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'pyphen available: {_PYPHEN_OK}',
        f'Prefix pollution findings: {len(pollution_findings)}',
        f'Banned construct findings: {len(banned_findings)}',
        f'Missing required symbols: {len(missing_symbols)}',
        f'Total findings: {total}',
        '',
    ]

    if banned_findings:
        report_lines.append('── BANNED CONSTRUCTS (Thread / threading) ────────────────────')
        report_lines.append('All async work must use Process. Thread/threading is not allowed.')
        report_lines.append('Suppress intentional exceptions with:  # thread-ok')
        report_lines.append('')
        for f, lineno, code, msg in banned_findings:
            rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
            report_lines.append(f'{rel}:{lineno}: [{code}] {msg}')
        report_lines.append('')

    if pollution_findings:
        report_lines.append('── PREFIX POLLUTION ──────────────────────────────────────────')
        report_lines.append('Members whose names redundantly repeat the app/class name prefix.')
        report_lines.append('Suppress with:  # noqa: nonconform')
        report_lines.append('')
        for f, lineno, code, msg in pollution_findings:
            rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
            report_lines.append(f'{rel}:{lineno}: [{code}] {msg}')
        report_lines.append('')

    if missing_symbols:
        report_lines.append('── MISSING REQUIRED SYMBOLS ──────────────────────────────────')
        report_lines.append('These canonical names were not found anywhere in the scanned files.')
        report_lines.append('A missing symbol may mean a rename silently dropped a core surface.')
        report_lines.append('')
        for name, description in missing_symbols:
            report_lines.append(f'  MISSING  {name}')
            report_lines.append(f'           {description}')
            report_lines.append('')

    if not total:
        report_lines.append('No nonconformances found.')

    text = '\n'.join(report_lines) + '\n'
    out.write_text(text, encoding='utf-8')
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))
    return 1 if total else 0


from vendor.claude.detector_base import Detector, Finding, sample_line, has_ok_marker
from vendor.claude.detector_runtime import tracedWriteText


class NonconformDetector(Detector):
    NAME = 'nonconform'
    VERSION = '2.0.0'
    REPORT_HEADER = 'NONCONFORMANCE DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/nonconform.txt'

    def __init__(self):
        self._no_prefix_check = False
        self._no_symbol_check = False
        self._all_files: list[Path] = []

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        findings: list[Finding] = []
        if not self._no_prefix_check:
            for lineno, code, msg in check_prefix_pollution(tree, lines, path):
                findings.append(Finding(path, lineno, 0, 'MEDIUM', code, msg, _sample(lines, lineno)))
        for lineno, code, msg in check_banned_constructs(tree, lines, path):
            findings.append(Finding(path, lineno, 0, 'HIGH', code, msg, _sample(lines, lineno)))
        for lineno, code, msg in check_inconsistent_return(tree, lines, path):
            findings.append(Finding(path, lineno, 0, 'MEDIUM', code, msg, _sample(lines, lineno)))
        for lineno, code, msg in check_state_exposure(tree, lines, path):
            findings.append(Finding(path, lineno, 0, 'LOW', code, msg, _sample(lines, lineno)))
        for lineno, code, msg in check_async_misuse(tree, lines, path):
            findings.append(Finding(path, lineno, 0, 'MEDIUM', code, msg, _sample(lines, lineno)))
        self._all_files.append(path)
        return findings

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        missing_symbols: list[tuple[str, str]] = []
        if not self._no_symbol_check:
            found = collect_defined_symbols(files)
            missing_symbols = check_required_symbols(found)

        pollution = [f for f in findings if f.rule == 'PREFIX_POLLUTION']
        banned = [f for f in findings if f.rule == 'BANNED_THREAD']
        inc_return = [f for f in findings if f.rule == 'INCONSISTENT_RETURN']
        state_exp = [f for f in findings if f.rule == 'STATE_EXPOSURE']
        async_mis = [f for f in findings if f.rule in ('ASYNC_NO_AWAIT', 'ASYNC_SLEEP_ZERO')]
        total = len(findings) + len(missing_symbols)

        lines_out = [
            self.REPORT_HEADER,
            '=' * len(self.REPORT_HEADER),
            f'Version: {self.VERSION}',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'pyphen available: {_PYPHEN_OK}',
            f'Prefix pollution findings: {len(pollution)}',
            f'Banned construct findings: {len(banned)}',
            f'Inconsistent return findings: {len(inc_return)}',
            f'State exposure findings: {len(state_exp)}',
            f'Async misuse findings: {len(async_mis)}',
            f'Missing required symbols: {len(missing_symbols)}',
            f'Total findings: {total}',
            '',
        ]

        if banned:
            lines_out += [
                '── BANNED CONSTRUCTS (Thread / threading) ────────────────────',
                'All async work must use Process. Thread/threading is not allowed.',
                'Suppress intentional exceptions with:  # thread-ok',
                '',
            ]
            for f in sorted(banned, key=lambda x: (str(x.path), x.line)):
                lines_out.append(f.render(root))
            lines_out.append('')

        if pollution:
            lines_out += [
                '── PREFIX POLLUTION ──────────────────────────────────────────',
                'Members whose names redundantly repeat the app/class name prefix.',
                'Suppress with:  # noqa: nonconform',
                '',
            ]
            for f in sorted(pollution, key=lambda x: (str(x.path), x.line)):
                lines_out.append(f.render(root))
            lines_out.append('')

        if inc_return:
            lines_out += [
                '── INCONSISTENT RETURN ───────────────────────────────────────',
                'Functions that return a value on some paths but fall off the end on others.',
                'Suppress with:  # noqa: nonconform',
                '',
            ]
            for f in sorted(inc_return, key=lambda x: (str(x.path), x.line)):
                lines_out.append(f.render(root))
            lines_out.append('')

        if state_exp:
            lines_out += [
                '── STATE EXPOSURE ────────────────────────────────────────────',
                'Public self.x attributes never accessed from outside the class — should be _x.',
                'Suppress with:  # noqa: nonconform',
                '',
            ]
            for f in sorted(state_exp, key=lambda x: (str(x.path), x.line)):
                lines_out.append(f.render(root))
            lines_out.append('')

        if async_mis:
            lines_out += [
                '── ASYNC MISUSE ──────────────────────────────────────────────',
                'async def with no awaits, or asyncio.sleep(0) yield-control hacks.',
                'Suppress with:  # noqa: nonconform',
                '',
            ]
            for f in sorted(async_mis, key=lambda x: (str(x.path), x.line)):
                lines_out.append(f.render(root))
            lines_out.append('')

        if missing_symbols:
            lines_out += [
                '── MISSING REQUIRED SYMBOLS ──────────────────────────────────',
                'These canonical names were not found anywhere in the scanned files.',
                'A missing symbol may mean a rename silently dropped a core surface.',
                '',
            ]
            for name, description in missing_symbols:
                lines_out.append(f'  MISSING  {name}')
                lines_out.append(f'           {description}')
                lines_out.append('')

        if not total:
            lines_out.append('No nonconformances found.')

        return '\n'.join(lines_out) + '\n'

    def _run(self, argv=None):
        import argparse
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default='.')
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('--no-prefix-check', action='store_true')
        ap.add_argument('--no-symbol-check', action='store_true')
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)
        self._no_prefix_check = ns.no_prefix_check
        self._no_symbol_check = ns.no_symbol_check

        from vendor.claude.detector_base import discover_project_root, iter_py
        discovered = discover_project_root()
        root = Path(ns.root if ns.root != '.' else str(discovered)).resolve()
        if ns.paths:
            seeds = [Path(x).resolve() for x in ns.paths]
        else:
            default_seeds = [root / 'start.py', root / 'classes', root / 'data.py', root / 'trio.py']
            seeds = [p for p in default_seeds if p.exists()] or [root]
        files = iter_py(seeds, root)
        findings = self.run_parallel(files, root)
        report = self.render_report(root, files, findings)
        out = Path(ns.output)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        tracedWriteText(out, report, encoding='utf-8')
        try:
            print(report)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
            print(report.encode(enc, errors='replace').decode(enc, errors='replace'))
        return self._exit_code(findings)


if __name__ == '__main__':
    NonconformDetector.main()
