#!/usr/bin/env python3
"""
Phase hooks detector (AST-based).

Three checks:
  1. PHASE HOOKS — every Phase()/registerPhase() call must wire onFault, onException, onTimeout.
  2. PHASE CLASS ARCHITECTURE — Phase subclasses with process() must define all three hooks.
  3. MAIN DISCIPLINE — main() must call lifecycle start and not contain bare imperative calls.
"""
from __future__ import annotations
import ast
import datetime
import os
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding, sample_line, has_ok_marker
from vendor.claude.detector_runtime import InsertDebuggerException, tracedWriteText

OK_MARKER = 'phase-hooks-ok'
_OK_SET = frozenset({OK_MARKER})
REQUIRED_HOOKS = ('onFault', 'onException', 'onTimeout')
OPTIONAL_HOOKS = ('onError',)
KWARG_ALIASES: dict[str, str] = {
    'on_fault': 'onFault', 'onFault': 'onFault',
    'on_exception': 'onException', 'onException': 'onException',
    'on_timeout': 'onTimeout', 'onTimeout': 'onTimeout',
    'on_error': 'onError', 'onError': 'onError',
}
EXCEPTION_HANDLER_NAMES = {'exceptionHandler', 'exception_handler'}
DEFAULT_PHASE_HOOK_MARKERS = {'_default_phase_error_hook', '_defaultStartPhaseErrorHook', '_default_process_error_hook'}
PHASE_BASE_NAMES = {'Phase', 'LifeCyclePhase', 'StartPhase'}
LIFECYCLE_START_ATTRS = {'start', 'run', 'runApplication', 'runApp', 'exec', 'exec_', 'runConstructionPhases', 'runPhaseGroup'}
LIFECYCLE_OWNER_NAMES = {'appLifeCycle', 'applicationLifeCycle', 'applicationLifecycle', 'lifecycle', 'lifeCycle'}
ALLOWED_MAIN_BARE_CALLS = {'_installTrace', 'installTrace', 'installExceptionRecorderBuiltin'}

_SAMPLE_PATTERN_A = '''\
    # Pattern A -- kwargs on registerPhase (functional style)
    appLifeCycle.registerPhase(
        'my-phase', self._myPhaseCallback,
        on_fault=self._onFault, on_exception=self._onException,
        on_timeout=self._onTimeout, on_error=self._onError,
    )
    def _onFault(self, context=None, phase=None, lifecycle=None):
        recordException('MyClass._onFault', context)
    def _onException(self, error=None, context=None, phase=None, lifecycle=None):
        recordException('MyClass._onException', error)
    def _onTimeout(self, context=None, phase=None, lifecycle=None):
        recordException('MyClass._onTimeout', context)
'''

_SAMPLE_PATTERN_B = '''\
    # Pattern B -- exceptionHandler class attribute (preferred for subclasses)
    class MyPhase(Phase):
        exceptionHandler = Exception
        def process(self, context=None, phase=None, lifecycle=None): pass
        def onFault(self, context=None, phase=None, lifecycle=None):
            recordException('MyPhase.onFault', context)
        def onException(self, error=None, context=None, phase=None, lifecycle=None):
            recordException('MyPhase.onException', error)
        def onTimeout(self, context=None, phase=None, lifecycle=None):
            recordException('MyPhase.onTimeout', context)
        def onError(self, error=None, context=None, phase=None, lifecycle=None):
            recordException('MyPhase.onError', error)
'''

_SAMPLE_PHASE_CLASS_FIX = _SAMPLE_PATTERN_B

_SAMPLE_MAIN_FIX = '''\
    def main(argv=None):
        app = PyAudioEncoder(argv)
        return appLifeCycle.runApplication()
'''


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _dotted(node.value)
        return f'{left}.{node.attr}' if left else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ''


def _has_ok(lines: list[str], lineno: int) -> bool: return has_ok_marker(lines, lineno, _OK_SET)
def _sample(lines: list[str], lineno: int) -> str: return sample_line(lines, lineno, 100)


def _kwarg_names_normalized(call: ast.Call) -> set[str]:
    result: set[str] = set()
    for kw in call.keywords:
        if kw.arg and kw.arg in KWARG_ALIASES:
            result.add(KWARG_ALIASES[kw.arg])
        elif kw.arg:
            result.add(kw.arg)
    return result


