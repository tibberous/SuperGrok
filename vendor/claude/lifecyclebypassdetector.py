#!/usr/bin/env python3
from __future__ import annotations
import ast
import collections
import datetime
import os
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding, build_parent_map, has_ok_marker, enclosing_function_name, enclosing_class_name, dotted_name as _base_dotted_name
from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText

# ---------------------------------------------------------------------------
# What this detector enforces:
#  1. DIRECT_SPAWN   — raw subprocess/os.system/thread outside an approved wrapper
#  2. NO_TTL         — Phase()/StartProcess() constructed without ttl
#  3. NO_PID         — .start() called with no pid capture nearby
#  4. BARE_SLEEP     — time.sleep() at module scope
#  5. INLINE_BLOCK   — blocking .wait()/.join() without timeout
#  6. BLOCKING_MAIN_THREAD — .communicate()/subprocess.run() on main Qt thread
# ---------------------------------------------------------------------------

BLOCKED_SPAWN = {
    'subprocess.Popen': 'DIRECT_SPAWN', 'subprocess.run': 'DIRECT_SPAWN',
    'subprocess.call': 'DIRECT_SPAWN', 'subprocess.check_call': 'DIRECT_SPAWN',
    'subprocess.check_output': 'DIRECT_SPAWN', 'subprocess.getoutput': 'DIRECT_SPAWN',
    'subprocess.getstatusoutput': 'DIRECT_SPAWN', 'os.system': 'DIRECT_SPAWN',
    'os.popen': 'DIRECT_SPAWN', 'os.execv': 'DIRECT_SPAWN', 'os.execve': 'DIRECT_SPAWN',
    'os.execvp': 'DIRECT_SPAWN', 'os.spawnl': 'DIRECT_SPAWN', 'os.spawnle': 'DIRECT_SPAWN',
    'os.spawnlp': 'DIRECT_SPAWN', 'os.spawnv': 'DIRECT_SPAWN',
    'threading.Thread': 'DIRECT_SPAWN', 'multiprocessing.Process': 'DIRECT_SPAWN',
    'QThread': 'DIRECT_SPAWN',
    'concurrent.futures.ThreadPoolExecutor': 'DIRECT_SPAWN',
    'concurrent.futures.ProcessPoolExecutor': 'DIRECT_SPAWN',
    'ThreadPoolExecutor': 'DIRECT_SPAWN', 'ProcessPoolExecutor': 'DIRECT_SPAWN',
}

