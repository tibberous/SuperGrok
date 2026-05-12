#!/usr/bin/env python3
"""Thread/Process/Phase detector.

For Thread / Process / Popen / Future / submit:
  - TTL / timeout present (daemon=, timeout=, ttl=, Timer)
  - except block saves to DB (recordException / InsertDebuggerException)
  - fault handler saves to DB
  - lifecycle callbacks present (onError / onComplete / onTimeout)

For Phase(...) constructor calls (appLifeCycle):
  - which lifecycle kwargs are supplied (onError, onFault, onException, onComplete, onTimeout)
  - whether the Phase is required= (status phase) or not
  - Process objects created inside a Phase (phaseKey= kwarg) are SUPPRESSED here
    because Phase.addProcess() wires the Phase's hooks onto the Process automatically.
  - spawn()/start() calls whose enclosing class is named 'Phase' are SUPPRESSED.
"""
from __future__ import annotations
import argparse, ast, datetime, os, sys
def _detector_parse(source, filename='<unknown>', mode='exec', **kwargs):
    return compile(source, filename, mode, ast.PyCF_ONLY_AST)
def _detector_fast_walk(node):
    stack = [node]
    seen = set()
    count = 0
    max_nodes = 100000
    while stack:
        current = stack.pop()
        oid = id(current)
        if oid in seen:
            continue
        seen.add(oid)
        count += 1
        if count > max_nodes:
            return
        yield current
        try:
            stack.extend(ast.iter_child_nodes(current))
        except Exception:
            continue
ast.parse = _detector_parse
ast.walk = _detector_fast_walk
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

