#!/usr/bin/env python3
"""Detects common bad-code patterns that are pure-AST checkable."""
from __future__ import annotations
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
OK_MARKER = 'noqa: badcode'

# ── helpers ───────────────────────────────────────────────────────────────────

def _sample(lines: list[str], lineno: int) -> str:
    return lines[lineno - 1].strip()[:120] if 0 < lineno <= len(lines) else ''


def _has_ok(lines: list[str], lineno: int) -> bool:
    snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
    return OK_MARKER in snippet


def _is_const(node: ast.expr, value: object) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _const_value(node: ast.expr) -> object:
    return node.value if isinstance(node, ast.Constant) else _MISSING


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


_MISSING = object()


def _block_terminates(stmts: list[ast.stmt]) -> bool:
    """Return True if the last reachable statement is return/raise/break/continue."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(last, ast.If):
        return bool(last.orelse) and _block_terminates(last.body) and _block_terminates(last.orelse)  # recursion-ok
    return False


def _collect_reads(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _collect_rhs_reads(stmt: ast.stmt) -> set[str]:
    """Reads that happen before the LHS write in an assignment statement."""
    if isinstance(stmt, ast.Assign):
        reads = _collect_reads(stmt.value)
        for target in stmt.targets:
            if not isinstance(target, ast.Name):
                reads |= _collect_reads(target)
        return reads
    if isinstance(stmt, ast.AnnAssign):
        reads: set[str] = set()
        if stmt.value:
            reads = _collect_reads(stmt.value)
        if not isinstance(stmt.target, ast.Name):
            reads |= _collect_reads(stmt.target)
        return reads
    if isinstance(stmt, ast.AugAssign):
        reads = _collect_reads(stmt.value)
        reads |= _collect_reads(stmt.target)
        return reads
    return {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _assigned_names(stmt: ast.stmt) -> list[tuple[str, int]]:
    """Simple name targets written by this statement."""
    lineno = getattr(stmt, 'lineno', 0) or 0
    if isinstance(stmt, ast.Assign):
        return [(t.id, lineno) for t in stmt.targets if isinstance(t, ast.Name)]
    if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        if isinstance(stmt.target, ast.Name) and (not isinstance(stmt, ast.AnnAssign) or stmt.value):
            return [(stmt.target.id, lineno)]
    return []


# ── check: dead writes ────────────────────────────────────────────────────────

def _check_dead_writes(stmts: list[ast.stmt], lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    last_write: dict[str, int] = {}  # name -> lineno

    for stmt in stmts:
        reads = _collect_rhs_reads(stmt)
        for name in reads:
            last_write.pop(name, None)

        for name, lineno in _assigned_names(stmt):
            if name == '_' or name.startswith('_'):
                pass
            elif name in last_write and not _has_ok(lines, lineno):
                findings.append((last_write[name], 'DEAD_WRITE',
                    f"'{name}' written at line {last_write[name]} then overwritten at line {lineno} before any read: {_sample(lines, lineno)}"))
            if name != '_':
                last_write[name] = lineno

        # entering a branch — values may or may not be read inside; be conservative
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                              ast.AsyncFor, ast.AsyncWith)):
            last_write.clear()
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                sub = getattr(child, 'body', None)
                if isinstance(sub, list):
                    findings.extend(_check_dead_writes(sub, lines))  # recursion-ok
                sub2 = getattr(child, 'orelse', None)
                if isinstance(sub2, list):
                    findings.extend(_check_dead_writes(sub2, lines))  # recursion-ok
                if isinstance(child, ast.ExceptHandler):
                    findings.extend(_check_dead_writes(child.body, lines))  # recursion-ok

    return findings


# ── check: self-assignment ────────────────────────────────────────────────────

def _check_self_assign(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        val = node.value
        for target in node.targets:
            if isinstance(target, ast.Name) and isinstance(val, ast.Name) and target.id == val.id:
                findings.append((lineno, 'SELF_ASSIGN', f"'{target.id} = {target.id}' assigns a variable to itself: {_sample(lines, lineno)}"))
    return findings


# ── check: augmented no-ops ───────────────────────────────────────────────────

_AUG_NOOPS = {
    ast.Add: 0, ast.Sub: 0, ast.BitOr: 0, ast.BitXor: 0,
    ast.Mult: 1, ast.Div: 1, ast.FloorDiv: 1, ast.Pow: 1, ast.Mod: None,
}

def _check_aug_noop(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AugAssign):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        identity = _AUG_NOOPS.get(type(node.op))
        if identity is not None and _is_const(node.value, identity):
            op = type(node.op).__name__.replace('Add', '+=').replace('Sub', '-=').replace('Mult', '*=').replace('Div', '/=').replace('FloorDiv', '//=').replace('Pow', '**=').replace('BitOr', '|=').replace('BitXor', '^=')
            findings.append((lineno, 'AUG_NOOP', f"Augmented assignment with identity value is a no-op: {_sample(lines, lineno)}"))
    return findings


# ── check: double negation ────────────────────────────────────────────────────

def _check_double_negation(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
                and isinstance(node.operand, ast.UnaryOp) and isinstance(node.operand.op, ast.Not)):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        findings.append((lineno, 'DOUBLE_NEGATION', f"'not not x' — use bool(x) or just x: {_sample(lines, lineno)}"))
    return findings


# ── check: tautological comparison (x == x, x is x) ─────────────────────────

def _node_eq(a: ast.expr, b: ast.expr) -> bool:
    return ast.dump(a) == ast.dump(b)


def _check_tautology(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        left = node.left
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.Is, ast.LtE, ast.GtE)) and _node_eq(left, comparator):
                findings.append((lineno, 'TAUTOLOGY', f"Comparison is always True — both sides are identical: {_sample(lines, lineno)}"))
            left = comparator
    return findings


# ── check: len(x) == 0 / len(x) > 0 ─────────────────────────────────────────

def _check_len_compare(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        left, op, right = node.left, node.ops[0], node.comparators[0]
        is_len_call = (isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == 'len')
        if not is_len_call:
            continue
        bad = False
        if isinstance(op, (ast.Eq, ast.LtE)) and _is_const(right, 0):
            bad = True
        elif isinstance(op, (ast.NotEq, ast.Gt)) and _is_const(right, 0):
            bad = True
        elif isinstance(op, ast.GtE) and _is_const(right, 1):
            bad = True
        elif isinstance(op, ast.GtE) and _is_const(right, 0):
            bad = True
        if bad:
            findings.append((lineno, 'LEN_COMPARE', f"Use 'not x' or 'x' instead of comparing len(): {_sample(lines, lineno)}"))
    return findings


# ── check: redundant else after terminating if ────────────────────────────────

def _check_redundant_else(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.If) or not stmt.orelse:
                continue
            if not _block_terminates(stmt.body):
                continue
            lineno = getattr(stmt.orelse[0], 'lineno', 0) or 0
            if _has_ok(lines, lineno):
                continue
            # skip elif chains — they read naturally; only flag plain else
            if isinstance(stmt.orelse[0], ast.If):
                continue
            findings.append((lineno, 'REDUNDANT_ELSE',
                f"else is redundant — the if-body always returns/raises: {_sample(lines, lineno)}"))
    return findings


# ── check: if x: return True else: return False ───────────────────────────────

def _is_bool_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def _return_bool_value(stmts: list[ast.stmt]) -> bool | None:
    if len(stmts) == 1 and isinstance(stmts[0], ast.Return) and stmts[0].value is not None:
        v = stmts[0].value
        if _is_bool_const(v):
            return bool(v.value)
    return None


def _check_bool_return(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        t = _return_bool_value(node.body)
        f = _return_bool_value(node.orelse)
        if t is None or f is None or t == f:
            continue
        if t is True and f is False:
            findings.append((lineno, 'BOOL_RETURN', f"'if x: return True / else: return False' — just return bool(x): {_sample(lines, lineno)}"))
        elif t is False and f is True:
            findings.append((lineno, 'BOOL_RETURN', f"'if x: return False / else: return True' — just return not x: {_sample(lines, lineno)}"))
    return findings


# ── check: dead code after return/raise/break/continue ───────────────────────

def _check_dead_code(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)

    def scan(stmts: list[ast.stmt]) -> None:
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, terminators):
                for dead in stmts[i + 1:]:
                    lineno = getattr(dead, 'lineno', 0) or 0
                    if _has_ok(lines, lineno):
                        continue
                    findings.append((lineno, 'DEAD_CODE',
                        f"Unreachable code after {type(stmt).__name__.lower()}: {_sample(lines, lineno)}"))
                return
            for child in ast.iter_child_nodes(stmt):
                sub = getattr(child, 'body', None)
                if isinstance(sub, list):
                    scan(sub)  # recursion-ok
                sub2 = getattr(child, 'orelse', None)
                if isinstance(sub2, list):
                    scan(sub2)  # recursion-ok
                if isinstance(child, ast.ExceptHandler):
                    scan(child.body)  # recursion-ok

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(node.body)
    return findings


# ── check: f-string with no interpolation ────────────────────────────────────

def _check_fstring_no_interp(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        if any(isinstance(v, ast.FormattedValue) for v in node.values):
            continue
        # Sanity-check: if the source line contains { it likely has interpolation
        # (can happen when AST lineno points to a containing expression)
        raw = lines[lineno - 1] if 0 < lineno <= len(lines) else ''
        if '{' in raw:
            continue
        findings.append((lineno, 'FSTRING_NO_INTERP',
            f"f-string has no interpolations — drop the f prefix: {_sample(lines, lineno)}"))
    return findings


# ── check: for k in d.keys() ─────────────────────────────────────────────────

def _check_iter_dict_keys(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        it = node.iter
        if not (isinstance(it, ast.Call) and not it.args[1:] and not it.keywords
                and isinstance(it.func, ast.Attribute) and it.func.attr == 'keys'
                and it.args == []):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        findings.append((lineno, 'ITER_DICT_KEYS',
            f"'for k in d.keys()' — just 'for k in d': {_sample(lines, lineno)}"))
    return findings


# ── check: sorted()/map()/filter() result thrown away ────────────────────────

_LAZY_OR_PURE = {'sorted', 'map', 'filter', 'reversed', 'enumerate', 'zip'}

def _check_unused_call_result(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
                continue
            call = stmt.value
            lineno = getattr(stmt, 'lineno', 0) or 0
            if _has_ok(lines, lineno):
                continue
            fname = ''
            if isinstance(call.func, ast.Name):
                fname = call.func.id
            elif isinstance(call.func, ast.Attribute):
                fname = call.func.attr
            if fname in _LAZY_OR_PURE:
                findings.append((lineno, 'UNUSED_RESULT',
                    f"Result of {fname}() is discarded — did you forget to assign or consume it? {_sample(lines, lineno)}"))
    return findings


# ── check: list([...]) / dict({}) wrapping a literal ─────────────────────────

def _check_redundant_wrap(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        if not (isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords):
            continue
        fname, arg = node.func.id, node.args[0]
        if fname == 'list' and isinstance(arg, ast.List):
            findings.append((lineno, 'REDUNDANT_WRAP', f"list([...]) wraps a list literal — use [...] directly: {_sample(lines, lineno)}"))
        elif fname == 'dict' and isinstance(arg, ast.Dict):
            findings.append((lineno, 'REDUNDANT_WRAP', f"dict({{...}}) wraps a dict literal — use {{...}} directly: {_sample(lines, lineno)}"))
        elif fname == 'tuple' and isinstance(arg, ast.Tuple):
            findings.append((lineno, 'REDUNDANT_WRAP', f"tuple((...)) wraps a tuple literal — use (...) directly: {_sample(lines, lineno)}"))
    return findings


# ── check: "".join([single_item]) ────────────────────────────────────────────

def _check_join_single(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == 'join'
                and len(node.args) == 1 and not node.keywords):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.List) and len(arg.elts) == 1:
            findings.append((lineno, 'JOIN_SINGLE',
                f"join() on a single-element list — just use the element directly: {_sample(lines, lineno)}"))
    return findings


# ── check: if True / if False / if None / if 0 / if 1 ───────────────────────

def _check_literal_condition(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.While)):
            continue
        lineno = getattr(node, 'lineno', 0) or 0
        if _has_ok(lines, lineno):
            continue
        test = node.test
        if isinstance(test, ast.Constant) and test.value in (True, False, None, 0, 1):
            findings.append((lineno, 'LITERAL_CONDITION',
                f"Condition is a literal constant '{test.value!r}' — branch is always taken or never taken: {_sample(lines, lineno)}"))
    return findings


# ── check: shadowed loop variable ─────────────────────────────────────────────

def _check_loop_var_shadow(tree: ast.AST, lines: list[str]) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        loop_var = node.target.id
        if loop_var == '_':
            continue
        for stmt in node.body:
            for child in ast.walk(stmt):
                if (isinstance(child, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == loop_var for t in child.targets)):
                    lineno = getattr(child, 'lineno', 0) or 0
                    if _has_ok(lines, lineno):
                        continue
                    findings.append((lineno, 'LOOP_VAR_SHADOW',
                        f"Loop variable '{loop_var}' overwritten inside the loop body: {_sample(lines, lineno)}"))
    return findings


# ── scan a single file ────────────────────────────────────────────────────────

def scan_file(path: Path) -> list[tuple[int, str, str]]:
    try:
        text = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception:
        InsertDebuggerException("badcodedetector.py:479", "handled exception")
        return []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        InsertDebuggerException("badcodedetector.py:484", "handled exception")
        return []

    findings: list[tuple[int, str, str]] = []

    # dead writes — per function body
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_check_dead_writes(node.body, lines))

    findings.extend(_check_self_assign(tree, lines))  # noqa: redundant
    findings.extend(_check_aug_noop(tree, lines))  # noqa: redundant
    findings.extend(_check_double_negation(tree, lines))  # noqa: redundant
    findings.extend(_check_tautology(tree, lines))  # noqa: redundant
    findings.extend(_check_len_compare(tree, lines))  # noqa: redundant
    findings.extend(_check_redundant_else(tree, lines))  # noqa: redundant
    findings.extend(_check_bool_return(tree, lines))  # noqa: redundant
    findings.extend(_check_dead_code(tree, lines))  # noqa: redundant
    findings.extend(_check_fstring_no_interp(tree, lines))  # noqa: redundant
    findings.extend(_check_iter_dict_keys(tree, lines))  # noqa: redundant
    findings.extend(_check_unused_call_result(tree, lines))  # noqa: redundant
    findings.extend(_check_redundant_wrap(tree, lines))  # noqa: redundant
    findings.extend(_check_join_single(tree, lines))  # noqa: redundant
    findings.extend(_check_literal_condition(tree, lines))  # noqa: redundant
    findings.extend(_check_loop_var_shadow(tree, lines))  # noqa: redundant

    findings.sort(key=lambda x: x[0])
    return findings


# ── file iteration ────────────────────────────────────────────────────────────

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
            InsertDebuggerException("badcodedetector.py:526", "handled exception")
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


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description='Detect common bad-code patterns via AST.')
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', required=True)
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root / 'start.py']
    files = iter_py(raw_paths, root)

    all_findings: list[tuple[Path, int, str, str]] = []
    for f in files:
        for lineno, code, msg in scan_file(f):
            all_findings.append((f, lineno, code, msg))

    all_findings.sort(key=lambda x: (str(x[0]), x[1]))

    out = Path(ns.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    by_code: dict[str, int] = {}
    for _, _, code, _ in all_findings:
        by_code[code] = by_code.get(code, 0) + 1

    report_lines = [
        'BAD CODE DETECTOR REPORT',
        '========================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Findings: {len(all_findings)}',
    ]
    if by_code:
        report_lines.append('')
        report_lines.append('By type:')
        for code, count in sorted(by_code.items(), key=lambda x: -x[1]):
            report_lines.append(f'  {code}: {count}')
    report_lines.append('')

    for f, lineno, code, msg in all_findings:
        rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
        report_lines.append(f'{rel}:{lineno}: [{code}] {msg}')

    if not all_findings:
        report_lines.append('No bad-code patterns found.')

    text = '\n'.join(report_lines) + '\n'
    tracedWriteText(out, text, encoding='utf-8')
    try:
        print(text)
    except UnicodeEncodeError:
        InsertDebuggerException("badcodedetector.py:597", "handled exception")
        enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))
    return 1 if all_findings else 0


if __name__ == '__main__':
    os._exit(int(main()))