SPAWN_ALLOWLIST_FUNCS = {
    'launcherRunCommand', 'launcherStartProcess', '_early_run_command', '_runCapturedCommand',
    '_roughRunCommand', 'managedSubprocessRun', 'managedSubprocessPopen',
    'lifecycleSubprocessRun', 'lifecycleSubprocessPopen',
    'runClaudeDetector', 'applicationRun', 'attemptPlainPipInstall', 'runHookCommand', 'startHookProcess',
    'StartWorkerProcess', '_early_run_all_claude_detectors', '_early_run_one_detector',
    '_launch_process', '_kill_process', '_reap', 'run_process', 'start_process', '_start', 'launch',
    'taskkill_process',
}
SPAWN_ALLOWLIST_CLASSES = {
    'StartProcess', 'LifeCycleController', 'AppLifeCycleController',
    'StartLifecycleController', 'StartDependencyRegistry',
    'Process', 'ManagedProcess', 'AppLifeCycle', 'Thread',
}
TTL_REQUIRED_CTORS = {'StartProcess', 'Phase', 'LifeCyclePhase'}
TTL_KW_NAMES = {'ttl', 'ttlSeconds', 'ttl_seconds', 'timeout', 'timeout_seconds', 'timeoutSeconds'}
PROCESS_LIKE_NAMES = {'process', 'proc', 'worker', 'p', 'child', 'sp', 'server_process', 'relay_process', 'watchdog_process'}
LONG_SLEEP_THRESHOLD = 1.0
BLOCKING_METHODS = {'wait', 'join', 'communicate'}
BLOCKING_MAIN_THREAD_METHODS = {'communicate'}
BLOCKING_MAIN_THREAD_FUNCS = {
    'subprocess.run', 'subprocess.call', 'subprocess.check_call',
    'subprocess.check_output', 'subprocess.getoutput', 'subprocess.getstatusoutput',
}
WORKER_THREAD_FUNCS = {'run', '_run', '_thread_run', 'worker_run', '_worker', '_thread', '_bg_run'}
WORKER_THREAD_CLASSES = {'QThread', 'Thread', 'WorkerThread', 'BackgroundThread', 'WaveformStreamLoader'}
OK_MARKERS = {
    'lifecycle-bypass-ok', 'noqa: lifecycle-bypass',
    'ttl-ok', 'pid-ok', 'sleep-ok', 'block-ok', 'phase-ownership-ok', 'phase-architecture-ok', 'main-thread-ok', 'thread-ok',
}
RULE_DESCRIPTIONS = {
    'DIRECT_SPAWN': 'Raw subprocess/thread/process call outside lifecycle wrapper',
    'NO_TTL': 'Phase/Process constructed without ttl/ttlSeconds/timeout_seconds',
    'NO_PID': '.start() called — no .pid capture in next 5 lines',
    'BARE_SLEEP': 'time.sleep() at module scope (blocks debugger parent)',
    'BLOCKING_NO_TIMEOUT': '.wait()/.join()/.communicate() called without timeout arg',
    'BLOCKING_MAIN_THREAD': '.communicate()/subprocess.run() on main Qt thread',
    'SYNTAX_ERROR': 'File could not be parsed',
    'READ_ERROR': 'File could not be read',
}


def dottedName(node: ast.AST) -> str: return _base_dotted_name(node)
def simpleName(node: ast.AST) -> str: name = dottedName(node); return name.split('.')[-1] if name else ''
def buildParentMap(tree: ast.AST) -> dict[int, ast.AST]: return build_parent_map(tree)
def enclosingFunction(node: ast.AST, parents: dict[int, ast.AST]) -> str: return enclosing_function_name(node, parents)
def enclosingClass(node: ast.AST, parents: dict[int, ast.AST]) -> str: return enclosing_class_name(node, parents)


def lineHasOk(lines: list[str], lineno: int) -> bool: return has_ok_marker(lines, lineno, OK_MARKERS)