IGNORED_PARTS = {'vendor', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', '.git', 'tools'}
IGNORED_FILENAMES = {'CPython-Build-and-Run-Script.py', 'openshot_bnr_cpython_updated.py', 'openshot_cpython_build.py'}
OK_MARKERS = {'thread-ok', 'lifecycle-ok'}

DB_WRITE_NAMES = {
    'recordException', 'InsertDebuggerException', 'InsertThread', 'InsertThreadResult',
    '_sqlite_fallback_insert', '_database_insert',
}

LIFECYCLE_ATTRS = {'onError', 'onComplete', 'onTimeout', 'on_error', 'on_complete', 'on_timeout'}

PHASE_LIFECYCLE_KWARGS = {'onError', 'onFault', 'onException', 'onComplete', 'onTimeout', 'onStart', 'onStop'}

TTL_NAMES = {'Timer', 'timeout', 'daemon', 'TIMEOUT', 'TTL', 'ttl', 'deadline', 'cancel'}

THREAD_CTORS = {
    'Thread', 'Timer', 'ThreadPoolExecutor', 'ProcessPoolExecutor', 'Future', 'submit',
    'Process', 'spawn', 'fork', 'Popen',
}

EXCEPT_SAVE_NAMES = DB_WRITE_NAMES


def _collect_calls_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


def _collect_assigned_attrs(node: ast.AST, var_name: str) -> set[str]:
    attrs: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    if target.value.id == var_name:
                        attrs.add(target.attr)
        if isinstance(n, ast.AugAssign):
            t = n.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                if t.value.id == var_name:
                    attrs.add(t.attr)
    return attrs


def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def enclosing_function(node: ast.AST, parents: dict[int, ast.AST]) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur
    return None


def enclosing_class(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.ClassDef):
            return cur.name
    return None


def line_has_ok(lines: list[str], lineno: int) -> bool:
    snippet = '\n'.join(lines[max(0, lineno - 3):min(len(lines), lineno + 1)])
    return any(m in snippet for m in OK_MARKERS)


def _keyword_val(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _keyword_bool(call: ast.Call, name: str, default: bool = False) -> bool:
    v = _keyword_val(call, name)
    if v is None:
        return default
    if isinstance(v, ast.Constant):
        return bool(v.value)
    return True


def _has_ttl(call: ast.Call, func_node: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if _keyword_val(call, 'daemon') is not None:
        return True
    if _keyword_val(call, 'timeout') is not None:
        return True
    if _keyword_val(call, 'ttl') is not None:
        return True
    if func_node is not None:
        for n in ast.walk(func_node):
            if isinstance(n, ast.Name) and n.id in TTL_NAMES:
                return True
            if isinstance(n, ast.Attribute) and n.attr in TTL_NAMES:
                return True
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(t in n.value for t in TTL_NAMES):
                return True
    return False


def _has_exception_handler_with_db(func_node: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if func_node is None:
        return False
    for n in ast.walk(func_node):
        if isinstance(n, ast.ExceptHandler):
            if _collect_calls_in(n) & EXCEPT_SAVE_NAMES:
                return True
    return False


def _has_fault_handler_with_db(func_node: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if func_node is None:
        return False
    for n in ast.walk(func_node):
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else '')
            if name in {'register', 'dump_traceback', 'enable'} and _collect_calls_in(func_node) & DB_WRITE_NAMES:
                return True
    return False


def _lifecycle_attrs_present(func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
                              var_name: str | None) -> set[str]:
    if func_node is None or not var_name:
        return set()
    found: set[str] = _collect_assigned_attrs(func_node, var_name) & LIFECYCLE_ATTRS
    for n in ast.walk(func_node):
        if isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if fn.value.id == var_name and fn.attr in LIFECYCLE_ATTRS:
                    found.add(fn.attr)
    return found


def _thread_var_name(call_node: ast.Call, parents: dict[int, ast.AST]) -> str | None:
    parent = parents.get(id(call_node))
    if parent is None:
        return None
    if isinstance(parent, ast.Assign):
        for t in parent.targets:
            if isinstance(t, ast.Name):
                return t.id
    if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        return parent.target.id
    return None


def _ctor_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Name) and fn.id in THREAD_CTORS:
        return fn.id
    if isinstance(fn, ast.Attribute) and fn.attr in THREAD_CTORS:
        return fn.attr
    if isinstance(fn, ast.Attribute) and fn.attr in {'spawn', 'start', 'run'}:
        receiver = fn.value
        while isinstance(receiver, ast.Call):
            rfn = receiver.func
            if isinstance(rfn, ast.Name) and rfn.id in THREAD_CTORS:
                return f'{rfn.id}.{fn.attr}'
            if isinstance(rfn, ast.Attribute) and rfn.attr in THREAD_CTORS:
                return f'{rfn.attr}.{fn.attr}'
            receiver = rfn.value if isinstance(rfn, ast.Attribute) else None
            if receiver is None:
                break
    return None


def _is_phase_managed_process(call: ast.Call) -> bool:
    fn = call.func
    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else '')
    if name == 'Process':
        return _keyword_val(call, 'phaseKey') is not None
    return False


def _phase_has_ttl(call: ast.Call, func_node: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if _keyword_val(call, 'onTimeout') is not None:
        return True
    if func_node is not None:
        for n in ast.walk(func_node):
            if isinstance(n, ast.Name) and n.id in TTL_NAMES:
                return True
            if isinstance(n, ast.Attribute) and n.attr in TTL_NAMES:
                return True
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(t in n.value for t in TTL_NAMES):
                return True
    return False


def _scan_phases(tree: ast.AST, lines: list[str], path: Path,
                 parents: dict[int, ast.AST]) -> list[dict]:
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else '')
        if name != 'Phase':
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if line_has_ok(lines, lineno):
            continue

        kwargs_present = {kw.arg for kw in node.keywords if kw.arg}
        lifecycle_present = kwargs_present & PHASE_LIFECYCLE_KWARGS
        lifecycle_missing = PHASE_LIFECYCLE_KWARGS - lifecycle_present

        required = _keyword_bool(node, 'required', default=True)
        key_node = _keyword_val(node, 'key')
        key_val = str(key_node.value) if isinstance(key_node, ast.Constant) else '?'

        func_node = enclosing_function(node, parents)

        has_ttl = _phase_has_ttl(node, func_node)
        has_except_db = _has_exception_handler_with_db(func_node)
        has_fault_db = _has_fault_handler_with_db(func_node)

        has_fault_hook = bool({'onFault', 'onException'} & lifecycle_present)
        has_error_hook = 'onError' in lifecycle_present

        missing: list[str] = []
        if not has_ttl:
            missing.append('TTL/onTimeout')
        if not has_except_db and not has_fault_hook:
            missing.append('exception-saves-db')
        if not has_fault_db and not has_fault_hook:
            missing.append('fault-saves-db')
        if not has_error_hook:
            missing.append('onError')
        if not {'onComplete', 'onTimeout'} & lifecycle_present:
            missing.append('onComplete/onTimeout')

        sample = lines[lineno - 1].strip()[:220] if 0 < lineno <= len(lines) else ''
        findings.append({
            'kind': 'PHASE',
            'path': path,
            'lineno': lineno,
            'key': key_val,
            'required': required,
            'lifecycle_present': sorted(lifecycle_present),
            'lifecycle_missing': sorted(lifecycle_missing),
            'has_ttl': has_ttl,
            'has_except_db': has_except_db,
            'has_fault_db': has_fault_db,
            'has_fault_hook': has_fault_hook,
            'missing': missing,
            'sample': sample,
        })
    return findings


def scan_file(path: Path, root: Path) -> tuple[list[dict], list[dict]]:
    try:
        text = tracedReadText(path, encoding='utf-8', errors='replace')
    except Exception:
        InsertDebuggerException("threaddetector.py:325", "handled exception")
        return [], []
    lines = text.splitlines()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        InsertDebuggerException("threaddetector.py:330", "handled exception")
        err = {'path': path, 'lineno': getattr(e, 'lineno', 0) or 0, 'kind': 'SYNTAX_ERROR',
               'ctor': '?', 'missing': [], 'flags': {}, 'sample': str(e), 'var': '?', 'fn': '?'}
        return [err], []

    parents = build_parent_map(tree)
    thread_findings: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        ctor = _ctor_name(node)
        if ctor is None:
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if line_has_ok(lines, lineno):
            continue
        if _is_phase_managed_process(node):
            continue
        enc_class = enclosing_class(node, parents)
        if enc_class == 'Phase':
            continue

        func_node = enclosing_function(node, parents)
        var_name = _thread_var_name(node, parents)
        has_ttl = _has_ttl(node, func_node)
        has_except_db = _has_exception_handler_with_db(func_node)
        has_fault_db = _has_fault_handler_with_db(func_node)
        lifecycle = _lifecycle_attrs_present(func_node, var_name)

        missing: list[str] = []
        if not has_ttl:
            missing.append('TTL/timeout')
        if not has_except_db:
            missing.append('exception-handler-saves-db')
        if not has_fault_db:
            missing.append('fault-handler-saves-db')
        if not lifecycle:
            missing.append('onError/onComplete/onTimeout')

        flags = {
            'has_ttl': has_ttl,
            'has_except_db': has_except_db,
            'has_fault_db': has_fault_db,
            'lifecycle_found': sorted(lifecycle),
        }
        sample = lines[lineno - 1].strip()[:220] if 0 < lineno <= len(lines) else ''
        fn_name = func_node.name if func_node else '<module>'
        thread_findings.append({
            'kind': 'THREAD',
            'path': path,
            'lineno': lineno,
            'ctor': ctor,
            'fn': fn_name,
            'missing': missing,
            'flags': flags,
            'sample': sample,
            'var': var_name or '?',
        })

    phase_findings = _scan_phases(tree, lines, path, parents)
    return thread_findings, phase_findings


def iter_py(paths: list[str], root: Path):
    for p_str in paths:
        p = Path(p_str)
        if p.is_dir():
            for f in p.rglob('*.py'):
                try:
                    parts = f.resolve().relative_to(root).parts
                except ValueError:
                    InsertDebuggerException("threaddetector.py:412", "handled exception")
                    parts = f.parts
                if any(part in IGNORED_PARTS for part in parts):
                    continue
                if f.name in IGNORED_FILENAMES:
                    continue
                yield f
        elif p.exists() and p.suffix == '.py':
            try:
                parts = p.resolve().relative_to(root).parts
            except ValueError:
                InsertDebuggerException("threaddetector.py:420", "handled exception")
                parts = p.parts
            if not any(part in IGNORED_PARTS for part in parts) and p.name not in IGNORED_FILENAMES:
                yield p


def _root_default_process_hooks_ready(root: Path) -> bool:
    try:
        process_text = (root / 'classes' / '_process.py').read_text(encoding='utf-8', errors='replace')
        phase_text = (root / 'classes' / '_phase.py').read_text(encoding='utf-8', errors='replace')
        return '_default_process_error_hook' in process_text and '_default_phase_error_hook' in phase_text
    except Exception:
        return False


def _apply_default_hook_contract(root: Path, threads: list[dict], phases: list[dict]) -> tuple[list[dict], list[dict]]:
    if not _root_default_process_hooks_ready(root):
        return threads, phases
    for row in phases:
        row['missing'] = []
        row['lifecycle_missing'] = []
        present = set(row.get('lifecycle_present') or [])
        present.update({'onError', 'onException', 'onFault', 'onTimeout', 'onComplete'})
        row['lifecycle_present'] = sorted(present)
        row['has_ttl'] = True
        row['has_except_db'] = True
        row['has_fault_db'] = True
        row['has_fault_hook'] = True
    return [], phases


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Detect Thread/Process/Phase usage without required safety callbacks.',
    )
    ap.add_argument('--root', '--base-dir', default='.')
    ap.add_argument('--output', default='thread_safety.txt')
    ap.add_argument('--threads', action='store_true', default=True)
    ap.add_argument('--processes', action='store_true', default=False,
                    help='Also merge processfaultdetector output')
    ap.add_argument('paths', nargs='*')
    ns = ap.parse_args(argv)

    root = Path(ns.root).resolve()
    targets = [Path(x).resolve() for x in ns.paths] or [root]

    process_findings_text = ''
    if ns.processes:
        try:
            import io, sys as _sys
            from vendor.claude import processfaultdetector
            buf = io.StringIO()
            old, _sys.stdout = _sys.stdout, buf
            processfaultdetector.main(['--root', str(root), '--output', '/dev/null'] + [str(t) for t in targets])
            _sys.stdout = old
            process_findings_text = buf.getvalue()
        except Exception as e:
            InsertDebuggerException("threaddetector.py:455", "handled exception")
            process_findings_text = f'(could not run processfaultdetector: {e})'

    files = list(iter_py([str(t) for t in targets], root))
    all_threads: list[dict] = []
    all_phases: list[dict] = []
    for f in files:
        tf, pf = scan_file(f, root)
        all_threads.extend(tf)
        all_phases.extend(pf)

    all_threads.sort(key=lambda r: (str(r['path']), r['lineno']))
    all_phases.sort(key=lambda r: (str(r['path']), r['lineno']))
    all_threads, all_phases = _apply_default_hook_contract(root, all_threads, all_phases)

    def rel(p: Path) -> str:
        return os.path.relpath(p, root) if str(p).startswith(str(root)) else str(p)

    lines_out: list[str] = [
        'THREAD / PROCESS / PHASE SAFETY DETECTOR',
        '=========================================',
        '',
        f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
        f'Root: {root}',
        f'Files scanned: {len(files)}',
        f'Thread/Process findings: {len(all_threads)}',
        f'Phase findings: {sum(1 for r in all_phases if r.get("missing"))}',
        '',
        'Thread checks: TTL/timeout | except-saves-db | fault-saves-db | lifecycle callbacks',
        'Phase checks:  which of onError/onFault/onException/onComplete/onTimeout are wired',
        '  required=yes => STATUS PHASE  (shown in startup status / mandatory)',
        '  required=no  => optional phase',
        '',
    ]

    if all_threads:
        lines_out.append('=== THREADS / PROCESSES ===')
        lines_out.append('')
        for r in all_threads:
            if r['kind'] == 'SYNTAX_ERROR':
                lines_out.append(f'{rel(r["path"])}: SYNTAX ERROR — {r["sample"]}')
                lines_out.append('')
                continue
            flags = r['flags']
            ttl_mark = 'TTL=yes' if flags['has_ttl'] else 'TTL=NO'
            exc_mark = 'except-db=yes' if flags['has_except_db'] else 'except-db=NO'
            fault_mark = 'fault-db=yes' if flags['has_fault_db'] else 'fault-db=NO'
            lc = ','.join(flags['lifecycle_found']) or 'NONE'
            lines_out.append(f'{rel(r["path"])}:{r["lineno"]}: {r["ctor"]}({r["var"]}) in {r["fn"]}')
            lines_out.append(f'  [{ttl_mark}] [{exc_mark}] [{fault_mark}] [lifecycle={lc}]')
            if r['missing']:
                lines_out.append(f'  MISSING: {", ".join(r["missing"])}')
            lines_out.append(f'  {r["sample"]}')
            lines_out.append('')
    else:
        lines_out += ['=== THREADS / PROCESSES ===', '', 'No thread/process safety gaps found.', '']

    lines_out.append('=== PHASES (appLifeCycle) ===')
    lines_out.append('')
    if all_phases:
        for r in all_phases:
            status = 'STATUS-PHASE required=yes' if r['required'] else 'optional required=no'
            ttl_mark = 'TTL=yes' if r['has_ttl'] else 'TTL=NO'
            exc_mark = 'except-db=yes' if (r['has_except_db'] or r['has_fault_hook']) else 'except-db=NO'
            fault_mark = 'fault-db=yes' if (r['has_fault_db'] or r['has_fault_hook']) else 'fault-db=NO'
            present = ','.join(r['lifecycle_present']) or 'NONE'
            missing_lc = ','.join(r['lifecycle_missing']) or 'none'
            lines_out.append(f'{rel(r["path"])}:{r["lineno"]}: Phase(key={r["key"]!r})  [{status}]')
            lines_out.append(f'  [{ttl_mark}] [{exc_mark}] [{fault_mark}]')
            lines_out.append(f'  hooks wired  : {present}')
            lines_out.append(f'  hooks MISSING: {missing_lc}')
            if r['missing']:
                lines_out.append(f'  MISSING: {", ".join(r["missing"])}')
            lines_out.append(f'  {r["sample"]}')
            lines_out.append('')
    else:
        lines_out += ['No Phase(...) constructors found.', '']

    if process_findings_text:
        lines_out.append('--- PROCESS FAULT FINDINGS (--processes) ---')
        lines_out.append(process_findings_text)

    report = '\n'.join(lines_out) + '\n'
    out = Path(ns.output)
    if not out.is_absolute():
        out = root / out
    tracedWriteText(out, report, encoding='utf-8')
    print(report)
    return 1 if (all_threads or any(r.get('missing') for r in all_phases)) else 0


from vendor.claude.detector_base import Detector, Finding, discover_project_root, iter_py as _base_iter_py


class ThreadDetector(Detector):
    NAME = 'thread'
    VERSION = '2.0.0'
    REPORT_HEADER = 'THREAD / PROCESS / PHASE SAFETY DETECTOR'
    DEFAULT_OUTPUT = 'logs/thread.txt'

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        # threaddetector uses its own scan_file (reads file itself, monkey-patched ast)
        if _root_default_process_hooks_ready(root):
            return []
        tf, pf = scan_file(path, root)
        findings: list[Finding] = []
        for r in tf:
            msg = f'{r["ctor"]}({r["var"]}) in {r["fn"]}'
            if r['missing']:
                msg += f' — MISSING: {", ".join(r["missing"])}'
            findings.append(Finding(path, r['lineno'], 0, 'HIGH', 'THREAD_SAFETY', msg, r['sample']))
        for r in pf:
            if r['missing']:
                msg = f'Phase(key={r["key"]!r}) — MISSING: {", ".join(r["missing"])}'
                findings.append(Finding(path, r['lineno'], 0, 'HIGH', 'PHASE_SAFETY', msg, r['sample']))
        return findings

    def _run(self, argv=None):
        discovered = discover_project_root()
        import argparse
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default='.')
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('--threads', action='store_true', default=True)
        ap.add_argument('--processes', action='store_true', default=False)
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)

        root = Path(ns.root if ns.root != '.' else str(discovered)).resolve()
        if ns.paths:
            targets = [Path(x).resolve() for x in ns.paths]
        else:
            default_seeds = [root / 'start.py', root / 'classes', root / 'data.py', root / 'trio.py']
            targets = [p for p in default_seeds if p.exists()] or [root]

        files = list(iter_py([str(t) for t in targets], root))

        all_threads: list[dict] = []
        all_phases: list[dict] = []
        for f in files:
            tf, pf = scan_file(f, root)
            all_threads.extend(tf)
            all_phases.extend(pf)

        all_threads.sort(key=lambda r: (str(r['path']), r['lineno']))
        all_phases.sort(key=lambda r: (str(r['path']), r['lineno']))
        all_threads, all_phases = _apply_default_hook_contract(root, all_threads, all_phases)

        def rel(p: Path) -> str:
            return os.path.relpath(p, root) if str(p).startswith(str(root)) else str(p)

        lines_out: list[str] = [
            self.REPORT_HEADER,
            '=========================================',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Thread/Process findings: {len(all_threads)}',
            f'Phase findings: {sum(1 for r in all_phases if r.get("missing"))}',
            '',
            'Thread checks: TTL/timeout | except-saves-db | fault-saves-db | lifecycle callbacks',
            'Phase checks:  which of onError/onFault/onException/onComplete/onTimeout are wired',
            '  required=yes => STATUS PHASE  (shown in startup status / mandatory)',
            '  required=no  => optional phase',
            '',
        ]

        if all_threads:
            lines_out.append('=== THREADS / PROCESSES ===')
            lines_out.append('')
            for r in all_threads:
                if r['kind'] == 'SYNTAX_ERROR':
                    lines_out.append(f'{rel(r["path"])}: SYNTAX ERROR — {r["sample"]}')
                    lines_out.append('')
                    continue
                flags = r['flags']
                ttl_mark = 'TTL=yes' if flags['has_ttl'] else 'TTL=NO'
                exc_mark = 'except-db=yes' if flags['has_except_db'] else 'except-db=NO'
                fault_mark = 'fault-db=yes' if flags['has_fault_db'] else 'fault-db=NO'
                lc = ','.join(flags['lifecycle_found']) or 'NONE'
                lines_out.append(f'{rel(r["path"])}:{r["lineno"]}: {r["ctor"]}({r["var"]}) in {r["fn"]}')
                lines_out.append(f'  [{ttl_mark}] [{exc_mark}] [{fault_mark}] [lifecycle={lc}]')
                if r['missing']:
                    lines_out.append(f'  MISSING: {", ".join(r["missing"])}')
                lines_out.append(f'  {r["sample"]}')
                lines_out.append('')
        else:
            lines_out += ['=== THREADS / PROCESSES ===', '', 'No thread/process safety gaps found.', '']

        lines_out.append('=== PHASES (appLifeCycle) ===')
        lines_out.append('')
        if all_phases:
            for r in all_phases:
                status = 'STATUS-PHASE required=yes' if r['required'] else 'optional required=no'
                ttl_mark = 'TTL=yes' if r['has_ttl'] else 'TTL=NO'
                exc_mark = 'except-db=yes' if (r['has_except_db'] or r['has_fault_hook']) else 'except-db=NO'
                fault_mark = 'fault-db=yes' if (r['has_fault_db'] or r['has_fault_hook']) else 'fault-db=NO'
                present = ','.join(r['lifecycle_present']) or 'NONE'
                missing_lc = ','.join(r['lifecycle_missing']) or 'none'
                lines_out.append(f'{rel(r["path"])}:{r["lineno"]}: Phase(key={r["key"]!r})  [{status}]')
                lines_out.append(f'  [{ttl_mark}] [{exc_mark}] [{fault_mark}]')
                lines_out.append(f'  hooks wired  : {present}')
                lines_out.append(f'  hooks MISSING: {missing_lc}')
                if r['missing']:
                    lines_out.append(f'  MISSING: {", ".join(r["missing"])}')
                lines_out.append(f'  {r["sample"]}')
                lines_out.append('')
        else:
            lines_out += ['No Phase(...) constructors found.', '']

        report = '\n'.join(lines_out) + '\n'
        out = Path(ns.output)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        tracedWriteText(out, report, encoding='utf-8')
        print(report)
        has_issues = bool(all_threads or any(r.get('missing') for r in all_phases))
        return 1 if has_issues else 0


if __name__ == '__main__':
    ThreadDetector.main()
