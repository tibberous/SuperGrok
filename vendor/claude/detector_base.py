#!/usr/bin/env python3
"""Base class for all vendor/claude detectors.

Centralises:
  - sys.path bootstrap
  - SKIP_DIR_NAMES
  - iter_py() spider
  - argparse wiring (--root, --output, positional seeds)
  - 8-worker parallel file scanning
  - report writing + flush/exit

Usage
-----
class MyDetector(Detector):
    NAME = 'my-detector'
    VERSION = '1.0.0'
    REPORT_HEADER = 'MY DETECTOR REPORT'

    def scan_file(self, path: Path, lines: list[str], tree: ast.AST) -> list[Finding]:
        ...  # return list of Finding

if __name__ == '__main__':
    MyDetector.main()
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import datetime
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_runtime import InsertDebuggerException, tracedReadText, tracedWriteText  # noqa: E402

def discover_project_root(anchor: Path | None = None) -> Path:
    """Walk up from *anchor* (default: this file) to find the dir holding start.py."""
    start = (anchor or Path(__file__)).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / 'start.py').exists():
            return candidate
    # Fallback: two levels above vendor/claude/
    return Path(__file__).resolve().parent.parent.parent


SKIP_DIR_NAMES: frozenset[str] = frozenset({
    '.git', '__pycache__', '.mypy_cache', '.ruff_cache', 'node_modules', 'vendor',
})

WORKERS = 8


# ---------------------------------------------------------------------------
# Shared finding type
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """Single detector finding.  severity is one of HIGH / MEDIUM / LOW / INFO."""
    path: Path
    line: int
    column: int = 0
    severity: str = 'HIGH'
    rule: str = ''
    message: str = ''
    source: str = ''
    # Optional verbose guidance for ChatGPT-style reports
    fix_hint: str = ''
    sample_code: str = ''

    def render(self, root: Path) -> str:
        try:
            rel = self.path.relative_to(root)
        except ValueError:
            InsertDebuggerException('detector_base.Finding.render', 'relative_to failed', str(self.path))
            rel = self.path
        base = f'{rel}:{self.line}:{self.column}: {self.severity} {self.rule} {self.message}'
        if self.source.strip():
            base += f' :: {self.source.strip()}'
        return base


# ---------------------------------------------------------------------------
# Shared AST / line helpers
# ---------------------------------------------------------------------------

def sample_line(lines: list[str], lineno: int, max_len: int = 120) -> str:
    """Return the stripped source line at *lineno* (1-based), truncated to *max_len*."""
    return lines[lineno - 1].strip()[:max_len] if 0 < lineno <= len(lines) else ''


def has_ok_marker(lines: list[str], lineno: int, markers: frozenset[str] | set[str]) -> bool:
    """Return True if any of *markers* appears in a 3-line window around *lineno* (1-based)."""
    snippet = '\n'.join(lines[max(0, lineno - 2):min(len(lines), lineno + 1)])
    return any(m in snippet for m in markers)


def build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Return a mapping from id(node) → parent node for every node in *tree*."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def enclosing_function_name(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    """Walk up *parents* from *node* and return the name of the nearest enclosing function."""
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return '<module>'


def enclosing_class_name(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    """Walk up *parents* from *node* and return the name of the nearest enclosing class."""
    cur = node
    while id(cur) in parents:
        cur = parents[id(cur)]
        if isinstance(cur, ast.ClassDef):
            return cur.name
    return ''


def dotted_name(node: ast.AST) -> str:
    """Resolve a chain of ast.Attribute / ast.Name nodes to a dotted string."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return '.'.join(reversed(parts))


# ---------------------------------------------------------------------------
# Spider
# ---------------------------------------------------------------------------

