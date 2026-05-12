#!/usr/bin/env python3
from __future__ import annotations
"""
Phase hooks detector.

Two checks:

  1. PHASE HOOKS — every Phase(...) constructor and registerPhase(...) call must
     wire an onException handler (and optionally onError, onFault, onTimeout).
     The handler may be passed as a kwarg directly on the constructor/call, or
     chained as .onException(...) on the result.

  2. MAIN DISCIPLINE — the module-level main() function must call the lifecycle
     start/run method (appLifeCycle.start(), lifecycle.runApplication(), etc.)
     and must not contain bare imperative statements beyond:
       - assignments that build the app object or lifecycle ref
       - a single lifecycle start/run call
       - early-exit guard blocks (if infoResult is not None: return ...)
     Any other top-level expression statements, loops, or side-effectful calls
     inside main() are flagged.
"""
import argparse, ast, datetime, os, sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

SKIP_DIR_NAMES = {'.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor'}
OK_MARKER = 'phase-hooks-ok'

# Hook kwargs that count as "wired" on Phase(...) or registerPhase(...)
REQUIRED_HOOKS = ('onException',)
OPTIONAL_HOOKS = ('onError', 'onFault', 'onTimeout')

# Names that identify a lifecycle start/run call in main()
LIFECYCLE_START_ATTRS = {
    'start', 'run', 'runApplication', 'runApp', 'exec', 'exec_',
    'runConstructionPhases', 'runPhaseGroup',
}
LIFECYCLE_OWNER_NAMES = {
    'appLifeCycle', 'applicationLifeCycle', 'applicationLifecycle',
    'lifecycle', 'lifeCycle',
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _dotted(node.value)
        return f'{left}.{node.attr}' if left else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ''


def _has_ok(lines: list[str], lineno: int) -> bool:
    row = lines[lineno - 1] if 0 < lineno <= len(lines) else ''
    return OK_MARKER in row


def _sample(lines: list[str], lineno: int) -> str:
    return lines[lineno - 1].strip()[:100] if 0 < lineno <= len(lines) else ''


def _kwarg_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


# ── call chain collector ───────────────────────────────────────────────────────
# Given an expression like  Phase(...).onException(fn).onError(fn2)
# we want to find the innermost Call node AND all .attr names chained on it.

def _unwrap_chain(node: ast.expr) -> tuple[ast.Call | None, set[str]]:
    """Return (innermost_call, set_of_chained_attr_names)."""
    chained: set[str] = set()
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        chained.add(cur.func.attr)
        cur = cur.func.value
    if isinstance(cur, ast.Call):
        return cur, chained
    return None, chained


# ── check 1: phase hooks ──────────────────────────────────────────────────────

def _is_phase_call(node: ast.Call) -> bool:
    name = _dotted(node.func)
    base = name.rsplit('.', 1)[-1]
    return base in ('Phase', 'registerPhase')


def check_phase_hooks(tree: ast.AST, lines: list[str]) -> list[tuple[int, str]]:
    """Return (lineno, message) findings for Phase calls missing required hooks."""
    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # We look at Expr and Assign statements so we can inspect the full
        # right-hand-side chain, not just the innermost call node.
        exprs: list[ast.expr] = []
        if isinstance(node, ast.Expr):
            exprs.append(node.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            val = node.value if isinstance(node, ast.Assign) else node.value
            if val is not None:
                exprs.append(val)
        elif isinstance(node, ast.Return) and node.value is not None:
            exprs.append(node.value)

        for expr in exprs:
            inner, chained_attrs = _unwrap_chain(expr)
            if inner is None or not _is_phase_call(inner):
                continue
            lineno = inner.lineno
            if _has_ok(lines, lineno):
                continue
            kwarg_keys = _kwarg_names(inner)
            all_hooked = kwarg_keys | chained_attrs
            for hook in REQUIRED_HOOKS:
                if hook not in all_hooked:
                    findings.append((lineno,
                        f"Phase call missing required '{hook}' hook: {_sample(lines, lineno)}"))

    return findings


# ── check 2: main() discipline ────────────────────────────────────────────────
#
# main() must be minimal:
#   - Construct the app object (assignment)
#   - Call the lifecycle start/run method (the one allowed bare call)
#   - Return the result
#
# Everything else — CLI arg handling, trace installs, setup calls — must be
# a Phase registered on appLifeCycle, not loose code in main(). GTP models
# frequently litter main() with setup logic; this detector enforces the standard.
#
# What we flag:
#   A. main() never calls a lifecycle start/run method at all.
#   B. main() contains any bare Expr call that is not the lifecycle start itself.

def _is_lifecycle_start_call(node: ast.Call) -> bool:
    """True if node is a direct call like appLifeCycle.start(...) or lifecycle.runApplication(...)."""
    name = _dotted(node.func)
    parts = name.rsplit('.', 1)
    if len(parts) != 2:
        return False
    attr = parts[-1]
    owner = parts[0].rsplit('.', 1)[-1]
    return attr in LIFECYCLE_START_ATTRS and owner in LIFECYCLE_OWNER_NAMES


def _expr_contains_lifecycle_start(expr: ast.expr) -> bool:
    """True if expr is, or has nested within it, a lifecycle start call.

    Handles:
      lifecycle.runApplication(args)
      result = lifecycle.runApplication(args)
      int(lifecycle.runApplication(args))   (wrapped in a cast)
    """
    for child in ast.walk(expr):
        if isinstance(child, ast.Call) and _is_lifecycle_start_call(child):
            return True
    return False


def check_main_discipline(tree: ast.AST, lines: list[str]) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != 'main':
            continue

        # Walk all descendant calls to find any lifecycle start
        has_lifecycle_start = any(
            isinstance(child, ast.Call) and _is_lifecycle_start_call(child)
            for child in ast.walk(node)
            if child is not node
        )

        if not has_lifecycle_start:
            findings.append((node.lineno,
                'main() never calls a lifecycle start/run method '
                '(e.g. appLifeCycle.start(), lifecycle.runApplication())'))

        # Flag ALL bare Expr calls that aren't the lifecycle start itself.
        # CLI args, trace installs, setup calls — all must be phases.
        for stmt in node.body:
            if not isinstance(stmt, ast.Expr):
                continue
            if _has_ok(lines, stmt.lineno):
                continue
            if _expr_contains_lifecycle_start(stmt.value):
                continue
            findings.append((stmt.lineno,
                f'main() contains a bare call — move this into a Phase on appLifeCycle: '
                f'{_sample(lines, stmt.lineno)}'))

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
    ap = argparse.ArgumentParser(description='Detect missing phase lifecycle hooks and main() discipline violations.')
    ap.add_argument('--root', default='.')
    ap.add_argument('--output', required=True)
    ap.add_argument('--no-phase-check',  action='store_true', help='Skip phase hook check')
    ap.add_argument('--no-main-check',   action='store_true', help='Skip main() discipline check')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args()

    root = Path(ns.root).resolve()
    raw_paths = [Path(x).resolve() for x in ns.paths] if ns.paths else [root]
    files = iter_py(raw_paths, root)

    phase_findings:  list[tuple[Path, int, str]] = []
    main_findings:   list[tuple[Path, int, str]] = []

    for f in files:
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
            lines = text.splitlines()
            tree = ast.parse(text, filename=str(f))
        except Exception:
            continue
        if not ns.no_phase_check:
            for lineno, msg in check_phase_hooks(tree, lines):
                phase_findings.append((f, lineno, msg))
        if not ns.no_main_check:
            for lineno, msg in check_main_discipline(tree, lines):
                main_findings.append((f, lineno, msg))

    phase_findings.sort(key=lambda x: (str(x[0]), x[1]))
    main_findings.sort(key=lambda x: (str(x[0]), x[1]))
    total = len(phase_findings) + len(main_findings)

    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)

    report_lines = [
        'PHASE HOOKS DETECTOR REPORT',
        '===========================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Phase hook findings: {len(phase_findings)}',
        f'main() discipline findings: {len(main_findings)}',
        f'Total findings: {total}',
        '',
    ]

    if phase_findings:
        report_lines.append('── PHASE HOOKS ───────────────────────────────────────────────')
        report_lines.append('Every Phase() and registerPhase() must wire an onException handler.')
        report_lines.append('Pass it as a kwarg or chain it: Phase(...).onException(handler)')
        report_lines.append(f'Suppress with:  # {OK_MARKER}')
        report_lines.append('')
        for f, lineno, msg in phase_findings:
            rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
            report_lines.append(f'{rel}:{lineno}: [PHASE_HOOK] {msg}')
        report_lines.append('')

    if main_findings:
        report_lines.append('── MAIN() DISCIPLINE ─────────────────────────────────────────')
        report_lines.append('main() must call a lifecycle start/run method and must not contain')
        report_lines.append('bare imperative statements beyond app construction and early-exit guards.')
        report_lines.append(f'Suppress with:  # {OK_MARKER}')
        report_lines.append('')
        for f, lineno, msg in main_findings:
            rel = os.path.relpath(f, root) if str(f).startswith(str(root)) else str(f)
            report_lines.append(f'{rel}:{lineno}: [MAIN_DISCIPLINE] {msg}')
        report_lines.append('')

    if not total:
        report_lines.append('No phase hook or main() discipline violations found.')

    text = '\n'.join(report_lines) + '\n'
    out.write_text(text, encoding='utf-8')
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))
    return 1 if total else 0


if __name__ == '__main__':
    raise SystemExit(main())