def literalFloat(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def _scan(path: Path, lines: list[str], tree: ast.AST) -> list[tuple[int, str, str]]:
    parents = buildParentMap(tree)
    rows: list[tuple[int, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if lineHasOk(lines, lineno):
            continue
        func_name = enclosingFunction(node, parents)
        class_name = enclosingClass(node, parents)
        sample = lines[lineno - 1].strip()[:200] if 0 < lineno <= len(lines) else ''
        context = f'{class_name}.{func_name}' if class_name else func_name

        called = dottedName(node.func)
        rule = BLOCKED_SPAWN.get(called)
        if rule:
            if func_name not in SPAWN_ALLOWLIST_FUNCS and class_name not in SPAWN_ALLOWLIST_CLASSES:
                rows.append((lineno, rule, f'{context}: {sample}'))

        ctor_name = simpleName(node.func)
        if ctor_name in TTL_REQUIRED_CTORS:
            kw_names = {kw.arg for kw in node.keywords if kw.arg}
            if not kw_names.intersection(TTL_KW_NAMES):
                rows.append((lineno, 'NO_TTL', f'{context}: {ctor_name}() missing ttl/ttlSeconds/timeout_seconds — {sample}'))

        if (isinstance(node.func, ast.Attribute) and node.func.attr == 'start'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id.lower() in PROCESS_LIKE_NAMES):
            window_start = max(0, lineno - 1)
            window_end = min(len(lines), lineno + 5)
            window_text = '\n'.join(lines[window_start:window_end])
            if '.pid' not in window_text and 'pid=' not in window_text:
                rows.append((lineno, 'NO_PID', f'{context}: {node.func.value.id}.start() — no .pid capture nearby — {sample}'))

        if called in ('time.sleep', 'sleep') and func_name == '<module>':
            rows.append((lineno, 'BARE_SLEEP', f'<module>: {sample}'))
        elif called in ('time.sleep',) and node.args:
            secs = literalFloat(node.args[0])
            if secs is not None and secs >= LONG_SLEEP_THRESHOLD and func_name == '<module>':
                rows.append((lineno, 'BARE_SLEEP', f'<module> sleep({secs}s): {sample}'))

        if isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKING_METHODS:
            kw_names = {kw.arg for kw in node.keywords if kw.arg}
            has_timeout_kw = bool(kw_names.intersection({'timeout', 'ttl', 'deadline'}))
            has_timeout_pos = len(node.args) > 0
            if not has_timeout_kw and not has_timeout_pos:
                rows.append((lineno, 'BLOCKING_NO_TIMEOUT', f'{context}: .{node.func.attr}() called without timeout — {sample}'))

        is_communicate_attr = isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKING_MAIN_THREAD_METHODS
        is_blocking_spawn = called in BLOCKING_MAIN_THREAD_FUNCS
        if is_communicate_attr or is_blocking_spawn:
            in_worker = (
                func_name in WORKER_THREAD_FUNCS
                or class_name in WORKER_THREAD_CLASSES
                or class_name in SPAWN_ALLOWLIST_CLASSES
                or func_name in SPAWN_ALLOWLIST_FUNCS
            )
            if not in_worker:
                method_label = f'.{node.func.attr}()' if is_communicate_attr else called + '()'
                rows.append((lineno, 'BLOCKING_MAIN_THREAD', f'{context}: {method_label} blocks Qt event loop — {sample}'))

    return rows


class LifecycleBypassDetector(Detector):
    NAME = 'lifecyclebypass'
    VERSION = '2.0.0'
    REPORT_HEADER = 'LIFECYCLE BYPASS DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/lifecyclebypass.txt'

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        if path.name == 'lifecyclebypassdetector.py':
            return []
        try:
            rel_parts = path.relative_to(root).parts
        except Exception:
            rel_parts = path.parts
        # Framework/documentation helper scripts are CLI tooling, not the Qt app
        # or FlatLine parent runtime. They are audited by file-io/bad-code/depcheck
        # instead of the lifecycle process-boundary rule.
        if rel_parts and rel_parts[0] in {'tools', 'handbook'}:
            return []
        findings = []
        for lineno, rule, message in _scan(path, lines, tree):
            findings.append(Finding(path, lineno, 0, 'HIGH', rule, message, ''))
        return findings

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        by_rule: dict[str, list[Finding]] = collections.defaultdict(list)
        for f in findings:
            by_rule[f.rule].append(f)

        lines_out = [
            self.REPORT_HEADER,
            '=' * len(self.REPORT_HEADER),
            f'Version: {self.VERSION}',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Findings: {len(findings)}',
            '',
        ]

        if findings:
            lines_out.append('Summary by rule:')
            for rule in sorted(by_rule):
                desc = RULE_DESCRIPTIONS.get(rule, rule)
                lines_out.append(f'  {rule} ({len(by_rule[rule])}): {desc}')
            lines_out.append('')
            for rule in sorted(by_rule):
                lines_out.append(f'--- {rule} ---')
                for f in sorted(by_rule[rule], key=lambda x: (str(x.path), x.line)):
                    lines_out.append(f'  {f.render(root)}')
                lines_out.append('')
            lines_out += [
                'Suppression markers:',
                '  # lifecycle-bypass-ok  # ttl-ok  # pid-ok  # sleep-ok  # block-ok  # main-thread-ok',
            ]
        else:
            lines_out.append('No lifecycle bypass findings.')

        return '\n'.join(lines_out) + '\n'


if __name__ == '__main__':
    LifecycleBypassDetector.main()