def _has_exception_handler_kwarg(call: ast.Call) -> bool:
    return any(kw.arg in EXCEPTION_HANDLER_NAMES for kw in call.keywords if kw.arg)


def _unwrap_chain(node: ast.expr) -> tuple[ast.Call | None, set[str]]:
    chained: set[str] = set()
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        chained.add(cur.func.attr)
        cur = cur.func.value
    if isinstance(cur, ast.Call):
        return cur, chained
    return None, chained


def _file_has_default_phase_hooks(tree: ast.AST) -> bool:
    names = set()
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(item.name)
        elif isinstance(item, ast.Name):
            names.add(item.id)
    return bool(names & DEFAULT_PHASE_HOOK_MARKERS)


def _is_phase_call(node: ast.Call) -> bool:
    name = _dotted(node.func)
    base = name.rsplit('.', 1)[-1]
    return base in ('Phase', 'registerPhase')


class PhaseHookFinding:
    def __init__(self, lineno: int, code: str, severity: str, message: str, fix_hint: str, sample_code: str):
        self.lineno = lineno
        self.code = code
        self.severity = severity
        self.message = message
        self.fix_hint = fix_hint
        self.sample_code = sample_code


def _root_has_default_phase_hooks(root: Path) -> bool:
    try:
        for rel in ('classes/_phase.py', 'start.py'):
            text = (root / rel).read_text(encoding='utf-8', errors='replace')
            if '_default_phase_error_hook' in text or '_defaultStartPhaseErrorHook' in text:
                return True
    except Exception:
        pass
    return False