def iter_py(paths: Iterable[Path], root: Path) -> list[Path]:
    """Recursively collect .py files from *paths*, skipping SKIP_DIR_NAMES."""
    seen: set[Path] = set()
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        if p.is_file() and p.suffix == '.py':
            candidates = [p]
        elif p.is_dir():
            candidates = list(p.rglob('*.py'))
        else:
            candidates = []
        for candidate in candidates:
            child = candidate.resolve()
            if child in seen:
                continue
            try:
                parts = child.relative_to(root).parts
            except ValueError:
                InsertDebuggerException('detector_base.iter_py', 'relative_to failed', str(child))
                continue
            if any(part in SKIP_DIR_NAMES for part in parts):
                continue
            seen.add(child)
            out.append(child)
    return out


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Detector:
    """Abstract base for all vendor/claude detectors.

    Subclasses MUST define NAME, VERSION, REPORT_HEADER, and scan_file().
    """
    NAME: str = 'detector'
    VERSION: str = '1.0.0'
    REPORT_HEADER: str = 'DETECTOR REPORT'
    DEFAULT_OUTPUT: str = 'logs/detector.txt'

    # Override to True to include verbose HOW TO FIX / SAMPLE CODE blocks
    VERBOSE: bool = False

    # -----------------------------------------------------------------------
    # Subclass API
    # -----------------------------------------------------------------------

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        """Scan a single parsed file. Return findings (empty list = clean)."""
        raise NotImplementedError

    # -----------------------------------------------------------------------
    # Spider + parallel runner
    # -----------------------------------------------------------------------

    def collect_files(self, seeds: list[Path], root: Path) -> list[Path]:
        return iter_py(seeds, root)

    def _scan_one(self, path: Path, root: Path) -> list[Finding]:
        try:
            source = tracedReadText(path, encoding='utf-8', errors='replace')
            lines = source.splitlines()
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                InsertDebuggerException(f'{self.NAME}.parse', exc, str(path))
                return []
            return self.scan_file(path, source, lines, tree, root)
        except OSError as exc:
            InsertDebuggerException(f'{self.NAME}.read', exc, str(path))
            return []
        except Exception as exc:
            InsertDebuggerException(f'{self.NAME}.scan', exc, str(path))
            return []

    def run_parallel(self, files: list[Path], root: Path) -> list[Finding]:
        """Scan *files* using up to WORKERS threads, return merged findings."""
        all_findings: list[Finding] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(self._scan_one, f, root): f for f in files}
            for future in concurrent.futures.as_completed(futures):
                try:
                    all_findings.extend(future.result())
                except Exception as exc:
                    InsertDebuggerException(f'{self.NAME}.future', exc, str(futures[future]))
        all_findings.sort(key=lambda f: (str(f.path), f.line, f.column))
        return all_findings

    # -----------------------------------------------------------------------
    # Report rendering
    # -----------------------------------------------------------------------

    def severity_counts(self, findings: list[Finding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def rule_counts(self, findings: list[Finding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.rule] = counts.get(f.rule, 0) + 1
        return counts

    def render_report_header(self, root: Path, files: list[Path], findings: list[Finding],
                              extra_lines: list[str] | None = None) -> list[str]:
        """Return the standard report header lines. Subclasses can append to the result."""
        sep = '=' * len(self.REPORT_HEADER)
        sc = self.severity_counts(findings)
        rc = self.rule_counts(findings)
        lines = [
            self.REPORT_HEADER,
            sep,
            f'Version: {self.VERSION}',
            '',
            f'Generated at: {datetime.datetime.now().isoformat(timespec="seconds")}',
            f'Root: {root}',
            f'Files scanned: {len(files)}',
            f'Findings: {len(findings)}',
            f'Severity counts: {", ".join(f"{k}={v}" for k, v in sorted(sc.items())) or "<none>"}',
            f'Rule counts: {", ".join(f"{k}={v}" for k, v in sorted(rc.items())) or "<none>"}',
            '',
        ]
        if extra_lines:
            lines.extend(extra_lines)
        return lines

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        lines = self.render_report_header(root, files, findings)
        lines.extend(self.render_rules())
        lines.append('')
        lines.append('Findings:')
        if not findings:
            lines.append(f'No {self.NAME} candidates found in scanned app paths.')
        else:
            for f in findings:
                lines.append(f.render(root))
                if self.VERBOSE and f.fix_hint:
                    lines.append(f'  HOW TO FIX: {f.fix_hint}')
                if self.VERBOSE and f.sample_code:
                    lines.append(f'  SAMPLE CODE:\n{f.sample_code}')
        return '\n'.join(lines) + '\n'

    def render_rules(self) -> list[str]:
        """Override to add a rules legend to the report header."""
        return []

    # -----------------------------------------------------------------------
    # Entry point
    # -----------------------------------------------------------------------

    def _exit_code(self, findings: list[Finding]) -> int:
        return 1 if any(f.severity in {'ERROR', 'HIGH'} for f in findings) else 0

    def _run(self, argv: Sequence[str] | None = None) -> int:
        discovered = discover_project_root()
        ap = argparse.ArgumentParser(description=self.REPORT_HEADER)
        ap.add_argument('--root', default=str(discovered))
        ap.add_argument('--output', default=self.DEFAULT_OUTPUT)
        ap.add_argument('paths', nargs='*')
        ns = ap.parse_args(list(argv) if argv is not None else None)

        root = Path(ns.root).resolve()
        # Default seeds: start.py + classes/ so we always cover the full project
        if ns.paths:
            seeds = [Path(x).resolve() for x in ns.paths]
        else:
            default_seeds = [root / 'start.py', root / 'classes', root / 'data.py', root / 'trio.py']
            seeds = [p for p in default_seeds if p.exists()] or [root]
        files = self.collect_files(seeds, root)
        findings = self.run_parallel(files, root)
        report = self.render_report(root, files, findings)

        out = Path(ns.output)
        if not out.is_absolute():
            out = root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        tracedWriteText(out, report, encoding='utf-8')
        print(report)
        return self._exit_code(findings)

    @classmethod
    def main(cls, argv: Sequence[str] | None = None) -> None:
        instance = cls()
        code = instance._run(argv)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(int(code))
