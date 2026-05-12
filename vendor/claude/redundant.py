#!/usr/bin/env python3
"""Detects runs of consecutive near-identical lines that should be a loop."""
from __future__ import annotations
import ast
import datetime
import os
import re
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding
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

_SKIP_SHAPE_PREFIXES = ('import <ID>', 'from <ID> import')


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
    if any(shape.startswith(p) for p in _SKIP_SHAPE_PREFIXES):
        return False
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


def _scan_source(path: Path, source: str, min_run: int = MIN_RUN) -> list[tuple[int, int, int, str, str, str]]:
    """Return list of (start_line, end_line, match_count, func, shape, sample) for each run."""
    if path.name in {'vulture_whitelist.py', 'data.py'}:
        return []
    raw_lines = source.splitlines()
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


def _safe_print(text: str) -> None:
    enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
    try:
        print(text)
    except UnicodeEncodeError:
        InsertDebuggerException("redundant.py:safe_print", "handled exception")
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))


class RedundantDetector(Detector):
    NAME = 'redundant'
    VERSION = '2.0.0'
    REPORT_HEADER = 'REDUNDANT CODE DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/redundant.txt'

    def __init__(self, min_run: int = MIN_RUN):
        self.min_run = min_run

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        findings = []
        for start, end, count, func, shape, sample in _scan_source(path, source, self.min_run):
            msg = f'({count} matching lines) [{func}] shape: {shape}'
            findings.append(Finding(path, start, 0, 'MEDIUM', 'REDUNDANT_RUN', msg, sample))
        return findings

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        lines_out = [
            self.REPORT_HEADER,
            '=' * len(self.REPORT_HEADER),
            f'Version: {self.VERSION}',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Min run length: {self.min_run}',
            f'Findings: {len(findings)}',
            '',
        ]
        # Sort worst offenders first (count is embedded in message — parse it)
        def count_key(f: Finding) -> int:
            try:
                return -int(f.message.split('(')[1].split(' ')[0])
            except Exception:
                return 0
        for f in sorted(findings, key=count_key):
            lines_out.append(f.render(root))
            if f.source:
                lines_out.append(f'  first: {f.source}')
            lines_out.append('')
        if not findings:
            lines_out.append('No repetitive line runs found.')
        return '\n'.join(lines_out) + '\n'

    def _run(self, argv=None):
        import argparse
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default='.')
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('--min-run', type=int, default=MIN_RUN)
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)
        self.min_run = ns.min_run

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
        _safe_print(report)
        return self._exit_code(findings)


if __name__ == '__main__':
    RedundantDetector.main()