def check_phase_hooks(tree: ast.AST, lines: list[str], *, default_hooks_available: bool = False) -> list[PhaseHookFinding]:
    findings: list[PhaseHookFinding] = []
    default_hooks_available = bool(default_hooks_available or _file_has_default_phase_hooks(tree))
    for node in ast.walk(tree):
        exprs: list[ast.expr] = []
        if isinstance(node, ast.Expr):
            exprs.append(node.value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            val = node.value
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
            kwarg_keys = _kwarg_names_normalized(inner)
            has_exception_handler = _has_exception_handler_kwarg(inner) or 'exceptionHandler' in chained_attrs
            all_hooked = kwarg_keys | chained_attrs
            snippet = _sample(lines, lineno)
            missing_required = [h for h in REQUIRED_HOOKS if h not in all_hooked]
            missing_optional = [h for h in OPTIONAL_HOOKS if h not in all_hooked]
            if default_hooks_available:
                continue
            if missing_required:
                no_exception_at_all = 'onException' not in all_hooked and not has_exception_handler
                if no_exception_at_all:
                    findings.append(PhaseHookFinding(lineno, 'PHASE_NO_EXCEPTION_HOOK', 'HIGH',
                        f"Phase call has no exception handler at all (missing onFault, onException, onTimeout): {snippet}",
                        "No exception handling. Use Pattern B (exceptionHandler class attribute) or Pattern A (kwargs).",
                        _SAMPLE_PATTERN_B + '\n' + _SAMPLE_PATTERN_A))
                else:
                    hooks_str = ', '.join(missing_required)
                    findings.append(PhaseHookFinding(lineno, 'PHASE_MISSING_HOOKS', 'HIGH',
                        f"Phase call missing required hook(s) [{hooks_str}]: {snippet}",
                        f"Add missing hook(s): {hooks_str}. Pass as kwargs or chain (.onFault(h).onException(h).onTimeout(h)).",
                        _SAMPLE_PATTERN_A))
            elif missing_optional:
                hooks_str = ', '.join(missing_optional)
                findings.append(PhaseHookFinding(lineno, 'PHASE_MISSING_OPTIONAL_HOOKS', 'MEDIUM',
                    f"Phase call missing optional hook(s) [{hooks_str}]: {snippet}",
                    f"Consider adding optional hook(s): {hooks_str}. onError fires on non-fault errors.",
                    _SAMPLE_PATTERN_A))
    return findings


def _class_bases(classdef: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in classdef.bases:
        name = _dotted(base)
        names.add(name.rsplit('.', 1)[-1])
    return names


def _class_method_names(classdef: ast.ClassDef) -> set[str]:
    return {node.name for node in classdef.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _class_has_attribute(classdef: ast.ClassDef, attr_name: str) -> bool:
    for node in classdef.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == attr_name:
                    return True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == attr_name:
                return True
    return False


def check_phase_class_architecture(tree: ast.AST, lines: list[str]) -> list[PhaseHookFinding]:
    findings: list[PhaseHookFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not (_class_bases(node) & PHASE_BASE_NAMES):
            continue
        methods = _class_method_names(node)
        if 'process' not in methods and 'run' not in methods:
            continue
        if _has_ok(lines, node.lineno):
            continue
        has_exception_handler_attr = any(_class_has_attribute(node, n) for n in EXCEPTION_HANDLER_NAMES)
        missing_hooks = [h for h in REQUIRED_HOOKS if h not in methods]
        missing_optional = [h for h in OPTIONAL_HOOKS if h not in methods]
        if missing_hooks:
            hooks_str = ', '.join(missing_hooks)
            no_exception_at_all = 'onException' not in methods and not has_exception_handler_attr
            if no_exception_at_all:
                findings.append(PhaseHookFinding(node.lineno, 'PHASE_CLASS_NO_EXCEPTION_HANDLER', 'HIGH',
                    f"Phase subclass '{node.name}' has process() but no exception handling (missing {hooks_str}): {_sample(lines, node.lineno)}",
                    f"Add 'exceptionHandler = Exception' and implement onFault, onException, onTimeout, onError.",
                    _SAMPLE_PHASE_CLASS_FIX))
            else:
                findings.append(PhaseHookFinding(node.lineno, 'PHASE_CLASS_MISSING_HOOKS', 'HIGH',
                    f"Phase subclass '{node.name}' missing hook method(s) [{hooks_str}]: {_sample(lines, node.lineno)}",
                    f"Implement missing methods: {hooks_str}. Each should call recordException().",
                    _SAMPLE_PHASE_CLASS_FIX))
        elif missing_optional and not has_exception_handler_attr:
            findings.append(PhaseHookFinding(node.lineno, 'PHASE_CLASS_NO_EXCEPTION_HANDLER_ATTR', 'MEDIUM',
                f"Phase subclass '{node.name}' has hooks but no 'exceptionHandler' class attribute: {_sample(lines, node.lineno)}",
                "Add: exceptionHandler = Exception (or a more specific exception class).",
                _SAMPLE_PHASE_CLASS_FIX))
    return findings


def _is_lifecycle_start_call(node: ast.Call) -> bool:
    name = _dotted(node.func)
    parts = name.rsplit('.', 1)
    if len(parts) != 2:
        return False
    attr, owner = parts[-1], parts[0].rsplit('.', 1)[-1]
    return attr in LIFECYCLE_START_ATTRS and owner in LIFECYCLE_OWNER_NAMES


def _expr_contains_lifecycle_start(expr: ast.expr) -> bool:
    return any(isinstance(child, ast.Call) and _is_lifecycle_start_call(child) for child in ast.walk(expr))


def check_main_discipline(tree: ast.AST, lines: list[str]) -> list[PhaseHookFinding]:
    findings: list[PhaseHookFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != 'main':
            continue
        has_lifecycle_start = any(
            isinstance(child, ast.Call) and _is_lifecycle_start_call(child)
            for child in ast.walk(node) if child is not node
        )
        if not has_lifecycle_start and not _has_ok(lines, node.lineno):
            findings.append(PhaseHookFinding(node.lineno, 'MAIN_NO_LIFECYCLE_START', 'HIGH',
                'main() never calls a lifecycle start/run method (appLifeCycle.start(), lifecycle.runApplication(), etc.)',
                "main() must call a lifecycle start method so phases are owned by the lifecycle controller.",
                _SAMPLE_MAIN_FIX))
        for stmt in node.body:
            if not isinstance(stmt, ast.Expr):
                continue
            if _has_ok(lines, stmt.lineno):
                continue
            if _expr_contains_lifecycle_start(stmt.value):
                continue
            if isinstance(stmt.value, ast.Call) and _dotted(stmt.value.func).rsplit('.', 1)[-1] in ALLOWED_MAIN_BARE_CALLS:
                continue
            findings.append(PhaseHookFinding(stmt.lineno, 'MAIN_BARE_CALL', 'MEDIUM',
                f'main() contains a bare call -- move into a Phase on appLifeCycle: {_sample(lines, stmt.lineno)}',
                "main() should only construct the app object and call lifecycle.start().",
                _SAMPLE_MAIN_FIX))
    return findings


def _format_finding_block(rel: str, f: PhaseHookFinding) -> list[str]:
    sep = '-' * 72
    lines_out = [sep, f'  [{f.severity}] {f.code}', f'  File: {rel}:{f.lineno}', f'  {f.message}', '', '  HOW TO FIX:']
    for hint_line in f.fix_hint.splitlines():
        lines_out.append(f'  {hint_line}')
    lines_out.append('')
    lines_out.append('  SAMPLE CODE:')
    for code_line in f.sample_code.rstrip().splitlines():
        lines_out.append(f'  {code_line}')
    lines_out.append('')
    return lines_out


class PhaseHooksDetector(Detector):
    NAME = 'phase-hooks'
    VERSION = '2.0.0'
    REPORT_HEADER = 'PHASE HOOKS DETECTOR REPORT  (AST-based)'
    DEFAULT_OUTPUT = 'logs/phase_hooks.txt'
    VERBOSE = True

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        all_phf: list[PhaseHookFinding] = []
        all_phf.extend(check_phase_hooks(tree, lines, default_hooks_available=_root_has_default_phase_hooks(root)))
        all_phf.extend(check_phase_class_architecture(tree, lines))
        entrypoint_names = {'start.py', 'cutiepy.py', 'cutiepy.headless.py'}
        if path.name in entrypoint_names:
            all_phf.extend(check_main_discipline(tree, lines))
        return [
            Finding(path, phf.lineno, 0, phf.severity, phf.code, phf.message, '',
                    fix_hint=phf.fix_hint, sample_code=phf.sample_code)
            for phf in all_phf
        ]

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        phase_f = [f for f in findings if f.rule.startswith('PHASE_') and not f.rule.startswith('PHASE_CLASS')]
        class_f = [f for f in findings if f.rule.startswith('PHASE_CLASS')]
        main_f = [f for f in findings if f.rule.startswith('MAIN_')]
        total = len(findings)
        high = sum(1 for f in findings if f.severity == 'HIGH')
        medium = sum(1 for f in findings if f.severity == 'MEDIUM')

        lines_out = [
            self.REPORT_HEADER,
            '=' * len(self.REPORT_HEADER),
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Total findings: {total}  (HIGH={high}, MEDIUM={medium})',
            f'  Phase call hook findings:   {len(phase_f)}',
            f'  Phase class arch findings:  {len(class_f)}',
            f'  main() discipline findings: {len(main_f)}',
            '',
            'ARCHITECTURE CONTRACT',
            '--------------------',
            'Every Phase must wire: onFault, onException, onTimeout.',
            f'Suppress with:  # {OK_MARKER}',
            '',
        ]

        def render_section(title: str, fs: list[Finding]) -> None:
            if not fs:
                return
            lines_out.append('=' * 72)
            lines_out.append(f'  {title}')
            lines_out.append('=' * 72)
            lines_out.append('')
            for f in sorted(fs, key=lambda x: (str(x.path), x.line)):
                try:
                    rel = str(f.path.relative_to(root))
                except ValueError:
                    rel = str(f.path)
                phf = PhaseHookFinding(f.line, f.rule, f.severity, f.message, f.fix_hint, f.sample_code)
                lines_out.extend(_format_finding_block(rel, phf))

        render_section('SECTION 1: PHASE CALL HOOK VIOLATIONS', phase_f)
        render_section('SECTION 2: PHASE CLASS ARCHITECTURE VIOLATIONS', class_f)
        render_section('SECTION 3: MAIN() DISCIPLINE VIOLATIONS', main_f)

        if not total:
            lines_out.append('No phase hook, class architecture, or main() discipline violations found.')

        return '\n'.join(lines_out) + '\n'

    def _run(self, argv=None):
        import argparse
        from vendor.claude.detector_base import discover_project_root, iter_py
        from vendor.claude.detector_runtime import tracedWriteText
        discovered = discover_project_root()
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default=str(discovered))
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('--no-phase-check', action='store_true')
        ap.add_argument('--no-class-check', action='store_true')
        ap.add_argument('--no-main-check', action='store_true')
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)
        root = Path(ns.root).resolve()
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
    raise SystemExit(PhaseHooksDetector._run(PhaseHooksDetector()))
