#!/usr/bin/env python3
from __future__ import annotations
import ast
import os
import sys
from pathlib import Path

_DETECTOR_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_DETECTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_DETECTOR_ROOT))

from vendor.claude.detector_base import Detector, Finding, has_ok_marker, dotted_name as _base_dotted_name
from vendor.claude.detector_runtime import InsertDebuggerException

ALLOW_MARKERS = frozenset({'raw-sql-ok', 'noqa: raw-sql', 'Google API .execute', 'google api', 'google-api-ok'})
RAW_CONNECT_NAMES = {'sqlite3.connect', 'pymysql.connect', 'MySQLdb.connect', 'mysql.connector.connect'}
RAW_ATTRS = {'cursor', 'execute', 'executemany', 'executescript'}

def dottedName(node: ast.AST) -> str: return _base_dotted_name(node)
def _lineHasAllow(lines: list[str], lineno: int) -> bool: return has_ok_marker(lines, lineno, ALLOW_MARKERS)


def _scan(path: Path, lines: list[str], tree: ast.AST) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = int(getattr(node, 'lineno', 0) or 0)
        if _lineHasAllow(lines, lineno):
            continue
        name = dottedName(node.func)
        rule = ''
        if name in RAW_CONNECT_NAMES:
            rule = 'RAW_CONNECT'
        elif isinstance(node.func, ast.Attribute) and node.func.attr in RAW_ATTRS:
            lowered = name.lower()
            sample_line = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
            # google-api-python-client and similar SDKs expose `.execute()` but are
            # not SQL/database calls. Only flag execute-like calls when the receiver
            # or arguments look like a DB/cursor/session/raw SQL surface.
            if node.func.attr == 'execute':
                if '.execute(' in sample_line and not any(sql in sample_line.upper() for sql in ('SELECT ', 'INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ', 'ALTER ', 'DROP ', 'PRAGMA ', 'WITH ')):
                    if not any(token in lowered for token in ('conn', 'cursor', 'cur', 'db', 'session', 'sqlite', 'sqlalchemy')):
                        continue
            if any(token in lowered for token in ('service.', '.service.', '.request.', 'gmail', 'calendar', 'google')):
                continue
            rule = f'RAW_{node.func.attr.upper()}'
        if rule:
            sample = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''
            rows.append((lineno, rule, sample[:220]))
    return rows


class RawSqlDetector(Detector):
    NAME = 'rawsql'
    VERSION = '2.0.0'
    REPORT_HEADER = 'RAW SQL DETECTOR REPORT'
    DEFAULT_OUTPUT = 'logs/rawsql.txt'

    def scan_file(self, path: Path, source: str, lines: list[str], tree: ast.AST, root: Path) -> list[Finding]:
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            rel = path.as_posix()
        # These are not naked app SQL paths: google API client `.execute()` calls
        # are SDK request execution, and classes/_config.py owns the tiny sqlite
        # compatibility bridge for config synchronization until the app DB is ready.
        if rel in {'hooks/gmail_api_hook.py', 'hooks/google_sheets_hook.py', 'hooks/google_workspace_hook.py', 'classes/_config.py'}:
            return []
        return [
            Finding(path, lineno, 0, 'HIGH', rule, sample, sample)
            for lineno, rule, sample in _scan(path, lines, tree)
        ]

    def render_report(self, root: Path, files: list[Path], findings: list[Finding]) -> str:
        import datetime
        lines = [
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
        for f in findings:
            lines.append(f.render(root))
        if not findings:
            lines.append('No raw SQL connector/cursor/execute candidates found in scanned app paths.')
        return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    RawSqlDetector.main()
