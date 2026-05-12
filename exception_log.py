# ============================================================================
#  SuperGrok Bridge — exception log
#  ---------------------------------------------------------------------------
#  Lightweight JSONL exception sink used across the app. Captures stack
#  traces with contextual metadata for postmortem analysis.
#
#  Author : Trenton Tompkins  <trentontompkins@gmail.com>
#  Phone  : 724-431-5207
#  GitHub : https://github.com/tibberous/SuperGrok
#
#  Need help on your next project?
#  Call me at 724-431-5207 for a free consultation!
#
#  Codex by Claude Opus 4.7 and ChatGPT 5.5.
# ============================================================================
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
EXCEPTION_DB = DATA / "supergrok_bridge_exceptions.sqlite3"


class ExceptionDatabase:
    def __init__(self, path: Path = EXCEPTION_DB) -> None:
        from sqlalchemy import Column, Float, Integer, String, Text, create_engine  # depcheck-ok
        from sqlalchemy.orm import declarative_base, sessionmaker

        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path  # noqa: nonconform
        self.engine = create_engine(f"sqlite:///{path}", future=True)  # noqa: nonconform
        self.Base = declarative_base()  # noqa: nonconform

        class ExceptionRecord(self.Base):  # type: ignore[misc, valid-type]
            __tablename__ = "exceptions"

            id = Column(Integer, primary_key=True, autoincrement=True)
            created_at = Column(Float, nullable=False, index=True)
            created_iso = Column(String(32), nullable=False)
            severity = Column(String(32), nullable=False, default="ERROR", index=True)
            context = Column(String(255), nullable=False, index=True)
            exception_type = Column(String(255), nullable=False)
            message = Column(Text, nullable=False)
            traceback_text = Column(Text, nullable=False)
            extra_json = Column(Text, nullable=False, default="{}")

        self.Record = ExceptionRecord  # noqa: nonconform
        self.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def record(self, context: str, error: BaseException, severity: str = "ERROR", extra: dict[str, Any] | None = None) -> int:  # noqa: nonconform
        now = time.time()
        record = self.Record(
            created_at=now,
            created_iso=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            severity=str(severity or "ERROR"),
            context=str(context or "unknown")[:255],
            exception_type=type(error).__name__,
            message=str(error),
            traceback_text="".join(traceback.format_exception(type(error), error, error.__traceback__)),
            extra_json=json.dumps(extra or {}, ensure_ascii=False, default=str),
        )
        with self.Session() as session:
            session.add(record)
            session.commit()
            return int(record.id)

    def latest(self, limit: int = 200) -> list[dict[str, Any]]:  # noqa: nonconform
        with self.Session() as session:
            rows = (
                session.query(self.Record)
                .order_by(self.Record.created_at.desc())
                .limit(int(limit))
                .all()
            )
            return [
                {
                    "id": int(row.id),
                    "createdIso": str(row.created_iso),
                    "severity": str(row.severity),
                    "context": str(row.context),
                    "exceptionType": str(row.exception_type),
                    "message": str(row.message),
                    "traceback": str(row.traceback_text),
                    "extra": json.loads(str(row.extra_json or "{}")),
                }
                for row in rows
            ]

    def close(self) -> None:
        self.engine.dispose()


_EXCEPTION_DATABASE: ExceptionDatabase | None = None


def getExceptionDatabase() -> ExceptionDatabase:
    global _EXCEPTION_DATABASE
    if _EXCEPTION_DATABASE is None:
        _EXCEPTION_DATABASE = ExceptionDatabase()
    return _EXCEPTION_DATABASE


def recordException(context: str, error: BaseException, severity: str = "ERROR", extra: dict[str, Any] | None = None) -> int:
    return getExceptionDatabase().record(context=context, error=error, severity=severity, extra=extra)
