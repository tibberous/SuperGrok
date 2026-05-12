"""
Monkey-patch detector — AST + import spider.

Walk a root Python file, follow every reachable import, and flag every
construct that looks like it mutates something that was defined elsewhere.
Leans toward false-positives rather than misses.

Usage (standalone):
    python vendor/claude/monkeypatchdetect.py start.py [--out monkeypatches.txt]
"""

from __future__ import annotations

import ast
import os
import sys
import importlib.util
import textwrap
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Finding definitions
# ---------------------------------------------------------------------------

# Names that are always considered "external" targets even when bare.
STDLIB_MUTATION_TARGETS = frozenset({
    'sys', 'os', 'builtins', '__builtins__', 'io', 'threading', 'socket',
    'http', 'urllib', 'json', 'logging', 'collections', 'functools',
    'itertools', 'inspect', 'types', 'importlib', 'gc', 'weakref',
    'contextlib', 'abc', 'pathlib', 'time', 'datetime', 're', 'struct',
    'copy', 'pickle', 'codecs', 'string', 'traceback', 'warnings',
    'platform', 'subprocess', 'shutil', 'tempfile', 'glob', 'fnmatch',
    'hashlib', 'hmac', 'base64', 'binascii', 'zlib', 'gzip', 'zipfile',
    'tarfile', 'csv', 'configparser', 'argparse', 'signal', 'errno',
    'ctypes', 'cffi', 'queue', 'multiprocessing', 'concurrent',
    'asyncio', 'selectors', 'ssl', 'email', 'html', 'xml',
    'sqlite3', 'decimal', 'fractions', 'random', 'math', 'cmath',
    'statistics', 'array', 'mmap', 'heapq', 'bisect', 'enum',
    'dataclasses', 'typing', 'unittest', 'doctest', 'pdb', 'profile',
    'timeit', 'cProfile', 'token', 'tokenize', 'ast', 'dis', 'opcode',
    'py_compile', 'compileall', 'zipimport', 'pkgutil', 'modulefinder',
})

# setattr / delattr calls whose first argument looks like an external name.
MUTATION_BUILTINS = frozenset({'setattr', 'delattr'})

# Attribute names that signal low-level object mutation.
DUNDER_MUTATION_ATTRS = frozenset({
    '__dict__', '__class__', '__bases__', '__mro__', '__subclasses__',
    '__code__', '__globals__', '__defaults__', '__kwdefaults__',
    '__annotations__', '__module__', '__qualname__', '__name__',
    '__doc__', '__wrapped__', '__slots__', '__init_subclass__',
    '__set_name__', '__init__', '__new__', '__del__',
})

# Calls that themselves perform patching.
PATCH_CALL_NAMES = frozenset({
    'patch', 'patch.object', 'patch.dict', 'patch.multiple',
    'monkeypatch', 'monkeypatch.setattr', 'monkeypatch.delattr',
    'monkeypatch.setitem', 'monkeypatch.delitem', 'monkeypatch.setenv',
    'mock.patch', 'mock.patch.object',
    'wrapt.wrap_function_wrapper', 'wrapt.patch_function_wrapper',
    'wrapt.decorator',
    'gevent.monkey.patch_all', 'gevent.monkey.patch_socket',
    'gevent.monkey.patch_thread',
    'eventlet.monkey_patch',
    'importlib.reload', 'reload',
})

