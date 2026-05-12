#!/usr/bin/env python3
"""Shared runtime surfaces for the Claude detector bundle.

Coded by ChatGPT 5.4 Thinking.
"""
from __future__ import annotations

import contextlib
import datetime
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import IO, Any, Iterator

APP_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOG_DIR = APP_ROOT / "logs"
DEFAULT_EXCEPTION_DB = DEFAULT_LOG_DIR / "debugger_exceptions.sqlite3"


def _exceptionDbPath() -> Path:
    raw = os.environ.get("DETECTOR_EXCEPTION_DB", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_EXCEPTION_DB


def _ensureExceptionTable(dbPath: Path) -> None:
    dbPath.parent.mkdir(parents=True, exist_ok=True)  # file-io-ok
    conn = sqlite3.connect(str(dbPath))  # raw-sql-ok
    try:
        conn.execute(  # raw-sql-ok
            """
            CREATE TABLE IF NOT EXISTS debugger_exceptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                context TEXT NOT NULL,
                exc_type TEXT NOT NULL,
                exc_message TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def InsertDebuggerException(
    source: str,
    exc: BaseException | str,
    context: str = "",
    dbPath: Path | None = None,
) -> None:
    """Persist an exception/fault where startup/debugger code can read it."""
    path = dbPath or _exceptionDbPath()
    try:
        _ensureExceptionTable(path)
        if isinstance(exc, BaseException):
            excType = type(exc).__name__
            excMessage = str(exc)
        else:
            excType = "Message"
            excMessage = str(exc)
        conn = sqlite3.connect(str(path))  # raw-sql-ok
        try:
            conn.execute(  # raw-sql-ok
                """
                INSERT INTO debugger_exceptions
                    (created_at, source, context, exc_type, exc_message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    source,
                    context,
                    excType,
                    excMessage,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as fallbackError:  # swallow-ok: DB fault surface failed; stderr is last resort.
        print(
            f"[DetectorRuntime:ERROR] failed to store debugger exception: {fallbackError}",
            file=sys.stderr,
        )


def readDebuggerExceptions(dbPath: Path | None = None) -> list[dict[str, str]]:
    """Read persisted detector exceptions for start.py/debugger display."""
    path = dbPath or _exceptionDbPath()
    if not path.exists():
        return []
    _ensureExceptionTable(path)
    conn = sqlite3.connect(str(path))  # raw-sql-ok
    try:
        rows = conn.execute(  # raw-sql-ok
            """
            SELECT created_at, source, context, exc_type, exc_message
            FROM debugger_exceptions
            ORDER BY id DESC
            LIMIT 200
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "created_at": str(createdAt),
            "source": str(source),
            "context": str(context),
            "exc_type": str(excType),
            "exc_message": str(excMessage),
        }
        for createdAt, source, context, excType, excMessage in rows
    ]


def tracedReadText(path: str | Path, encoding: str = "utf-8", errors: str = "replace") -> str:
    try:
        return Path(path).read_text(encoding=encoding, errors=errors)  # file-io-ok
    except Exception as exc:
        InsertDebuggerException("tracedReadText", exc, str(path))
        raise


def tracedWriteText(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)  # file-io-ok
        target.write_text(text, encoding=encoding)  # file-io-ok
    except Exception as exc:
        InsertDebuggerException("tracedWriteText", exc, str(path))
        raise


@contextlib.contextmanager
def tracedOpen(path: str | Path, mode: str = "r", **kwargs: Any) -> Iterator[IO[Any]]:
    try:
        with open(path, mode, **kwargs) as handle:  # file-io-ok
            yield handle
    except Exception as exc:
        InsertDebuggerException("tracedOpen", exc, f"{path} mode={mode}")
        raise


def tracedCopy2(source: str | Path, target: str | Path) -> Path:
    try:
        result = shutil.copy2(str(source), str(target))  # file-io-ok
        return Path(result)
    except Exception as exc:
        InsertDebuggerException("tracedCopy2", exc, f"{source} -> {target}")
        raise


def launcherRunCommand(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    text: bool = True,
    capture_output: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Lifecycle-owned subprocess.run wrapper for detector/debugger routes."""
    try:
        return subprocess.run(  # lifecycle-bypass-ok phase-ownership-ok
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            text=text,
            capture_output=capture_output,
            timeout=timeout,
        )
    except Exception as exc:
        InsertDebuggerException("launcherRunCommand", exc, " ".join(cmd))
        raise