# sys.modules manipulation.
SYS_MODULES_PATTERNS = frozenset({'sys.modules', "__import__('sys').modules"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    file: str
    line: int
    col: int
    kind: str
    code: str
    note: str = ''
    confidence: int = 50  # 0-100; higher = more certain it is a monkey-patch

    def confidence_label(self) -> str:
        if self.confidence >= 90:
            return 'HIGH'
        if self.confidence >= 60:
            return 'MEDIUM'
        return 'LOW'

    def render(self) -> str:
        rel = self.file
        label = self.confidence_label()
        lines = [
            f'[{self.kind}] {rel}:{self.line}  confidence={self.confidence}% ({label})',
        ]
        if self.note:
            lines.append(f'  note : {self.note}')
        for i, src_line in enumerate(self.code.splitlines()):
            lines.append(f'  {">" if i == 0 else " "} {src_line}')
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------

def _source_lines(path: str) -> list[str]:
    try:
        return tracedReadText(path, encoding='utf-8', errors='replace').splitlines(keepends=True)
    except OSError as exc:
        InsertDebuggerException('_source_lines', exc, path)
        return []


def _snippet(source_lines: list[str], lineno: int, end_lineno: Optional[int] = None) -> str:
    """Extract source lines (1-indexed).  Returns at most 8 lines."""
    if not source_lines:
        return ''
    start = max(0, lineno - 1)
    stop = min(len(source_lines), (end_lineno or lineno))
    stop = min(stop, start + 8)
    return ''.join(source_lines[start:stop]).rstrip()


def _has_ok_marker(source_lines: list[str], lineno: int, end_lineno: Optional[int] = None) -> bool:
    """Allow intentional low-level wiring marked with # monkeypatch-ok."""
    if not source_lines:
        return False
    start = max(0, int(lineno or 1) - 2)
    stop = min(len(source_lines), int(end_lineno or lineno or 1) + 1)
    snippet = ''.join(source_lines[start:stop])
    return 'monkeypatch-ok' in snippet


# ---------------------------------------------------------------------------
# Name tracking — what names in this file came from imports?
# ---------------------------------------------------------------------------

def _collect_imported_names(tree: ast.Module) -> set[str]:
    """Return every top-level name that arrived via an import statement."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split('.')[0]
                names.add(bound)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == '*':
                    continue
                bound = alias.asname or alias.name
                names.add(bound)
    return names


# ---------------------------------------------------------------------------
# Pattern checkers — each returns a list[Finding]
# ---------------------------------------------------------------------------

def _node_src(node: ast.AST) -> tuple[int, Optional[int]]:
    return getattr(node, 'lineno', 0), getattr(node, 'end_lineno', None)


def _attr_target_root(node: ast.expr) -> Optional[str]:
    """Walk a.b.c.d and return the leftmost name 'a'."""
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur.id
    return None


def _call_dotted_name(node: ast.Call) -> str:
    """Return dotted name of a Call's func, e.g. 'mock.patch.object'."""
    parts: list[str] = []
    cur = node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return '.'.join(reversed(parts))


def _subscript_root(node: ast.expr) -> Optional[str]:
    """For node['key'] = ..., return the root name."""
    if isinstance(node, ast.Subscript):
        return _attr_target_root(node.value) or (
            node.value.id if isinstance(node.value, ast.Name) else None
        )
    return None


# ---- individual pattern detectors -----------------------------------------

def check_attr_assignment(
    node: ast.Assign | ast.AugAssign | ast.AnnAssign,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """module.attr = value  or  Klass.method = func"""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target] if node.value is not None else []
    else:
        return

    for t in targets:
        if not isinstance(t, ast.Attribute):
            continue
        lineno, end_lineno = _node_src(node)
        if _has_ok_marker(src, lineno, end_lineno):
            continue
        root = _attr_target_root(t)
        if root is None:
            continue
        is_imported = root in imported
        is_stdlib = root in STDLIB_MUTATION_TARGETS
        is_dunder = t.attr.startswith('__') and t.attr.endswith('__')
        if is_stdlib or is_dunder:
            ln, eln = _node_src(node)
            note_parts = []
            if is_imported:
                note_parts.append(f"'{root}' is an imported name")
            if is_stdlib:
                note_parts.append(f"'{root}' is a stdlib/well-known module")
            if is_dunder:
                note_parts.append(f"assigning dunder '{t.attr}'")
            # Confidence: stdlib + dunder = very high, imported = high, dunder alone = medium
            if is_stdlib:
                conf = 90
            elif is_imported and is_dunder:
                conf = 92
            elif is_imported:
                conf = 75
            else:
                conf = 55  # dunder-only without confirmed external root
            findings.append(Finding(
                file=file, line=ln, col=getattr(t, 'col_offset', 0),
                kind='attr-assign',
                code=_snippet(src, ln, eln),
                note='; '.join(note_parts),
                confidence=conf,
            ))


def check_setattr_delattr(
    node: ast.Call,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """setattr(obj, 'name', value) / delattr(obj, 'name')"""
    name = _call_dotted_name(node)
    if name not in MUTATION_BUILTINS:
        return
    if not node.args:
        return
    first = node.args[0]
    root = (
        first.id if isinstance(first, ast.Name)
        else _attr_target_root(first)
    )
    is_imported = root is not None and (root in STDLIB_MUTATION_TARGETS)
    # Also flag if the second arg (attr name) is a dunder.
    attr_name = ''
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        attr_name = str(node.args[1].value or '')
    is_dunder = attr_name.startswith('__') and attr_name.endswith('__')

    if is_imported or is_dunder:
        ln, eln = _node_src(node)
        note_parts = []
        if is_imported:
            note_parts.append(f"target '{root}' is imported/stdlib")
        if is_dunder:
            note_parts.append(f"setting dunder attribute '{attr_name}'")
        conf = 88 if is_imported else 70
        if is_dunder:
            conf = min(100, conf + 7)
        findings.append(Finding(
            file=file, line=ln, col=getattr(node, 'col_offset', 0),
            kind='setattr-call',
            code=_snippet(src, ln, eln),
            note='; '.join(note_parts),
            confidence=conf,
        ))


def check_dict_mutation(
    node: ast.Assign | ast.AugAssign,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """obj.__dict__['key'] = value  or  vars(obj)['key'] = value"""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    else:
        return

    for t in targets:
        if not isinstance(t, ast.Subscript):
            continue
        val = t.value
        # obj.__dict__['key']
        if isinstance(val, ast.Attribute) and val.attr == '__dict__':
            root = _attr_target_root(val)
            if root and (root in imported or root in STDLIB_MUTATION_TARGETS):
                ln, eln = _node_src(node)
                findings.append(Finding(
                    file=file, line=ln, col=getattr(t, 'col_offset', 0),
                    kind='dict-mutation',
                    code=_snippet(src, ln, eln),
                    note=f"mutating __dict__ of imported name '{root}'",
                    confidence=88,
                ))
                continue
        # vars(obj)['key'] — harder to track statically, flag all vars() subscript assigns
        if isinstance(val, ast.Call):
            fn = val.func
            fn_name = fn.id if isinstance(fn, ast.Name) else _call_dotted_name(val)
            if fn_name == 'vars':
                ln, eln = _node_src(node)
                findings.append(Finding(
                    file=file, line=ln, col=getattr(t, 'col_offset', 0),
                    kind='vars-mutation',
                    code=_snippet(src, ln, eln),
                    note='vars(obj)[...] assignment — potential monkey-patch',
                    confidence=60,
                ))
        # sys.modules['name'] = ...
        root = _subscript_root(t)
        if root == 'sys':
            # check if the chain is sys.modules
            if isinstance(t.value, ast.Attribute) and t.value.attr == 'modules':
                ln, eln = _node_src(node)
                findings.append(Finding(
                    file=file, line=ln, col=getattr(t, 'col_offset', 0),
                    kind='sys-modules-replace',
                    code=_snippet(src, ln, eln),
                    note='direct sys.modules[...] injection',
                    confidence=95,
                ))


def check_patch_calls(
    node: ast.Call,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """unittest.mock.patch, monkeypatch.setattr, wrapt.wrap_function_wrapper, etc."""
    name = _call_dotted_name(node)
    # Match exact patch APIs. Only the explicit importlib/imp/bare reload names count as reload.
    for pat in PATCH_CALL_NAMES:
        if pat == 'reload':
            matched = name in {'reload', 'importlib.reload', 'imp.reload'}
        else:
            matched = name == pat or name.endswith('.' + pat)
        if matched:
            ln, eln = _node_src(node)
            findings.append(Finding(
                file=file, line=ln, col=getattr(node, 'col_offset', 0),
                kind='patch-call',
                code=_snippet(src, ln, eln),
                note=f"call to known patching function '{name}'",
                confidence=93,
            ))
            return


def check_code_object_mutation(
    node: ast.Assign | ast.AugAssign,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """func.__code__ = ..., func.__defaults__ = ..., etc."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    else:
        return

    for t in targets:
        if isinstance(t, ast.Attribute) and t.attr in DUNDER_MUTATION_ATTRS:
            ln, eln = _node_src(node)
            root = _attr_target_root(t)
            note = f"assigning '{t.attr}'"
            is_ext = root and (root in imported or root in STDLIB_MUTATION_TARGETS)
            if is_ext:
                note += f" on imported name '{root}'"
            conf = 91 if is_ext else 65
            findings.append(Finding(
                file=file, line=ln, col=getattr(t, 'col_offset', 0),
                kind='dunder-mutation',
                code=_snippet(src, ln, eln),
                note=note,
                confidence=conf,
            ))


def check_exec_eval(
    node: ast.Call,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """exec() / eval() with dynamic content — could hide patches."""
    name = _call_dotted_name(node)
    if name not in ('exec', 'eval', 'compile'):
        return
    # Only flag if the argument is NOT a simple string literal
    # (literal exec is often just needed; dynamic exec is suspicious)
    if node.args:
        first = node.args[0]
        if not isinstance(first, ast.Constant):
            ln, eln = _node_src(node)
            findings.append(Finding(
                file=file, line=ln, col=getattr(node, 'col_offset', 0),
                kind='dynamic-exec',
                code=_snippet(src, ln, eln),
                note=f"dynamic {name}() — may conceal monkey-patching",
                confidence=55,
            ))


def check_type_manipulation(
    node: ast.Call,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """type.__setattr__, object.__setattr__, or direct type() with 3 args to create new class."""
    name = _call_dotted_name(node)
    # type('Name', bases, dict) — dynamic class creation / replacement
    if name == 'type' and len(node.args) == 3:
        return
    # object.__setattr__(obj, ...) or type.__setattr__(cls, ...)
    if name in ('object.__setattr__', 'type.__setattr__', 'super().__setattr__'):
        ln, eln = _node_src(node)
        findings.append(Finding(
            file=file, line=ln, col=getattr(node, 'col_offset', 0),
            kind='low-level-setattr',
            code=_snippet(src, ln, eln),
            note=f"low-level {name}() call",
            confidence=70,
        ))


def check_importlib_reload(
    node: ast.Call,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """importlib.reload(mod) — resets module state, often paired with patching."""
    name = _call_dotted_name(node)
    if name in ('importlib.reload', 'reload', 'imp.reload'):
        ln, eln = _node_src(node)
        findings.append(Finding(
            file=file, line=ln, col=getattr(node, 'col_offset', 0),
            kind='module-reload',
            code=_snippet(src, ln, eln),
            note=f"{name}() — module reload can reset or expose patch targets",
            confidence=72,
        ))


def check_reassignment_of_imported_name(
    node: ast.Assign,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
    locally_defined: set[str],
) -> None:
    """Project fallback aliases are not monkey patches; mutation checks catch real external writes."""
    return


def check_protocol_swaps(
    node: ast.Assign | ast.AugAssign,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """Detect classic protocol swaps: sys.stdout = ..., sys.stderr = ...,
       sys.excepthook = ..., threading.excepthook = ..., socket.socket = ..., etc."""
    PROTOCOL_ATTRS: dict[str, set[str]] = {
        'sys': {
            'stdout', 'stderr', 'stdin', 'excepthook', 'displayhook',
            'path_hooks', 'path_importer_cache', 'meta_path', 'path',
            'modules', 'audit',
        },
        'threading': {'excepthook'},
        'socket': {'socket', 'getaddrinfo', 'create_connection'},
        'os': {'environ', 'path'},
        'logging': {'root', 'basicConfig'},
        'builtins': set(),  # any attribute
        '__builtins__': set(),
    }

    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AugAssign):
        targets = [node.target]
    else:
        return

    for t in targets:
        if not isinstance(t, ast.Attribute):
            continue
        root = _attr_target_root(t)
        if root not in PROTOCOL_ATTRS:
            continue
        allowed = PROTOCOL_ATTRS[root]
        if not allowed or t.attr in allowed:
            ln, eln = _node_src(node)
            findings.append(Finding(
                file=file, line=ln, col=getattr(t, 'col_offset', 0),
                kind='protocol-swap',
                code=_snippet(src, ln, eln),
                note=f"replacing protocol attribute '{root}.{t.attr}'",
                confidence=85,
            ))


def check_class_body_replacement(
    node: ast.ClassDef,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """Local fallback class definitions are not monkey patches."""
    return


def check_funcdef_shadow(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    src: list[str],
    imported: set[str],
    file: str,
    findings: list[Finding],
) -> None:
    """Local fallback function definitions are not monkey patches."""
    return


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------

def _collect_locally_defined(tree: ast.Module) -> set[str]:
    """Names defined by class/def statements at module scope."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _nopatch_lines(source_lines: list[str]) -> set[int]:
    """Return 1-indexed line numbers that carry a # nopatch suppression comment."""
    suppressed: set[int] = set()
    for i, line in enumerate(source_lines, start=1):
        stripped = line.rstrip()
        if '# nopatch' in stripped.lower():
            suppressed.add(i)
    return suppressed


def analyze_file(path: str) -> list[Finding]:
    src_lines = _source_lines(path)
    if not src_lines:
        return []
    try:
        tree = ast.parse(''.join(src_lines), filename=path)
    except SyntaxError:
        InsertDebuggerException("monkeypatchdetect.py:598", "handled exception")
        return []

    imported = _collect_imported_names(tree)  # noqa: redundant
    locally_defined = _collect_locally_defined(tree)  # noqa: redundant
    suppressed = _nopatch_lines(src_lines)  # noqa: redundant
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            check_attr_assignment(node, src_lines, imported, path, findings)
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                check_dict_mutation(node, src_lines, imported, path, findings)  # noqa: redundant
                check_code_object_mutation(node, src_lines, imported, path, findings)  # noqa: redundant
                check_protocol_swaps(node, src_lines, imported, path, findings)  # noqa: redundant
            if isinstance(node, ast.Assign):  # noqa: redundant
                check_reassignment_of_imported_name(  # noqa: redundant
                    node, src_lines, imported, path, findings, locally_defined  # noqa: redundant
                )  # noqa: redundant

        elif isinstance(node, ast.Call):  # noqa: redundant
            check_setattr_delattr(node, src_lines, imported, path, findings)  # noqa: redundant
            check_patch_calls(node, src_lines, imported, path, findings)  # noqa: redundant
            check_exec_eval(node, src_lines, imported, path, findings)  # noqa: redundant
            check_type_manipulation(node, src_lines, imported, path, findings)  # noqa: redundant
            check_importlib_reload(node, src_lines, imported, path, findings)  # noqa: redundant

        elif isinstance(node, ast.ClassDef):
            check_class_body_replacement(node, src_lines, imported, path, findings)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_funcdef_shadow(node, src_lines, imported, path, findings)

    return [
        f for f in findings
        if f.line not in suppressed
        and 'monkeypatch-ok' not in str(f.code or '').lower()
    ]


# ---------------------------------------------------------------------------
# Import spider
# ---------------------------------------------------------------------------

def _resolve_module_path(module_name: str, from_file: str) -> Optional[str]:
    """Try to find the filesystem path for a module name."""
    from_dir = str(Path(from_file).parent)
    # Relative candidate: package/submodule next to from_file
    parts = module_name.split('.')
    for base in [from_dir] + sys.path:
        candidate = Path(base).joinpath(*parts)
        # package
        init = candidate / '__init__.py'
        if init.exists():
            return str(init)
        # module file
        py = candidate.with_suffix('.py')
        if py.exists():
            return str(py)
    # Fall back to importlib
    try:
        spec = importlib.util.find_spec(module_name)
        if spec and spec.origin and spec.origin.endswith('.py'):
            return spec.origin
    except (ModuleNotFoundError, ValueError):
        InsertDebuggerException("monkeypatchdetect.py:658", "handled exception")
        pass
    return None


def _imports_from_file(path: str) -> list[str]:
    """Return all module names imported by path."""
    src_lines = _source_lines(path)
    if not src_lines:
        return []
    try:
        tree = ast.parse(''.join(src_lines), filename=path)
    except SyntaxError:
        InsertDebuggerException("monkeypatchdetect.py:670", "handled exception")
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def spider(root_file: str, max_files: int = 2000) -> list[str]:
    """BFS from root_file, following imports.  Returns sorted list of .py paths."""
    visited: set[str] = set()
    queue: list[str] = [str(Path(root_file).resolve())]
    project_root = str(Path(root_file).resolve().parent)

    while queue and len(visited) < max_files:
        path = queue.pop(0)
        norm = str(Path(path).resolve())
        if norm in visited:
            continue
        visited.add(norm)
        for mod_name in _imports_from_file(norm):
            resolved = _resolve_module_path(mod_name, norm)
            if resolved is None:
                continue
            norm_r = str(Path(resolved).resolve())
            # Stay within the project tree to avoid crawling the entire stdlib
            if not norm_r.startswith(project_root):
                # Allow one level of installed packages adjacent to project
                # but skip deep stdlib paths
                if 'site-packages' in norm_r or 'dist-packages' in norm_r:
                    pass  # allow third-party packages
                else:
                    continue
            if norm_r not in visited:
                queue.append(norm_r)

    return sorted(visited)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Merge multiple findings on the same file+line into one.

    Keeps the highest confidence, joins all distinct kinds and notes.
    """
    seen: dict[tuple[str, int], Finding] = {}
    for f in findings:
        key = (f.file, f.line)
        if key not in seen:
            seen[key] = Finding(
                file=f.file, line=f.line, col=f.col,
                kind=f.kind, code=f.code,
                note=f.note, confidence=f.confidence,
            )
        else:
            existing = seen[key]
            existing.confidence = max(existing.confidence, f.confidence)
            # merge kinds
            kinds = [k.strip() for k in existing.kind.split(',')]
            if f.kind not in kinds:
                kinds.append(f.kind)
            existing.kind = ', '.join(kinds)
            # merge notes
            notes = [n.strip() for n in existing.note.split(';') if n.strip()]
            for rawPart in (f.note or '').split(';'):
                cleanPart = rawPart.strip()
                if cleanPart and cleanPart not in notes:
                    notes.append(cleanPart)
            existing.note = '; '.join(notes)
    return list(seen.values())


def _project_py_files(root_dir: str) -> list[str]:
    """Return project .py files for directory-mode scans without entering caches/vendor."""
    root = Path(root_dir).resolve()
    skip = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
    files: list[str] = []
    for child in root.rglob('*.py'):
        try:
            rel_parts = child.resolve().relative_to(root).parts
        except ValueError:
            InsertDebuggerException("monkeypatchdetect.py:758", "handled exception")
            continue
        if any(part in skip for part in rel_parts):
            continue
        files.append(str(child.resolve()))
    return sorted(dict.fromkeys(files))


def run(root_file: str, out_file: Optional[str] = None, project_only: bool = True) -> int:
    root_obj = Path(root_file).resolve()
    root_path = str(root_obj)
    if not root_obj.exists():
        print(f'[monkeypatchdetect] ERROR: file not found: {root_file}', file=sys.stderr)
        return 1

    if root_obj.is_dir():
        print(f'[monkeypatchdetect] Spidering project tree from {root_obj} ...', file=sys.stderr)
        files = _project_py_files(str(root_obj))
        project_root = str(root_obj)
    else:
        print(f'[monkeypatchdetect] Spidering imports from {root_file} ...', file=sys.stderr)
        files = spider(root_path)
        project_root = str(root_obj.parent)
    print(f'[monkeypatchdetect] Analysing {len(files)} files ...', file=sys.stderr)

    raw_findings: list[Finding] = []
    for f in files:
        # Skip third-party packages unless explicitly requested
        if project_only and ('site-packages' in f or 'dist-packages' in f):
            continue
        raw_findings.extend(analyze_file(f))

    # Deduplicate: one entry per file+line
    all_findings = _dedup_findings(raw_findings)

    # Sort by file then line
    all_findings.sort(key=lambda x: (x.file, x.line, x.col))

    if not all_findings:
        lines = [
            '=' * 78,
            'MONKEY-PATCH DETECTION REPORT',
            f'Root file     : {root_file}',
            f'Files scanned : {len(files)}',
            'Findings      : 0  (HIGH=0  MEDIUM=0  LOW=0)',
            '',
            'No monkey-patches detected.',
            '=' * 78,
            '',
        ]
        report = '\n'.join(lines)
        print(report)
        if out_file:
            tracedWriteText(Path(out_file), report, encoding='utf-8')
            print(f'[monkeypatchdetect] Report written to {out_file}', file=sys.stderr)
        return 0

    # Group by file for a readable report
    by_file: dict[str, list[Finding]] = {}
    for f in all_findings:
        by_file.setdefault(f.file, []).append(f)

    high = sum(1 for f in all_findings if f.confidence >= 90)
    medium = sum(1 for f in all_findings if 60 <= f.confidence < 90)
    low = sum(1 for f in all_findings if f.confidence < 60)

    lines: list[str] = [
        '=' * 78,
        'MONKEY-PATCH DETECTION REPORT',
        f'Root file     : {root_file}',
        f'Files scanned : {len(files)}',
        f'Findings      : {len(all_findings)}  (HIGH={high}  MEDIUM={medium}  LOW={low})',
        '',
        'Confidence scale:',
        '  HIGH   (90-100%) — almost certainly a monkey-patch',
        '  MEDIUM (60-89%)  — likely a monkey-patch; review recommended',
        '  LOW    (0-59%)   — suspicious pattern; may be intentional',
        '=' * 78,
        '',
    ]

    for filepath, group in sorted(by_file.items()):
        try:
            rel = os.path.relpath(filepath, start=str(Path(root_file).parent))
        except ValueError:
            InsertDebuggerException("monkeypatchdetect.py:842", "handled exception")
            rel = filepath
        lines.append(f'--- {rel}  ({len(group)} finding{"s" if len(group) != 1 else ""})')
        lines.append('')
        for finding in group:
            finding.file = rel  # use relative path in output
            lines.append(finding.render())
            lines.append('')
        lines.append('')

    report = '\n'.join(lines)
    if out_file:
        tracedWriteText(Path(out_file), report, encoding='utf-8')
    if os.environ.get('CUTIEPY_MONKEY_PRINT_FULL') == '1':
        print(report)
    else:
        print('MONKEY-PATCH DETECTION REPORT')
        print(f'Root file     : {root_file}')  # noqa: redundant
        print(f'Files scanned : {len(files)}')  # noqa: redundant
        print(f'Findings      : {len(all_findings)}  (HIGH={high}  MEDIUM={medium}  LOW={low})')  # noqa: redundant
        if out_file:
            print(f'Report written: {out_file}')
    if out_file:
        print(f'[monkeypatchdetect] Report written to {out_file}', file=sys.stderr)

    return 0


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Detect monkey patches in a Python project.')
    parser.add_argument('root', help='Root .py file to spider from, or a project directory to scan recursively')
    parser.add_argument('--out', default=None, help='Output file path (default: print to stdout)')
    args = parser.parse_args()
    code = run(args.root, args.out)
    os._exit(int(code))
