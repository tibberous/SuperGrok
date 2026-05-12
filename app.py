# ============================================================================
#  SuperGrok Bridge — Qt application
#  ---------------------------------------------------------------------------
#  Main split-pane bridge window: debug column | chat column | live web pane.
#  Hosts grok.com / chatgpt.com / gemini.google.com / claude.ai inside Qt
#  WebEngine with a persistent per-provider cookie profile.
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

import builtins
import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from exception_log import getExceptionDatabase, recordException

from PySide6.QtCore import QObject, QPoint, QProcess, QTimer, QUrl, Qt, Slot, Signal  # depcheck-ok
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import (  # depcheck-ok
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import (  # depcheck-ok
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView  # depcheck-ok
from PySide6.QtWebChannel import QWebChannel  # depcheck-ok
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket  # depcheck-ok
try:
    from PySide6.QtTest import QTest  # depcheck-ok
except Exception:  # swallow-ok
    QTest = None

from sqlalchemy import Column, Float, Integer, String, Text, create_engine, or_  # depcheck-ok
from sqlalchemy.orm import declarative_base, sessionmaker  # depcheck-ok

HAS_SQLALCHEMY = True


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
PRISM = ASSETS / "prism"
JQUERY = ASSETS / "jquery" / "jquery.min.js"
BRIDGE_JS = ASSETS / "js" / "grok_dom_bridge.js"
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
DEBUG_LOG = ROOT / "debug.log"
SESSION_LOG = ROOT / "session.log"
REQUEST_DB = DATA / "supergrok_bridge.sqlite3"
POLICY_DB = DATA / "toolcall_policy.sqlite3"
UI_STATE_DB = DATA / "supergrok_bridge_ui.sqlite3"
PROCESS_DB = DATA / "supergrok_bridge_processes.sqlite3"
EXCEPTION_DB = DATA / "supergrok_bridge_exceptions.sqlite3"
DEBUGGER_DB = DATA / "supergrok_bridge_debugger.sqlite3"
TRAFFIC_LOG = DATA / "traffic.log"
PROCESS_DEFAULT_TTL_SECONDS = 30
COMMON_COMMANDS = ROOT / "common_commands.txt"
RETURNED_ATTACHMENTS = DATA / "received_attachments"
DEBUGGER_HEARTBEAT_INTERVAL_MS = 1000
DEBUGGER_SURFACES = (
    "heartbeat",
    "poll",
    "vardump",
    "accepts-proxy",
    "bridge-service",
    "chat",
)
SOURCE_SIGNATURE_GLOBS = (
    "start.py",
    "supergrok_bridge/**/*.py",
    "common_commands.txt",
)

CHATGPT_TARGET_ALIASES = {"chatgpt", "chatgtp", "gtp", "gpt", "openai"}
GROK_TARGET_ALIASES = {"grok", "supergrok", "xai"}
GEMINI_TARGET_ALIASES = {"gemini", "gem", "gem-bridge", "gembridge", "google", "googleai", "bard"}
CLAUDE_TARGET_ALIASES = {"claude", "anthropic", "claudeai", "claude-bridge", "claudebridge", "cl"}


def normalizeChatTarget(value: object = "") -> str:
    target = str(value or "").strip().lower().replace("_", "-")
    if target in CHATGPT_TARGET_ALIASES:
        return "chatgpt"
    if target in GROK_TARGET_ALIASES:
        return "grok"
    if target in GEMINI_TARGET_ALIASES:
        return "gemini"
    if target in CLAUDE_TARGET_ALIASES:
        return "claude"
    return target or "grok"


def chatTargetFromUrl(url: object = "") -> str:
    text = str(url or "").lower()
    if "chatgpt.com" in text or "chat.openai.com" in text:
        return "chatgpt"
    if "gemini.google.com" in text or "bard.google.com" in text:
        return "gemini"
    if "claude.ai" in text:
        return "claude"
    return "grok"


def chatProviderLabel(target: object = "") -> str:
    t = normalizeChatTarget(target)
    if t == "chatgpt":
        return "ChatGPT"
    if t == "gemini":
        return "Gemini"
    if t == "claude":
        return "Claude"
    return "Grok"


def chatProviderHomeUrl(target: object = "") -> str:
    t = normalizeChatTarget(target)
    if t == "chatgpt":
        return "https://chatgpt.com/"
    if t == "gemini":
        return "https://gemini.google.com/app"
    if t == "claude":
        return "https://claude.ai/new"
    return "https://grok.com/"


def sourceSignaturePaths() -> list[Path]:
    paths: list[Path] = []
    for pattern in SOURCE_SIGNATURE_GLOBS:
        for candidate in ROOT.glob(pattern):
            if not candidate.is_file():
                continue
            if "__pycache__" in candidate.parts:
                continue
            resolved = candidate.resolve()
            if resolved not in paths:
                paths.append(resolved)
    return sorted(paths, key=lambda item: str(item).lower())


def buildSourceSignature() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    for path in sourceSignaturePaths():
        try:
            data = path.read_bytes()
            stat = path.stat()
            rel = path.relative_to(ROOT).as_posix()
            file_sha = hashlib.sha256(data).hexdigest()
            row = {
                "path": rel,
                "size": int(stat.st_size),
                "mtimeNs": int(stat.st_mtime_ns),
                "sha256": file_sha,
            }
        except Exception as error:  # swallow-ok
            row = {"path": str(path), "error": f"{type(error).__name__}: {error}"}
        rows.append(row)
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return {
        "schema": 1,
        "root": str(ROOT.resolve()),
        "signature": digest.hexdigest(),
        "files": rows,
        "capturedAt": time.time(),
        "pid": int(os.getpid() or 0),
    }


BRIDGE_LOADED_SOURCE_SIGNATURE = buildSourceSignature()
REQUEST_LIST_LIMIT = 100
NETWORK_EVENT_PREFIX = "__SUPERGROK_NETWORK_EVENT__"
CHAT_MODAL_EVENT_PREFIX = "__SUPERGROK_CHAT_MODAL__"
BRIDGE_SERVICE_HOST = "127.0.0.1"
BRIDGE_SERVICE_PORT = int(os.environ.get("SUPERGROK_BRIDGE_PORT", "8767") or "8767")



_ORIGINAL_PRINT = getattr(builtins, "print")


def _appendTextLog(path: Path, text: str) -> None:
    """Append text to a root log without using print or recursively failing."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as handle:  # file-io-ok: project debug/session log.
            handle.write(text)
    except Exception:  # swallow-ok
        pass


def safeJson(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    except Exception as error:  # swallow-ok
        return json.dumps({"jsonError": f"{type(error).__name__}: {error}", "repr": repr(payload)}, ensure_ascii=False)


def debugLog(kind: str, text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _appendTextLog(DEBUG_LOG, f"[{stamp}] [{str(kind or 'debug').upper()}] {str(text or '')}\n")


def debugJson(kind: str, payload: dict[str, Any]) -> None:
    event = dict(payload or {})
    event.setdefault("loggedAt", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    debugLog(kind, safeJson(event))


def clearTextLog(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8", errors="replace")
    except Exception:  # swallow-ok
        pass


def clearRunLogs(reason: str = "bridge-app-start") -> None:
    if os.environ.get("SUPERGROK_KEEP_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    for path in (DEBUG_LOG, SESSION_LOG, TRAFFIC_LOG):
        clearTextLog(path)
    debugJson("log-reset", {"eventType": "log-reset", "reason": reason, "root": str(ROOT), "pid": os.getpid()})


def warnLog(text: str) -> None:
    debugLog("warn", text)
    try:
        _ORIGINAL_PRINT(f"[WARN:supergrok] {text}", file=sys.stderr, flush=True)
    except Exception:  # swallow-ok
        pass


def sessionLog(payload: dict[str, Any]) -> None:
    """Append one structured session event with full request/response content."""
    event = dict(payload or {})
    event.setdefault("loggedAt", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    _appendTextLog(SESSION_LOG, safeJson(event) + "\n")


def _installPrintTee() -> None:
    if bool(getattr(builtins, "_supergrok_print_tee_installed", False)):
        return

    def teePrint(*args: Any, **kwargs: Any) -> None:
        fileObj = kwargs.get("file", sys.stdout)
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        _ORIGINAL_PRINT(*args, **kwargs)
        if fileObj in (None, sys.stdout, sys.stderr):
            try:
                rendered = str(sep).join(str(arg) for arg in args) + str(end)
                _appendTextLog(DEBUG_LOG, rendered)
            except Exception:  # swallow-ok
                pass

    setattr(builtins, "_supergrok_print_tee_installed", True)  # nopatch
    builtins.print = teePrint  # nopatch


_installPrintTee()
debugLog("startup", f"SuperGrok Bridge process pid={os.getpid()} root={ROOT}")


def loc(text: str) -> str:
    """Localization seam for user-visible strings.

    The app is still English-only, but UI text should pass through one
    callable so future language tables can replace it without another UI pass.
    """
    return text


def debuggerDatabasePath() -> Path:
    """Return the FlatLine-compatible SQLite DB used by launcher probes."""
    configured = os.environ.get("SUPERGROK_DEBUGGER_DB") or os.environ.get("TRIO_DEBUGGER_DB") or os.environ.get("FLATLINE_DEBUGGER_DB")
    return Path(configured).expanduser().resolve() if configured else DEBUGGER_DB


class DebuggerHeartbeatDatabase:
    """SQLAlchemy-backed heartbeat store for FlatLine surface probes."""

    def __init__(self, path: Path | None = None) -> None:
        dbPath = path or debuggerDatabasePath()
        dbPath.parent.mkdir(parents=True, exist_ok=True)
        self.path = dbPath
        self.engine = create_engine(f"sqlite:///{dbPath}", future=True)  # noqa: nonconform
        self.Base = declarative_base()  # noqa: nonconform

        class HeartbeatRecord(self.Base):  # type: ignore[misc, valid-type]
            __tablename__ = "heartbeat"

            id = Column(Integer, primary_key=True, autoincrement=True)
            created = Column(String(32), nullable=False, index=True)
            heartbeat_microtime = Column(Float, nullable=False, index=True)
            source = Column(String(128), nullable=False, default="SuperGrok Bridge")
            event_kind = Column(String(64), nullable=False, default="heartbeat", index=True)
            reason = Column(String(255), nullable=False, default="")
            caller = Column(String(255), nullable=False, default="")
            phase = Column(String(128), nullable=False, default="")  # noqa: redundant
            pid = Column(Integer, nullable=False, default=0, index=True)
            stack_trace = Column(Text, nullable=False, default="")
            var_dump = Column(Text, nullable=False, default="{}")
            process_snapshot = Column(Text, nullable=False, default="{}")  # noqa: redundant
            processed = Column(Integer, nullable=False, default=0, index=True)

        self.Record = HeartbeatRecord  # noqa: nonconform
        self.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def emit(  # noqa: nonconform
        self,
        *,
        eventKind: str = "heartbeat",
        reason: str = "",
        caller: str = "",
        phase: str = "",
        varDump: dict[str, Any] | None = None,
        processSnapshot: dict[str, Any] | None = None,
    ) -> int:
        now = time.time()
        row = self.Record(
            created=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            heartbeat_microtime=float(now),
            source="SuperGrok Bridge",
            event_kind=str(eventKind or "heartbeat"),
            reason=str(reason or ""),
            caller=str(caller or ""),
            phase=str(phase or ""),
            pid=int(os.getpid() or 0),
            stack_trace="".join(traceback.format_stack(limit=8)),
            var_dump=json.dumps(varDump or {}, ensure_ascii=False, default=str),
            process_snapshot=json.dumps(processSnapshot or {}, ensure_ascii=False, default=str),
            processed=0,
        )
        with self.Session() as session:
            session.add(row)
            session.commit()
            return int(row.id)

    def close(self) -> None:
        self.engine.dispose()


_DEBUGGER_HEARTBEAT_DATABASE: DebuggerHeartbeatDatabase | None = None


def getDebuggerHeartbeatDatabase() -> DebuggerHeartbeatDatabase:
    global _DEBUGGER_HEARTBEAT_DATABASE
    if _DEBUGGER_HEARTBEAT_DATABASE is None:
        _DEBUGGER_HEARTBEAT_DATABASE = DebuggerHeartbeatDatabase()
    return _DEBUGGER_HEARTBEAT_DATABASE


def ensureDebuggerHeartbeatSchema(path: Path | None = None) -> Path:
    """Create the minimal heartbeat/poll/vardump surface schema if needed."""
    database = DebuggerHeartbeatDatabase(path)
    dbPath = database.path
    database.close()
    return dbPath


def emitDebuggerHeartbeat(
    *,
    eventKind: str = "heartbeat",
    reason: str = "",
    caller: str = "",
    phase: str = "",
    varDump: dict[str, Any] | None = None,
    processSnapshot: dict[str, Any] | None = None,
) -> None:
    """Persist a debugger-visible heartbeat row without depending on Qt widgets."""
    try:
        getDebuggerHeartbeatDatabase().emit(
            eventKind=eventKind,
            reason=reason,
            caller=caller,
            phase=phase,
            varDump=varDump,
            processSnapshot=processSnapshot,
        )
    except Exception as error:
        recordException("supergrok_bridge/app.py:emitDebuggerHeartbeat", error, extra={"handler": "except Exception as error:"})


def tracedReadText(path: Path, *, encoding: str = "utf-8") -> str:
    writeTrafficLog({"eventType": "file-read-start", "path": str(path)})
    try:
        text = path.read_text(encoding=encoding)  # lifecycle-file-ok
        writeTrafficLog({"eventType": "file-read-complete", "path": str(path), "chars": len(text)})
        return text
    except Exception as error:
        recordException("supergrok_bridge/app.py:tracedReadText", error, extra={"path": str(path)})
        writeTrafficLog({"eventType": "file-read-failed", "path": str(path), "error": f"{type(error).__name__}: {error}"})
        raise


def tracedWriteText(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    writeTrafficLog({"eventType": "file-write-start", "path": str(path), "chars": len(text)})
    try:
        path.write_text(text, encoding=encoding)  # lifecycle-file-ok
        writeTrafficLog({"eventType": "file-write-complete", "path": str(path)})
    except Exception as error:
        recordException("supergrok_bridge/app.py:tracedWriteText", error, extra={"path": str(path)})
        writeTrafficLog({"eventType": "file-write-failed", "path": str(path), "error": f"{type(error).__name__}: {error}"})
        raise


def tracedAppendJsonLine(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    try:
        with path.open("a", encoding="utf-8") as handle:  # lifecycle-file-ok
            handle.write(line)
    except Exception as error:
        recordException("supergrok_bridge/app.py:tracedAppendJsonLine", error, extra={"path": str(path)})
        raise


def managedSubprocessRun(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a controlled child process through the lifecycle-owned subprocess seam."""
    writeTrafficLog({"eventType": "managed-subprocess-start", "args": args, "timeout": timeout})
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)  # lifecycle-bypass-ok
        writeTrafficLog({"eventType": "managed-subprocess-complete", "args": args, "returnCode": result.returncode})
        return result
    except Exception as error:
        recordException("supergrok_bridge/app.py:managedSubprocessRun", error, extra={"args": args, "timeout": timeout})
        writeTrafficLog({"eventType": "managed-subprocess-failed", "args": args, "error": f"{type(error).__name__}: {error}"})
        raise

@dataclass
class AppConfig:
    initialUrl: str
    target: str = "grok"
    debug: bool = False
    profileDir: str = ""
    remoteDebugPort: int = 9222
    processTtlSeconds: int = PROCESS_DEFAULT_TTL_SECONDS
    serviceMode: bool = False
    servicePort: int = BRIDGE_SERVICE_PORT
    hideWindow: bool = False
    windowMode: str = "visible"
    offscreenMode: str = "auto"


def writeTrafficLog(event: dict[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    payload = dict(event or {})
    payload.setdefault("loggedAt", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    try:
        text = safeJson(payload)
        tracedAppendJsonLine(TRAFFIC_LOG, payload)
        debugLog("traffic", text)
    except Exception as error:
        recordException("supergrok_bridge/app.py:writeTrafficLog", error, extra={"handler": "except Exception:", "payloadPreview": repr(payload)[:1000]})
        debugJson("traffic-log-error", {"eventType": "traffic-log-error", "error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc(), "payloadPreview": repr(payload)[:2000]})
        pass


def clearTrafficLog() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        tracedWriteText(TRAFFIC_LOG, "", encoding="utf-8")
    except Exception as error:
        recordException("supergrok_bridge/app.py:101", error, extra={"handler": "except Exception:"})
        pass


def loadCommonCommandRoots() -> set[str]:
    roots: set[str] = set()
    try:
        if COMMON_COMMANDS.exists():
            for raw in COMMON_COMMANDS.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                roots.add(Path(line).name.lower().removesuffix(".exe"))
    except Exception as error:
        recordException("supergrok_bridge/app.py:loadCommonCommandRoots", error, extra={"handler": "except Exception as error:"})
    roots.update({"python", "python3", "py", "pip", "pip3", "pwsh", "powershell"})
    return roots


def isKnownToolCommand(command: str) -> bool:
    root = commandRoot(command)
    return bool(root and root in loadCommonCommandRoots())


def _stripCommandPromptPrefix(line: str) -> str:
    command = str(line or "").strip()
    for prefix in ("PS> ", "PS ", "$ ", "> "):
        if command.startswith(prefix):
            command = command[len(prefix):].strip()
    return command


def parseBacktickToolCommands(message: str, policyDb: Any | None = None) -> list[str]:
    """Parse ToolCall command candidates from single, double, and triple backticks.

    The intentionally simple contract for the Grok web bridge is: Grok has no
    structured API tool_calls field here, so command-looking text in backticks is
    treated as a ToolCall when its command root is listed in common_commands.txt
    or has previously been whitelisted.
    """
    text = str(message or "")
    found: list[str] = []
    spans: list[tuple[int, int]] = []

    def accept(command: str) -> bool:
        clean = _stripCommandPromptPrefix(command)
        if not clean or clean.startswith("#"):
            return False
        root = commandRoot(clean)
        if not root:
            return False
        try:
            if policyDb is not None and getattr(policyDb, "decisionFor", lambda _root: "")(root) == "always_allow":
                return True
        except Exception:  # swallow-ok
            pass
        return isKnownToolCommand(clean)

    fencePattern = re.compile(r"```(?P<label>[^`\r\n]*)\r?\n(?P<body>[\s\S]*?)```", re.IGNORECASE)
    for match in fencePattern.finditer(text):
        spans.append(match.span())
        body = match.group("body") or ""
        for rawLine in body.splitlines():
            command = _stripCommandPromptPrefix(rawLine)
            if accept(command):
                found.append(command)

    inlinePattern = re.compile(r"(?<!`)`{1,2}([^`\r\n]{1,500})`{1,2}(?!`)")
    for match in inlinePattern.finditer(text):
        start = match.start()
        if any(a <= start < b for a, b in spans):
            continue
        command = _stripCommandPromptPrefix(match.group(1))
        if accept(command):
            found.append(command)

    unique: list[str] = []
    seen: set[str] = set()
    for command in found:
        key = command.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def bridgeAttachmentPromptText(message: str, attachments: object = None) -> str:
    rows = [str(message or "").strip()]
    items = attachments if isinstance(attachments, list) else []
    if not items:
        return rows[0]
    rows.append("\n\n[SuperGrok attached files]")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"attachment-{index}")
        mime = str(item.get("mime") or "application/octet-stream")
        size = int(item.get("size") or 0)
        sha = str(item.get("sha256") or "")
        rows.append(f"\n--- Attachment {index}: {name} | {mime} | {size} bytes | sha256={sha} ---")
        if item.get("text") is not None:
            text = str(item.get("text") or "")
            rows.append(text)
            if item.get("textTruncated"):
                rows.append("\n[Attachment text truncated by SuperGrok]")
        elif item.get("base64"):
            rows.append(f"[base64 attachment data follows]\n{item.get('base64')}")
        else:
            rows.append(str(item.get("note") or "[metadata only; attachment was too large to inline]") )
    return "\n".join(rows).strip()


def _safeReturnedFilename(name: str, fallback: str = "attachment.txt") -> str:
    raw = Path(str(name or fallback).strip().replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw).strip(" ._")
    return cleaned or fallback


def _inferMimeExtension(mime: str, fallback: str = ".bin") -> str:
    mime = str(mime or "").split(";", 1)[0].strip().lower()
    if mime == "text/plain":
        return ".txt"
    if mime == "text/html":
        return ".html"
    if mime == "application/json":
        return ".json"
    guessed = mimetypes.guess_extension(mime) if mime else ""
    return guessed or fallback


def _saveReturnedAttachment(name: str, data: bytes, *, source: str, mime: str = "") -> dict[str, Any]:
    RETURNED_ATTACHMENTS.mkdir(parents=True, exist_ok=True)
    filename = _safeReturnedFilename(name)
    base = Path(filename).stem or "attachment"
    suffix = Path(filename).suffix or _inferMimeExtension(mime)
    target = RETURNED_ATTACHMENTS / f"{base}{suffix}"
    counter = 1
    while target.exists():
        target = RETURNED_ATTACHMENTS / f"{base}_{counter}{suffix}"
        counter += 1
    target.write_bytes(data)
    return {"name": target.name, "path": str(target), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "mime": mime, "source": source}


def extractReturnedAttachmentsFromAnswer(answer: str) -> list[dict[str, Any]]:
    text = str(answer or "")
    saved: list[dict[str, Any]] = []
    # data URL attachments: data:mime;base64,...
    for match in re.finditer(r"data:(?P<mime>[A-Za-z0-9.+/-]+);base64,(?P<data>[A-Za-z0-9+/=\r\n]+)", text):
        mime = match.group("mime") or "application/octet-stream"
        try:
            data = base64.b64decode(re.sub(r"\s+", "", match.group("data") or ""), validate=False)
            saved.append(_saveReturnedAttachment("grok_attachment" + _inferMimeExtension(mime), data, source="data-url", mime=mime))
        except Exception as error:
            recordException("supergrok_bridge/app.py:extractReturnedAttachments.data-url", error, extra={"handler": "except Exception as error:"})
    # filename markers around fenced code blocks.
    fencePattern = re.compile(r"(?P<prefix>(?:^|\n).{0,160}?(?:file(?:name)?|path)\s*[:=]\s*(?P<name>[A-Za-z0-9._ /\\-]{1,120})\s*)?```(?P<label>[^`\r\n]*)\r?\n(?P<body>[\s\S]*?)```", re.IGNORECASE)
    for match in fencePattern.finditer(text):
        name = (match.group("name") or "").strip()
        body = match.group("body") or ""
        label = (match.group("label") or "").strip().lower()
        if not name:
            first = body.splitlines()[0].strip() if body.splitlines() else ""
            marker = re.match(r"(?:#|//|<!--)?\s*(?:file(?:name)?|path)\s*[:=]\s*([A-Za-z0-9._ /\\-]{1,120})", first, re.I)
            if marker:
                name = marker.group(1).strip().strip("--> ")
                body = "\n".join(body.splitlines()[1:])
        if not name:
            continue
        mime = mimetypes.guess_type(name)[0] or ("text/" + label if label and label not in {"text", "txt"} else "text/plain")
        try:
            saved.append(_saveReturnedAttachment(name, body.encode("utf-8"), source="fenced-block", mime=mime))
        except Exception as error:
            recordException("supergrok_bridge/app.py:extractReturnedAttachments.fence", error, extra={"handler": "except Exception as error:"})
    return saved


def _shellProgramForToolCalls() -> tuple[str, list[str]]:
    if os.name == "nt":
        program = shutil.which("pwsh") or shutil.which("pwsh.exe") or shutil.which("powershell") or shutil.which("powershell.exe") or "powershell"
        return program, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    return "/bin/bash", ["-lc"]


def executeCliToolCallsFromAnswer(answer: str, *, timeoutSeconds: int = PROCESS_DEFAULT_TTL_SECONDS) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    commands = parseBacktickToolCommands(answer)
    for command in commands:
        root = commandRoot(command)
        if not root or not isKnownToolCommand(command):
            continue
        program, baseArgs = _shellProgramForToolCalls()
        started = time.monotonic()
        writeTrafficLog({"eventType": "cli-toolcall-start", "command": command, "root": root, "program": program})
        try:
            completed = subprocess.run([program, *baseArgs, command], cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(1, int(timeoutSeconds or PROCESS_DEFAULT_TTL_SECONDS)), check=False)  # lifecycle-bypass-ok block-ok main-thread-ok: bounded subprocess.run with explicit timeout for CLI toolcall handler
            result = {
                "command": command,
                "root": root,
                "program": program,
                "returncode": int(completed.returncode or 0),
                "ok": int(completed.returncode or 0) == 0,
                "stdout": str(completed.stdout or "")[-20000:],
                "stderr": str(completed.stderr or "")[-20000:],
                "elapsedSeconds": round(time.monotonic() - started, 3),
            }
        except subprocess.TimeoutExpired as error:
            result = {"command": command, "root": root, "program": program, "returncode": 124, "ok": False, "stdout": str(getattr(error, "stdout", "") or "")[-20000:], "stderr": str(getattr(error, "stderr", "") or "")[-20000:], "error": f"TimeoutExpired after {getattr(error, 'timeout', '?')}s", "elapsedSeconds": round(time.monotonic() - started, 3)}
        except Exception as error:
            recordException("supergrok_bridge/app.py:executeCliToolCallsFromAnswer", error, extra={"command": command})
            result = {"command": command, "root": root, "program": program, "returncode": 1, "ok": False, "stdout": "", "stderr": "", "error": f"{type(error).__name__}: {error}", "elapsedSeconds": round(time.monotonic() - started, 3)}
        writeTrafficLog({"eventType": "cli-toolcall-result", **result})
        results.append(result)
    return results


def commandRoot(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return ""
    try:
        parts = shlex.split(text, posix=(os.name != "nt"))
        if parts:
            return Path(parts[0]).name.lower().removesuffix(".exe")
    except Exception as error:
        recordException("supergrok_bridge/app.py:113", error, extra={"handler": "except Exception:"})
        pass
    return Path(text.split()[0]).name.lower().removesuffix(".exe") if text.split() else ""


def hasShellControlOperators(command: str) -> bool:
    return bool(re.search(r"[;&|<>`]", command or ""))


def displayUrl(url: QUrl | str, maxLength: int = 160) -> str:
    """Return a readable, safer URL for logs without dumping giant OAuth query strings."""
    raw = url.toString() if isinstance(url, QUrl) else str(url)
    if not raw:
        return ""
    parsed = QUrl(raw)
    if parsed.isValid() and parsed.scheme() and parsed.host():
        clean = f"{parsed.scheme()}://{parsed.host()}{parsed.path()}"
        if parsed.hasQuery():
            clean += "?…"
    else:
        clean = raw
    if len(clean) > maxLength:
        clean = clean[: maxLength - 1] + "…"
    return clean


def navigationUrl(text: str) -> QUrl:
    """Convert address/search-bar text into a same-session navigable URL."""
    value = text.strip()
    if not value:
        return QUrl()
    lower = value.lower()
    if lower.startswith(("http://", "https://", "file://", "about:", "devtools:")):
        return QUrl.fromUserInput(value)
    if "." in value and " " not in value:
        return QUrl.fromUserInput(value)
    # Search terms stay inside Grok by default, keeping cookies/profile/session untouched.
    return QUrl.fromUserInput(f"https://grok.com/?q={value}")


SOURCE_DIALOG_DARK_THEME_CSS = """
  html, body { margin:0; min-height:100%; background:#1a1d23; color:#e2e8f0; font-family:Consolas, "Cascadia Mono", "Fira Code", monospace; font-size:13px; }
  pre[class*="language-"] { margin:0; padding:14px 18px; background:#1a1d23 !important; white-space:pre-wrap; overflow-wrap:anywhere; }
  code[class*="language-"], pre[class*="language-"] { color:#e2e8f0 !important; text-shadow:none !important; font-family:inherit; }
  .token.comment, .token.prolog, .token.doctype, .token.cdata { color:#5c6370; font-style:italic; }
  .token.punctuation { color:#abb2bf; }
  .token.property, .token.tag, .token.boolean, .token.number, .token.constant, .token.symbol, .token.deleted { color:#f78c6c; }
  .token.selector, .token.attr-name, .token.string, .token.char, .token.builtin, .token.inserted { color:#c3e88d; }
  .token.operator, .token.entity, .token.url, .language-css .token.string, .style .token.string { color:#89ddff; background:transparent; }
  .token.atrule, .token.attr-value, .token.keyword { color:#c792ea; }
  .token.function, .token.class-name { color:#82aaff; }
  .token.regex, .token.important, .token.variable { color:#ffcb6b; }
  .line-numbers .line-numbers-rows { border-right-color:#2d313a; }
  .line-numbers-rows > span:before { color:#3e4451; }
  ::selection { background:#3d4451; color:#e2e8f0; }
  ::-webkit-scrollbar { width:12px; height:12px; }
  ::-webkit-scrollbar-track { background:#1a1d23; }
  ::-webkit-scrollbar-thumb { background:#3d4451; border-radius:6px; }
  ::-webkit-scrollbar-thumb:hover { background:#4d5461; }
"""


class SourceDialog(QDialog):
    """Pretty source viewer with Prism-highlighted view and plain-text fallback.

    Default view is a QWebEngineView rendering the source through Prism with a
    custom dark "One Dark"-ish theme. A toggle button switches to a plain
    QPlainTextEdit if Prism fails to render (older PySide6 builds occasionally
    blank-paint a second WebEngine on the same page). Copy/Save/Find work in
    both modes and operate against the same backing source string.
    """

    def __init__(self, source: str, language: str = "markup", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = source or "<!-- empty source -->"  # noqa: nonconform
        self.language = language or "markup"  # noqa: nonconform
        self.setWindowTitle(loc(f"View Source — {self.language}"))
        self.resize(1120, 780)

        self.findBox = QLineEdit(self)  # noqa: nonconform
        self.findBox.setPlaceholderText(loc("Find in source... Ctrl+F"))

        # Plain fallback editor — always built, always works.
        self.editor = QPlainTextEdit(self)  # noqa: nonconform
        self.editor.setReadOnly(True)
        self.editor.setPlainText(self.source)
        self.editor.setFont(QFont("Consolas", 10))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self.openSourceContextMenu)

        # Prism-highlighted view — only created if Prism assets are available.
        self.prismView: QWebEngineView | None = None
        prismAvailable = (PRISM / "prism.min.css").exists() and (PRISM / "prism.min.js").exists()
        if prismAvailable:
            try:
                self.prismView = QWebEngineView(self)
                self.prismView.setHtml(self.buildPrismHtml(self.source, self.language), QUrl.fromLocalFile(str(ROOT / "source_dialog.html")))
            except Exception as error:
                recordException("supergrok_bridge/app.py:SourceDialog.prism-init", error, extra={"handler": "prism view init"})
                self.prismView = None  # noqa: nonconform

        self.stack = QStackedWidget(self)  # noqa: nonconform
        if self.prismView is not None:
            self.stack.addWidget(self.prismView)  # index 0 — Prism
            self.stack.addWidget(self.editor)     # index 1 — plain fallback
            self.stack.setCurrentIndex(0)
        else:
            self.stack.addWidget(self.editor)
            self.stack.setCurrentIndex(0)

        saveButton = QPushButton(loc("Save As..."), self)
        copyAllButton = QPushButton(loc("Copy All"), self)
        closeButton = QPushButton(loc("Close"), self)  # noqa: redundant
        findNextButton = QPushButton(loc("Find Next"), self)  # noqa: redundant
        findPreviousButton = QPushButton(loc("Find Prev"), self)
        self.toggleButton = QPushButton(loc("Plain"), self) if self.prismView is not None else QPushButton(loc("Plain"), self)  # noqa: nonconform
        self.toggleButton.setEnabled(self.prismView is not None)
        self.toggleButton.setToolTip(loc("Toggle Prism highlight / plain text view"))

        saveButton.clicked.connect(self.saveSourceAs)
        copyAllButton.clicked.connect(self.copyAllSource)
        closeButton.clicked.connect(self.close)  # noqa: redundant
        self.findBox.returnPressed.connect(self.findNextSource)
        findNextButton.clicked.connect(self.findNextSource)
        findPreviousButton.clicked.connect(self.findPreviousSource)
        self.toggleButton.clicked.connect(self.toggleHighlightMode)

        focusFindAction = QAction(loc("Find"), self)
        focusFindAction.setShortcut(QKeySequence.StandardKey.Find)
        focusFindAction.triggered.connect(lambda: (self.findBox.setFocus(), self.findBox.selectAll()))
        self.addAction(focusFindAction)

        copyAction = QAction(loc("Copy"), self)
        copyAction.setShortcut(QKeySequence.StandardKey.Copy)
        copyAction.triggered.connect(self.editor.copy)
        self.addAction(copyAction)

        selectAllAction = QAction(loc("Select All"), self)
        selectAllAction.setShortcut(QKeySequence.StandardKey.SelectAll)
        selectAllAction.triggered.connect(self.editor.selectAll)
        self.addAction(selectAllAction)

        modeLabel = loc("Highlighted source") if self.prismView is not None else loc("Plain source viewer")
        buttonRow = QHBoxLayout()
        buttonRow.addWidget(QLabel(modeLabel, self))
        buttonRow.addWidget(self.findBox, 1)
        buttonRow.addWidget(findPreviousButton)
        buttonRow.addWidget(findNextButton)
        buttonRow.addWidget(self.toggleButton)
        buttonRow.addWidget(copyAllButton)
        buttonRow.addWidget(saveButton)
        buttonRow.addWidget(closeButton)  # noqa: redundant

        layout = QVBoxLayout(self)
        layout.addLayout(buttonRow)
        layout.addWidget(self.stack, 1)

    def buildPrismHtml(self, source: str, language: str) -> str:
        escapedSource = html.escape(source, quote=False)
        prismCss = QUrl.fromLocalFile(str(PRISM / "prism.min.css")).toString()
        prismJs = QUrl.fromLocalFile(str(PRISM / "prism.min.js")).toString()
        # Component scripts are optional — present a small whitelist.
        componentTags = []
        for name in ("prism-markup.min.js", "prism-javascript.min.js", "prism-css.min.js", "prism-json.min.js", "prism-python.min.js", "prism-bash.min.js", "prism-powershell.min.js", "prism-sql.min.js", "prism-yaml.min.js"):
            path = PRISM / "components" / name
            if path.exists():
                componentTags.append(f'<script src="{html.escape(QUrl.fromLocalFile(str(path)).toString())}"></script>')
        cssTag = f'<link rel="stylesheet" href="{html.escape(prismCss)}">'
        jsTag = f'<script src="{html.escape(prismJs)}"></script>'
        comps = "\n".join(componentTags)
        return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>View Source</title>
{cssTag}
<style>{SOURCE_DIALOG_DARK_THEME_CSS}</style>
</head>
<body>
<pre><code class="language-{html.escape(language)}">{escapedSource}</code></pre>
{jsTag}
{comps}
<script>if (window.Prism) {{ Prism.highlightAll(); }}</script>
</body>
</html>'''

    @Slot()
    def toggleHighlightMode(self) -> None:
        if self.prismView is None:
            return
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.toggleButton.setText(loc("Highlight"))
        else:
            self.stack.setCurrentIndex(0)
            self.toggleButton.setText(loc("Plain"))

    def _findInPrism(self, text: str, backward: bool = False) -> None:
        if self.prismView is None:
            return
        try:
            flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
            self.prismView.findText(text, flags)
        except Exception as error:
            recordException("supergrok_bridge/app.py:SourceDialog._findInPrism", error, extra={"backward": backward})

    @Slot()
    def findNextSource(self) -> None:
        text = self.findBox.text()
        if not text:
            return
        if self.prismView is not None and self.stack.currentIndex() == 0:
            self._findInPrism(text, backward=False)
            return
        if not self.editor.find(text):
            self.editor.moveCursor(QTextCursor.MoveOperation.Start)
            self.editor.find(text)

    @Slot()
    def findPreviousSource(self) -> None:
        text = self.findBox.text()
        if not text:
            return
        if self.prismView is not None and self.stack.currentIndex() == 0:
            self._findInPrism(text, backward=True)
            return
        if not self.editor.find(text, QTextDocument.FindFlag.FindBackward):
            self.editor.moveCursor(QTextCursor.MoveOperation.End)
            self.editor.find(text, QTextDocument.FindFlag.FindBackward)

    @Slot(QPoint)
    def openSourceContextMenu(self, point: QPoint) -> None:
        menu = self.editor.createStandardContextMenu()
        menu.addSeparator()
        copyAllAction = QAction(loc("Copy All Source"), self)
        copyAllAction.triggered.connect(self.copyAllSource)
        findAction = QAction(loc("Find..."), self)
        findAction.triggered.connect(lambda: (self.findBox.setFocus(), self.findBox.selectAll()))
        saveAction = QAction(loc("Save Source As..."), self)
        saveAction.triggered.connect(self.saveSourceAs)
        menu.addAction(copyAllAction)
        menu.addAction(findAction)
        menu.addAction(saveAction)  # noqa: redundant
        menu.exec(self.editor.mapToGlobal(point))  # qt-main-thread-ok

    @Slot()
    def copyAllSource(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.source)

    @Slot()
    def saveSourceAs(self) -> None:
        filename, _selectedFilter = QFileDialog.getSaveFileName(
            self,
            "Save Source As",
            str(Path.home() / "grok_page_source.html"),
            "HTML Files (*.html *.htm);;Text Files (*.txt);;All Files (*)",
        )
        if not filename:
            writeTrafficLog({"eventType": "file-save-cancelled", "dialog": "SourceDialog.saveSourceAs"})
            return
        target = Path(filename)
        writeTrafficLog({"eventType": "file-save-start", "dialog": "SourceDialog.saveSourceAs", "path": str(target), "bytes": len(self.source.encode("utf-8", errors="replace"))})
        try:
            tracedWriteText(target, self.source, encoding="utf-8")
            writeTrafficLog({"eventType": "file-save-complete", "dialog": "SourceDialog.saveSourceAs", "path": str(target)})
        except Exception as error:
            writeTrafficLog({"eventType": "file-save-failed", "dialog": "SourceDialog.saveSourceAs", "path": str(target), "error": f"{type(error).__name__}: {error}"})
            QMessageBox.warning(self, "Save Source Failed", f"Could not save source to:\n{target}\n\n{type(error).__name__}: {error}")


class InjectionDialog(QDialog):
    def __init__(self, defaultCode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(loc("Inject JavaScript into Grok Page"))
        self.resize(950, 680)
        self.editor = QPlainTextEdit(self)  # noqa: nonconform
        self.editor.setPlainText(defaultCode)
        self.editor.setFont(QFont("Consolas", 10))

        self.injectButton = QPushButton(loc("Inject"), self)  # noqa: nonconform
        self.cancelButton = QPushButton(loc("Cancel"), self)  # noqa: nonconform
        self.injectButton.clicked.connect(self.accept)
        self.cancelButton.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(QLabel(loc("Runs in the live Grok page. Use carefully."), self))
        buttons.addStretch(1)
        buttons.addWidget(self.injectButton)
        buttons.addWidget(self.cancelButton)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addLayout(buttons)

    def code(self) -> str:
        return self.editor.toPlainText()



RequestBase = declarative_base()


class WebRequestRecord(RequestBase):  # type: ignore[misc, valid-type]
    __tablename__ = "web_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(Float, nullable=False, index=True)
    created_iso = Column(String(32), nullable=False)
    view_label = Column(String(255), nullable=False)
    method = Column(String(32), nullable=False)  # noqa: redundant
    url = Column(Text, nullable=False, index=True)
    display_url = Column(Text, nullable=False)
    first_party_url = Column(Text, nullable=False, default="")
    initiator = Column(Text, nullable=False, default="")
    navigation_type = Column(String(128), nullable=False, default="")
    resource_type = Column(String(128), nullable=False, default="")
    raw_json = Column(Text, nullable=False)


class RequestDatabase:
    """SQLAlchemy recorder for Qt WebEngine and JavaScript request events."""

    def __init__(self, path: Path) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", future=True)  # noqa: nonconform
        RequestBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def ensureSchema(self) -> None:
        RequestBase.metadata.create_all(self.engine)

    def requestValues(self, payload: dict[str, Any], rawJson: str, now: float) -> dict[str, Any]:
        keyMap = {
            "view_label": "viewLabel",
            "method": "method",
            "url": "url",
            "display_url": "displayUrl",
            "first_party_url": "firstPartyUrl",
            "initiator": "initiator",
            "navigation_type": "navigationType",
            "resource_type": "resourceType",
        }
        values = {column: str(payload.get(source, "")) for column, source in keyMap.items()}
        values.update(
            {
                "created_at": now,
                "created_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "raw_json": rawJson,
            }
        )
        return values

    def record(self, payload: dict[str, Any]) -> int:  # noqa: nonconform
        now = time.time()
        rawJson = json.dumps(payload, ensure_ascii=False, indent=2)
        values = self.requestValues(payload, rawJson, now)
        with self.Session() as session:
            row = WebRequestRecord(**values)
            session.add(row)
            session.commit()
            return int(row.id)

    def get(self, requestId: int) -> dict[str, Any] | None:
        with self.Session() as session:
            row = session.get(WebRequestRecord, requestId)
            if row is None:
                return None
            rawJson = str(row.raw_json)
        try:
            return json.loads(rawJson)
        except Exception as error:
            recordException("RequestDatabase.get.json", error, extra={"requestId": requestId, "rawJsonPreview": rawJson[:500]})
            return {"raw_json": rawJson}

    def latestRowsForGrokResponses(self) -> list[tuple[int, str, str]]:  # noqa: nonconform
        with self.Session() as session:
            rows = (
                session.query(WebRequestRecord)
                .filter(
                    or_(
                        WebRequestRecord.url.like("%/rest/app-chat/conversations/%/responses%"),
                        WebRequestRecord.url.like("%/rest/app-chat/conversations/new%"),
                        WebRequestRecord.raw_json.like("%/rest/app-chat/conversations/%/responses%"),
                        WebRequestRecord.raw_json.like("%/rest/app-chat/conversations/new%"),
                    )
                )
                .order_by(WebRequestRecord.id.desc())
                .limit(50)
                .all()
            )
            return [(int(row.id), str(row.url), str(row.raw_json)) for row in rows]

    def latestGrokChatSeed(self) -> dict[str, Any]:
        """Return the newest captured Grok chat POST template for the GrokChat modal."""
        for requestId, rowUrl, rawJson in self.latestRowsForGrokResponses():
            try:
                payload = json.loads(rawJson)
                url = str(payload.get("url") or rowUrl or "")
                conversationId = extractConversationId(url)
                page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
                if not conversationId:
                    conversationId = extractConversationId(str(page.get("url", "")))

                request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
                requestBody = request.get("body", "")
                template: dict[str, Any] = {}
                if isinstance(requestBody, str) and requestBody.strip().startswith("{"):
                    template = json.loads(requestBody)

                response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
                responseBody = response.get("body", "")
                parsedResponse = parseGrokResponseStream(responseBody if isinstance(responseBody, str) else "")

                parentResponseId = (
                    parsedResponse.get("responseId")
                    or extractRid(str(page.get("url", "")))
                    or str(template.get("parentResponseId") or "")
                )

                if conversationId:
                    return {
                        "requestId": requestId,
                        "conversationId": conversationId,
                        "parentResponseId": parentResponseId,
                        "template": template,
                        "lastAnswer": parsedResponse.get("message", ""),
                        "sourceUrl": url,
                    }
            except Exception as error:
                recordException("RequestDatabase.latestGrokChatSeed", error, extra={"requestId": requestId, "rowUrl": rowUrl})
                continue
        return {}

    def close(self) -> None:
        self.engine.dispose()


PolicyBase = declarative_base()


class CommandPolicyRecord(PolicyBase):  # type: ignore[misc, valid-type]
    __tablename__ = "command_policy"
    id = Column(Integer, primary_key=True, autoincrement=True)
    command_root = Column(String(255), unique=True, nullable=False, index=True)
    decision = Column(String(32), nullable=False, default="always_allow")


UIStateBase = declarative_base()


class UIStateRecord(UIStateBase):  # type: ignore[misc, valid-type]
    __tablename__ = "ui_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(255), unique=True, nullable=False, index=True)
    state_value = Column(Text, nullable=False, default="")


ProcessBase = declarative_base()


class ProcessRecord(ProcessBase):  # type: ignore[misc, valid-type]
    __tablename__ = "processes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    process_key = Column(String(128), unique=True, nullable=False, index=True)
    command = Column(Text, nullable=False)
    program = Column(Text, nullable=False)
    arguments_json = Column(Text, nullable=False, default="[]")
    pid = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, index=True, default="created")
    started_at = Column(Float, nullable=False, index=True)
    started_iso = Column(String(32), nullable=False)
    ttl_seconds = Column(Integer, nullable=False, default=PROCESS_DEFAULT_TTL_SECONDS)
    expires_at = Column(Float, nullable=False, index=True)
    finished_at = Column(Float, nullable=True)
    exit_code = Column(Integer, nullable=True)
    exit_status = Column(String(64), nullable=False, default="")
    error = Column(Text, nullable=False, default="")
    stdout_preview = Column(Text, nullable=False, default="")
    stderr_preview = Column(Text, nullable=False, default="")  # noqa: redundant


class ProcessDatabase:
    """Persistent SQLAlchemy process table for ToolCall subprocess supervision."""

    def __init__(self, path: Path) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", future=True)  # noqa: nonconform
        ProcessBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def ensureSchema(self) -> None:
        ProcessBase.metadata.create_all(self.engine)

    def clearForNewRun(self) -> None:
        with self.Session() as session:
            session.query(ProcessRecord).delete()
            session.commit()

    def createProcess(self, processKey: str, command: str, program: str, arguments: list[str], ttlSeconds: int) -> None:
        now = time.time()
        values = {
            "process_key": processKey,
            "command": command,
            "program": program,
            "arguments_json": json.dumps(arguments, ensure_ascii=False),
            "pid": 0,
            "status": "created",
            "started_at": now,
            "started_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "ttl_seconds": int(ttlSeconds),
            "expires_at": now + max(1, int(ttlSeconds)),
        }
        with self.Session() as session:
            session.add(ProcessRecord(**values))
            session.commit()

    def updateStatus(self, processKey: str, status: str, **updates: Any) -> None:
        payload = dict(updates)
        payload["status"] = status
        allowed = {"pid", "status", "finished_at", "exit_code", "exit_status", "error", "stdout_preview", "stderr_preview"}
        with self.Session() as session:
            row = session.query(ProcessRecord).filter_by(process_key=processKey).one_or_none()
            if row is None:
                return
            for key, value in payload.items():
                if key in allowed:
                    setattr(row, key, value)
            session.commit()

    def markStarted(self, processKey: str, pid: int) -> None:
        self.updateStatus(processKey, "running", pid=int(pid or 0))

    def markCompleted(self, processKey: str, exitCode: int, exitStatus: str, stdout: str, stderr: str) -> None:
        self.updateStatus(
            processKey,
            "completed" if int(exitCode) == 0 else "failed",
            finished_at=time.time(),
            exit_code=int(exitCode),
            exit_status=exitStatus,
            stdout_preview=(stdout or "")[-4000:],
            stderr_preview=(stderr or "")[-4000:],
        )

    def markTimedOut(self, processKey: str, error: str, stdout: str = "", stderr: str = "") -> None:
        self.updateStatus(
            processKey,
            "timed_out",
            finished_at=time.time(),
            error=error,
            stdout_preview=(stdout or "")[-4000:],
            stderr_preview=(stderr or "")[-4000:],
        )

    def markFault(self, processKey: str, error: str, stdout: str = "", stderr: str = "") -> None:
        self.updateStatus(
            processKey,
            "faulted",
            finished_at=time.time(),
            error=error,
            stdout_preview=(stdout or "")[-4000:],
            stderr_preview=(stderr or "")[-4000:],
        )

    def allRows(self, limit: int = 200) -> list[dict[str, Any]]:  # noqa: nonconform
        with self.Session() as session:
            rows = (
                session.query(ProcessRecord)
                .order_by(ProcessRecord.started_at.desc())
                .limit(int(limit))
                .all()
            )
            return [self.rowToDict(row) for row in rows]

    def rowToDict(self, row: ProcessRecord) -> dict[str, Any]:
        return {
            "processKey": str(row.process_key),
            "status": str(row.status),
            "pid": int(row.pid or 0),
            "command": str(row.command),
            "program": str(row.program),
            "arguments": json.loads(str(row.arguments_json or "[]")),
            "startedIso": str(row.started_iso),
            "ttlSeconds": int(row.ttl_seconds or 0),
            "expiresAt": float(row.expires_at or 0),
            "finishedAt": float(row.finished_at or 0) if row.finished_at is not None else None,
            "exitCode": row.exit_code,
            "exitStatus": str(row.exit_status or ""),
            "error": str(row.error or ""),
            "stdoutPreview": str(row.stdout_preview or ""),
            "stderrPreview": str(row.stderr_preview or ""),
        }

    def close(self) -> None:
        self.engine.dispose()


class UIStateDatabase:
    """SQLAlchemy-backed persistent UI state store."""

    def __init__(self, path: Path) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", future=True)  # noqa: nonconform
        UIStateBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def get(self, key: str, default: str = "") -> str:  # noqa: nonconform
        key = (key or "").strip()
        if not key:
            return default
        with self.Session() as session:
            row = session.query(UIStateRecord).filter_by(state_key=key).one_or_none()
            return str(row.state_value) if row is not None else default

    def set(self, key: str, value: str) -> None:
        key = (key or "").strip()
        if not key:
            return
        with self.Session() as session:
            row = session.query(UIStateRecord).filter_by(state_key=key).one_or_none()
            if row is None:
                session.add(UIStateRecord(state_key=key, state_value=str(value)))
            else:
                row.state_value = str(value)
            session.commit()

    def getBool(self, key: str, default: bool = True) -> bool:
        value = self.get(key, "1" if default else "0").strip().lower()
        return value in {"1", "true", "yes", "on", "visible", "show"}

    def setBool(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")

    def close(self) -> None:
        self.engine.dispose()


class CommandPolicyDatabase:
    def __init__(self, path: Path) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(f"sqlite:///{path}", future=True)  # noqa: nonconform
        PolicyBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)  # noqa: nonconform

    def decisionFor(self, root: str) -> str:  # noqa: nonconform
        root = (root or "").lower().strip()
        if not root:
            return ""
        with self.Session() as session:
            row = session.query(CommandPolicyRecord).filter_by(command_root=root).one_or_none()
            return str(row.decision) if row is not None else ""

    def alwaysAllow(self, root: str) -> None:
        root = (root or "").lower().strip()
        if not root:
            return
        with self.Session() as session:
            row = session.query(CommandPolicyRecord).filter_by(command_root=root).one_or_none()
            if row is None:
                session.add(CommandPolicyRecord(command_root=root, decision="always_allow"))
            else:
                row.decision = "always_allow"
            session.commit()

    def close(self) -> None:
        self.engine.dispose()

def extractConversationId(text: str) -> str:
    match = re.search(r"/c/([0-9a-fA-F-]{36})(?:[/?#]|$)", text or "")
    if not match:
        match = re.search(r"/conversations/([0-9a-fA-F-]{36})/responses(?:[/?#]|$)", text or "")
    return match.group(1) if match else ""


def extractRid(text: str) -> str:
    match = re.search(r"[?&]rid=([0-9a-fA-F-]{36})(?:[&#]|$)", text or "")
    return match.group(1) if match else ""



def cleanGrokAnswerCandidate(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def isBadGrokAnswerCandidate(text: object, prompt: object = "") -> bool:
    clean = cleanGrokAnswerCandidate(text)
    if not clean:
        return True
    lowered = clean.lower()
    promptClean = cleanGrokAnswerCandidate(prompt).lower()
    if promptClean and lowered == promptClean:
        return True
    generic = {
        "refer to the following content:",
        "refer to the following content",
        "refer to following content:",
        "refer to following content",
        "what do you want to know?",
        "what do you want to know",
        "message chatgpt",
        "ask anything",
        "what can i help with",
        "new chat",
        "fast",
        "private",
        "imagine",
        "sign in",
        "sign up",
    }
    if lowered in generic:
        return True
    if re.match(r"^refer to (the )?following content:?$", clean, re.I):
        return True
    if re.match(r"^by messaging grok, you agree to", clean, re.I):
        return True
    if re.match(r"^chatgpt can make mistakes", clean, re.I):
        return True
    if re.match(r"^toggle sidebar\b", clean, re.I):
        return True
    return False


def parseGrokResponseStream(body: str) -> dict[str, str]:
    """Parse Grok's newline-delimited JSON response stream into a clean answer.

    Grok has used more than one stream shape. Older captures placed fields like
    ``token`` and ``modelResponse`` directly under ``result``. Current Grok web
    responses often wrap them under ``result.response`` and return that stream
    from ``/rest/app-chat/conversations/new``. Keep both shapes working so the
    CLI bridge can finish from the network stream instead of waiting on fragile
    DOM text.
    """
    tokens: list[str] = []
    finalMessage = ""
    responseId = ""

    def considerPacket(packet: dict[str, Any]) -> None:
        nonlocal finalMessage, responseId
        result = packet.get("result") if isinstance(packet, dict) else None
        if not isinstance(result, dict):
            return
        response = result.get("response") if isinstance(result.get("response"), dict) else None
        candidates: list[dict[str, Any]] = [result]
        if response is not None:
            candidates.insert(0, response)
        for item in candidates:
            modelResponse = item.get("modelResponse")
            if isinstance(modelResponse, dict):
                message = modelResponse.get("message")
                if isinstance(message, str) and message:
                    finalMessage = message
                rid = modelResponse.get("responseId")
                if isinstance(rid, str) and rid:
                    responseId = rid
            token = item.get("token")
            if isinstance(token, str) and item.get("messageTag") == "final":
                tokens.append(token)
            rid = item.get("responseId")
            if isinstance(rid, str) and rid and not responseId and not isinstance(item.get("userResponse"), dict):
                responseId = rid

    for rawLine in str(body or "").splitlines():
        line = rawLine.strip()
        if not line:
            continue
        try:
            packet = json.loads(line)
        except Exception as error:
            recordException("supergrok_bridge/app.py:parseGrokResponseStream", error, extra={"linePreview": line[:500]})
            continue
        if isinstance(packet, dict):
            considerPacket(packet)
    return {"message": finalMessage or "".join(tokens), "responseId": responseId}




def _jsonPacketsFromResponseBody(body: str) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    text = str(body or "")
    for rawLine in text.splitlines():
        line = rawLine.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            packet = json.loads(line)
        except Exception:  # swallow-ok
            continue
        if isinstance(packet, dict):
            packets.append(packet)
    if not packets and text.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                packets.append(parsed)
            elif isinstance(parsed, list):
                packets.extend([item for item in parsed if isinstance(item, dict)])
        except Exception:  # swallow-ok
            pass
    return packets


def _contentTextFromAny(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _contentTextFromAny(item)
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(value, dict):
        if isinstance(value.get("parts"), list):
            return _contentTextFromAny(value.get("parts"))
        if isinstance(value.get("text"), str):
            return str(value.get("text") or "")
        if isinstance(value.get("value"), str):
            return str(value.get("value") or "")
        if isinstance(value.get("content"), (str, list, dict)):
            return _contentTextFromAny(value.get("content"))
    return ""


def parseChatGptResponseStream(body: str) -> dict[str, str]:
    """Parse ChatGPT web fetch/SSE/patch responses into assistant text.

    The ChatGPT web app is a React application and its transport changes shape.
    This accepts historical ``/backend-api/conversation`` SSE packets, newer
    patch-style packets with ``p``/``v`` fields, and Responses-API-like output
    arrays.  It intentionally prefers explicitly assistant-authored content, then
    falls back to streaming deltas only when no final assistant message exists.
    """
    finalMessages: list[str] = []
    responseId = ""
    tokenPieces: list[str] = []

    def roleFromNode(node: dict[str, Any]) -> str:
        author = node.get("author") if isinstance(node.get("author"), dict) else {}
        message = node.get("message") if isinstance(node.get("message"), dict) else {}
        messageAuthor = message.get("author") if isinstance(message.get("author"), dict) else {}  # noqa: redundant
        role = str(
            node.get("role")
            or author.get("role")
            or message.get("role")
            or messageAuthor.get("role")  # noqa: redundant
            or ""
        ).strip().lower()
        return role

    def appendFinal(text: object) -> None:
        clean = str(text or "").strip()
        if clean and clean not in finalMessages:
            finalMessages.append(clean)

    def appendToken(text: object) -> None:
        value = str(text or "")
        if value:
            tokenPieces.append(value)

    def pathText(value: Any) -> str:
        if isinstance(value, (list, tuple)):
            return "/".join(str(item) for item in value)
        return str(value or "")

    def contentText(node: dict[str, Any]) -> str:
        # ChatGPT commonly stores text under content.parts.  OpenAI Responses
        # style objects commonly store it under output/content/text.
        for key in ("content", "delta", "text", "value", "v"):
            if key in node:
                text = _contentTextFromAny(node.get(key))
                if text:
                    return text
        return ""

    def visit(node: Any, roleHint: str = "", pathHint: str = "") -> None:
        nonlocal responseId
        if isinstance(node, list):
            for item in node:
                visit(item, roleHint=roleHint, pathHint=pathHint)
            return
        if not isinstance(node, dict):
            return

        role = roleFromNode(node) or roleHint
        nodeType = str(node.get("type") or node.get("event") or node.get("message_type") or "").lower()
        path = pathText(node.get("p") or node.get("path") or pathHint)
        pathLower = path.lower()

        rid = (
            node.get("response_id")
            or node.get("responseId")
            or node.get("message_id")
            or node.get("messageId")  # noqa: redundant
            or node.get("conversation_id")  # noqa: redundant
            or node.get("conversationId")
            or node.get("id")  # noqa: redundant
        )
        if isinstance(rid, str) and rid and not responseId:
            responseId = rid

        if role == "assistant":
            text = contentText(node)
            if text.strip():
                appendFinal(text)

        # OpenAI Responses-style completed output item.
        if nodeType in {"output_text", "response.output_text.done", "response.output_text.delta"}:
            text = str(node.get("text") or node.get("delta") or node.get("value") or "")
            if text:
                if nodeType.endswith("delta"):
                    appendToken(text)
                else:
                    appendFinal(text)

        # ChatGPT web patch/event streams often use p/o/v fields.  Accept
        # content patches that point into message/content/parts or output text.
        if pathLower and any(marker in pathLower for marker in ("message/content", "content/parts", "output_text", "/parts/")):
            text = _contentTextFromAny(node.get("v") if "v" in node else node.get("value"))
            if text and role in {"", "assistant"}:
                appendToken(text)

        # Common streaming deltas.  Keep these as fallback so user text in JSON
        # templates does not beat explicit assistant content.
        if role in {"", "assistant"}:
            for key in ("delta", "text_delta", "value", "text"):
                value = node.get(key)
                if isinstance(value, str) and value and ("delta" in key or "delta" in nodeType or nodeType in {"text", "message_delta"}):
                    appendToken(value)

        # Historical ChatGPT conversation messages: { message: { author:{role}, content:{parts} } }
        message = node.get("message") if isinstance(node.get("message"), dict) else None
        if message is not None:
            visit(message, roleHint=role or roleHint, pathHint=path)

        # Mapping values and generic containers.
        for key in ("data", "result", "response", "item", "payload", "conversation", "messages", "mapping", "output", "content", "choices"):
            child = node.get(key)
            if isinstance(child, (dict, list)):
                if key == "content" and role == "assistant":
                    text = _contentTextFromAny(child)
                    if text.strip():
                        appendFinal(text)
                visit(child, roleHint=role, pathHint=path)

        # Some JSON maps conversation nodes under random ids, each with a message.
        for key, child in node.items():
            if key in {"message", "data", "result", "response", "item", "payload", "conversation", "messages", "mapping", "output", "content", "choices"}:
                continue
            if isinstance(child, dict) and ("message" in child or "author" in child or "content" in child):
                visit(child, roleHint=role, pathHint=path)

    for packet in _jsonPacketsFromResponseBody(body):
        visit(packet)

    def collapseTokens(parts: list[str]) -> str:
        progressive = ""
        for raw in parts:
            piece = str(raw or "")
            if not piece:
                continue
            if not progressive:
                progressive = piece
            elif piece.startswith(progressive):
                progressive = piece
            elif progressive.endswith(piece):
                continue
            else:
                progressive += piece
        return progressive.strip()

    fallback = collapseTokens(tokenPieces)
    final = finalMessages[-1].strip() if finalMessages else ""
    return {"message": final or fallback, "responseId": responseId}

def parseAssistantResponseStream(body: str, target: object = "") -> dict[str, str]:
    if normalizeChatTarget(target) == "chatgpt":
        parsed = parseChatGptResponseStream(body)
        if parsed.get("message"):
            return parsed
    parsed = parseGrokResponseStream(body)
    if parsed.get("message"):
        return parsed
    if normalizeChatTarget(target) != "chatgpt":
        return parseChatGptResponseStream(body)
    return parsed


def isAssistantChatStreamUrl(url: object, target: object = "") -> bool:
    text = str(url or "").lower()
    wanted = normalizeChatTarget(target)
    if "/rest/app-chat/conversations/new" in text:
        return True
    if "/rest/app-chat/conversations/" in text and re.search(r"/responses(?:[?#]|$)", text):
        return True
    if "chatgpt.com" in text or "chat.openai.com" in text or wanted == "chatgpt":
        # Explicitly reject noisy telemetry/static routes before using the broad
        # ChatGPT fallback.  These can be huge and never contain assistant text.
        noisy = (
            "/ces/", "/statsig", "/rgstr", "/log", "/analytics", "/cdn/", "/_next/",
            "googletagmanager", "cookielaw", "intercom", "sentry", "datadog",
        )
        if any(item in text for item in noisy):
            return False
        chatHints = (
            "/backend-api/conversation",
            "/backend-api/f/conversation",
            "/backend-api/lat/r",
            "/backend-api/responses",
            "/backend-api/chat",
            "/backend-api/message",
            "/backend-api/thread",
            "/conversation",
            "/responses",
            "/messages",
            "/threads",
        )
        if any(hint in text for hint in chatHints):
            return True
    return False

def enumName(value: Any) -> str:
    try:
        return str(value).split('.')[-1]
    except Exception as error:
        recordException("supergrok_bridge/app.py:725", error, extra={"handler": "except Exception:"})
        return str(value)


def qbytearrayText(value: Any) -> str:
    try:
        if hasattr(value, "data"):
            return bytes(value).decode("utf-8", errors="replace")
        return bytes(value).decode("utf-8", errors="replace")
    except Exception as error:
        recordException("supergrok_bridge/app.py:qbytearrayText", error, extra={"handler": "except Exception:"})
        return str(value)


def redactedHeaderValue(key: str, value: Any) -> str:
    lowered = str(key or "").lower()
    if any(part in lowered for part in ("authorization", "cookie", "token", "secret", "credential", "password")):
        return "[REDACTED]"
    return str(value if value is not None else "")


def qtRequestHeaders(info: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    try:
        if not hasattr(info, "httpHeaders"):
            return headers
        raw = info.httpHeaders()
        if hasattr(raw, "items"):
            iterator = raw.items()
        else:
            iterator = raw
        for key, value in iterator:
            keyText = qbytearrayText(key)
            headers[keyText] = redactedHeaderValue(keyText, qbytearrayText(value))
    except Exception as error:  # swallow-ok
        headers["__headers_error"] = f"{type(error).__name__}: {error}"
    return headers


class WebRequestInterceptor(QWebEngineUrlRequestInterceptor):
    requestSeen = Signal(dict)

    def __init__(self, viewLabel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.viewLabel = viewLabel  # noqa: nonconform

    def interceptRequest(self, info: Any) -> None:  # Qt calls this synchronously before Chromium sends the request.
        try:
            url = info.requestUrl()
            if hasattr(url, "scheme") and url.scheme() not in {"http", "https"}:
                return
            firstPartyUrl = info.firstPartyUrl() if hasattr(info, "firstPartyUrl") else QUrl()
            initiator = info.initiator() if hasattr(info, "initiator") else QUrl()
            requestHeaders = qtRequestHeaders(info)
            payload = {
                "viewLabel": self.viewLabel,
                "method": qbytearrayText(info.requestMethod()) if hasattr(info, "requestMethod") else "",
                "url": url.toString(),
                "displayUrl": displayUrl(url),
                "firstPartyUrl": firstPartyUrl.toString() if hasattr(firstPartyUrl, "toString") else str(firstPartyUrl),
                "initiator": initiator.toString() if hasattr(initiator, "toString") else str(initiator),
                "navigationType": enumName(info.navigationType()) if hasattr(info, "navigationType") else "",
                "resourceType": enumName(info.resourceType()) if hasattr(info, "resourceType") else "",
                "captureLayer": "qt-interceptor",
                "eventType": "request-metadata",
                "request": {"method": qbytearrayText(info.requestMethod()) if hasattr(info, "requestMethod") else "", "url": url.toString(), "headers": requestHeaders, "body": ""},
                "response": {"headers": {}, "body": "", "note": "Qt interceptor does not expose response metadata/body."},
                "note": "Captured by QWebEngineUrlRequestInterceptor before Chromium networking. Qt may expose request headers depending on the PySide/Qt build; request/response bodies are captured separately by JavaScript fetch/XMLHttpRequest hooks when available.",
            }
            self.requestSeen.emit(payload)
        except Exception as error:
            recordException("supergrok_bridge/app.py:768", error, extra={"handler": "except Exception:"})
            return


class RequestPane(QWidget):
    def __init__(self, database: RequestDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database  # noqa: nonconform
        self.requests: list[tuple[int, dict[str, Any], str]] = []

        self.label = QLabel(loc("Requests"), self)  # noqa: nonconform
        self.searchBox = QLineEdit(self)  # noqa: nonconform
        self.searchBox.setPlaceholderText(loc("Search requests by URL, method, resource type, initiator..."))
        self.searchBox.setClearButtonEnabled(True)

        self.requestList = QListWidget(self)  # noqa: nonconform
        self.requestList.setMinimumHeight(175)
        self.requestList.setMaximumHeight(235)
        self.requestList.setAlternatingRowColors(True)

        self.viewButton = QPushButton(loc("View"), self)  # noqa: nonconform
        self.copyButton = QPushButton(loc("Copy Requests"), self)  # noqa: nonconform
        self.sourceButton = QPushButton(loc("View Source"), self)  # noqa: redundant
        self.clearButton = QPushButton(loc("Clear List"), self)  # noqa: redundant  # noqa: nonconform

        self.preview = QWebEngineView(self)  # noqa: nonconform
        self.preview.setMinimumHeight(210)
        self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(self.openPreviewContextMenu)
        enableWebSettings(self.preview.settings(), localContentCanAccessRemote=False)

        header = QHBoxLayout()
        header.addWidget(self.label)
        header.addStretch(1)
        header.addWidget(self.viewButton)
        header.addWidget(self.copyButton)
        header.addWidget(self.sourceButton)  # noqa: redundant
        header.addWidget(self.clearButton)  # noqa: redundant

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.searchBox)
        layout.addWidget(self.requestList)
        layout.addWidget(QLabel(loc("Request Preview"), self))
        layout.addWidget(self.preview)

        self.searchBox.textChanged.connect(self.filterRequests)
        self.viewButton.clicked.connect(self.showSelectedRequest)
        self.copyButton.clicked.connect(self.copyVisibleRequests)  # noqa: redundant
        self.sourceButton.clicked.connect(self.openVisibleRequestsSource)  # noqa: redundant
        self.clearButton.clicked.connect(self.clearList)
        self.requestList.itemSelectionChanged.connect(self.showSelectedRequest)  # noqa: redundant
        self.requestList.itemDoubleClicked.connect(lambda _item: self.showSelectedRequest())
        self.setPreviewText({"status": "Request inspector ready. Select a request above."})

    def addRequest(self, requestId: int, payload: dict[str, Any]) -> None:
        method = str(payload.get("method", "")).upper() or "REQ"
        resource = str(payload.get("resourceType", ""))
        display = payload.get("displayUrl") or payload.get("url") or ""
        text = f"#{requestId} {method} {resource} {display}".strip()
        self.requests.append((requestId, payload, text))
        if len(self.requests) > REQUEST_LIST_LIMIT:
            self.requests = self.requests[-REQUEST_LIST_LIMIT:]
        # Do not auto-select every new request. Auto-previewing every load can recursively
        # churn the preview WebEngine and make the real Grok pane look stuck.
        previousId = self.currentRequestId()
        self.filterRequests()
        if previousId is None and self.requestList.count() > 0:
            self.requestList.setCurrentRow(self.requestList.count() - 1)

    @Slot()
    def clearList(self) -> None:
        self.requests.clear()
        self.requestList.clear()
        self.setPreviewText({"status": "Request list cleared. Database rows are preserved."})

    @Slot()
    def filterRequests(self) -> None:
        needle = self.searchBox.text().strip().lower()
        currentId = self.currentRequestId()
        self.requestList.clear()
        selectedRow = -1
        for requestId, payload, text in self.requests:
            haystack = "\n".join([
                text,
                str(payload.get("url", "")),
                str(payload.get("method", "")),
                str(payload.get("resourceType", "")),
                str(payload.get("navigationType", "")),
                str(payload.get("initiator", "")),
                str(payload.get("firstPartyUrl", "")),
                json.dumps(payload, ensure_ascii=False),
            ]).lower()
            if needle and needle not in haystack:
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, requestId)
            self.requestList.addItem(item)
            if currentId == requestId:
                selectedRow = self.requestList.count() - 1
        if selectedRow >= 0:
            self.requestList.setCurrentRow(selectedRow)

    def currentRequestId(self) -> int | None:
        item = self.requestList.currentItem()
        if item is None:
            selected = self.requestList.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value) if value is not None else None

    @Slot()
    def showSelectedRequest(self) -> None:
        requestId = self.currentRequestId()
        if requestId is None and self.requestList.count() > 0:
            self.requestList.setCurrentRow(0)
            requestId = self.currentRequestId()
        if requestId is None:
            self.setPreviewText({"status": "Select a request first."})
            return
        payload = self.database.get(requestId)
        if payload is None:
            self.setPreviewText({"error": f"Request #{requestId} not found"})
            return
        self.setPreviewText(payload)

    def visibleRequestIds(self) -> list[int]:
        ids: list[int] = []
        for row in range(self.requestList.count()):
            item = self.requestList.item(row)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if value is not None:
                ids.append(int(value))
        return ids

    def visibleRequestPayloads(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for requestId in self.visibleRequestIds():
            payload = self.database.get(requestId)
            if payload is not None:
                out.append(payload)
        return out

    def visibleRequestsJson(self) -> str:
        payload = {
            "copiedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "count": len(self.visibleRequestIds()),
            "search": self.searchBox.text(),
            "requests": self.visibleRequestPayloads(),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @Slot()
    def copyVisibleRequests(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.visibleRequestsJson())
        self.setPreviewText({"status": "Copied request details to clipboard", "count": len(self.visibleRequestIds())})

    @Slot()
    def openVisibleRequestsSource(self) -> None:
        dialog = SourceDialog(self.visibleRequestsJson(), "json", self)
        dialog.exec()  # qt-main-thread-ok

    def setPreviewText(self, payload: dict[str, Any] | str) -> None:
        source = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        self.preview.setHtml(self.buildPreviewHtml(source, "json"), QUrl.fromLocalFile(str(ROOT / "request_preview.html")))

    def buildPreviewHtml(self, source: str, language: str) -> str:
        escapedSource = html.escape(source, quote=False)
        prismCss = QUrl.fromLocalFile(str(PRISM / "prism.min.css")).toString() if (PRISM / "prism.min.css").exists() else ""
        prismJs = QUrl.fromLocalFile(str(PRISM / "prism.min.js")).toString() if (PRISM / "prism.min.js").exists() else ""
        cssTag = f'<link rel="stylesheet" href="{html.escape(prismCss)}">' if prismCss else ""
        jsTag = f'<script src="{html.escape(prismJs)}"></script>' if prismJs else ""
        return f'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Request Preview</title>
{cssTag}
<style>
  html, body {{ margin:0; min-height:100%; background:#111827; color:#f9fafb; }}
  pre {{ margin:0; padding:10px; font-size:12px; line-height:1.35; white-space:pre-wrap; overflow-wrap:anywhere; }}
  code {{ white-space:pre-wrap !important; overflow-wrap:anywhere; }}
</style>
</head>
<body>
<pre><code class="language-{html.escape(language)}">{escapedSource}</code></pre>
{jsTag}
<script>if (window.Prism) {{ Prism.highlightAll(); }}</script>
</body>
</html>'''

    @Slot(QPoint)
    def openPreviewContextMenu(self, point: QPoint) -> None:
        menu = self.preview.createStandardContextMenu()
        copyAction = QAction(loc("Copy"), self)
        copyAction.setShortcut(QKeySequence.StandardKey.Copy)
        copyAction.triggered.connect(lambda: self.preview.page().triggerAction(QWebEnginePage.WebAction.Copy))
        selectAllAction = QAction(loc("Select All"), self)
        selectAllAction.triggered.connect(lambda: self.preview.page().triggerAction(QWebEnginePage.WebAction.SelectAll))
        copyRequestsAction = QAction(loc("Copy Request List JSON"), self)
        copyRequestsAction.triggered.connect(self.copyVisibleRequests)
        menu.addSeparator()
        menu.addAction(copyAction)
        menu.addAction(selectAllAction)
        menu.addAction(copyRequestsAction)  # noqa: redundant
        menu.exec(self.preview.mapToGlobal(point))  # qt-main-thread-ok


class BridgeWebPage(QWebEnginePage):
    consoleMessage = Signal(str, str, int, str)
    alertRequested = Signal(str, str)

    def javaScriptConsoleMessage(self, level: Any, message: str, lineNumber: int, sourceID: str) -> None:
        self.consoleMessage.emit(enumName(level), str(message), int(lineNumber), str(sourceID))
        try:
            super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)
        except Exception as error:
            recordException("supergrok_bridge/app.py:989", error, extra={"handler": "except Exception:"})
            pass

    def javaScriptAlert(self, securityOrigin: QUrl, msg: str) -> None:
        origin = securityOrigin.toString() if hasattr(securityOrigin, "toString") else str(securityOrigin)
        self.alertRequested.emit(origin, str(msg))


class ManagedWebView(QWebEngineView):
    def __init__(
        self,
        label: str,
        sourceCallback: Callable[[QWebEngineView], None],
        devToolsCallback: Callable[[QWebEnginePage, str, bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.label = label  # noqa: nonconform
        self.sourceCallback = sourceCallback  # noqa: nonconform
        self.devToolsCallback = devToolsCallback  # noqa: redundant  # noqa: nonconform
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.openContextMenu)

    @Slot(QPoint)
    def openContextMenu(self, point: QPoint) -> None:
        menu = self.createStandardContextMenu()
        menu.addSeparator()

        copyAction = QAction(loc("Copy"), self)
        copyAction.setShortcut(QKeySequence.StandardKey.Copy)
        copyAction.triggered.connect(lambda: self.page().triggerAction(QWebEnginePage.WebAction.Copy))
        menu.addAction(copyAction)

        sourceAction = QAction(loc("View Source"), self)
        sourceAction.triggered.connect(lambda: self.sourceCallback(self))
        menu.addAction(sourceAction)

        devToolsAction = QAction(loc("Dev Inspect with Chromium DevTools"), self)
        devToolsAction.triggered.connect(lambda: self.devToolsCallback(self.page(), self.label, True))
        menu.addAction(devToolsAction)

        openDevToolsAction = QAction(loc("Open DevTools Dock"), self)
        openDevToolsAction.triggered.connect(lambda: self.devToolsCallback(self.page(), self.label, False))
        menu.addAction(openDevToolsAction)

        menu.exec(self.mapToGlobal(point))  # qt-main-thread-ok


def enableWebSettings(settings: QWebEngineSettings, localContentCanAccessRemote: bool = False) -> None:
    for name, value in [
        ("JavascriptEnabled", True),
        ("LocalContentCanAccessFileUrls", True),
        ("LocalContentCanAccessRemoteUrls", localContentCanAccessRemote),
        ("DeveloperExtrasEnabled", True),
        ("JavascriptCanOpenWindows", True),
        ("JavascriptCanAccessClipboard", True),
    ]:
        attribute = getattr(QWebEngineSettings.WebAttribute, name, None)
        if attribute is not None:
            settings.setAttribute(attribute, value)


class DebugScriptPane(QWidget):
    def __init__(
        self,
        sourceCallback: Callable[[QWebEngineView], None],
        devToolsCallback: Callable[[QWebEnginePage, str, bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        SCRIPTS.mkdir(parents=True, exist_ok=True)
        self.scriptsList = QListWidget(self)
        self.scriptsList.setMinimumHeight(190)
        self.scriptsList.setMaximumHeight(220)

        self.reloadButton = QPushButton(loc("Reload Scripts"), self)  # noqa: nonconform
        self.view = ManagedWebView("Debug Output", sourceCallback, devToolsCallback, self)
        enableWebSettings(self.view.settings(), localContentCanAccessRemote=False)

        header = QHBoxLayout()
        header.addWidget(QLabel(loc("Scripts"), self))
        header.addStretch(1)
        header.addWidget(self.reloadButton)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.scriptsList)
        layout.addWidget(QLabel(loc("Debug Output"), self))
        layout.addWidget(self.view, 1)

        self.reloadButton.clicked.connect(self.reloadScripts)
        self.view.setHtml(self.buildDebugHtml(), QUrl.fromLocalFile(str(ROOT / "debug.html")))
        self.reloadScripts()

    def reloadScripts(self) -> None:
        self.scriptsList.clear()
        for path in sorted(SCRIPTS.glob("*.js")):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.scriptsList.addItem(item)
        self.append("system", f"Loaded {self.scriptsList.count()} script(s) from {SCRIPTS}")

    def scriptPathFromItem(self, item: QListWidgetItem) -> Path:
        return Path(str(item.data(Qt.ItemDataRole.UserRole)))

    def buildDebugHtml(self) -> str:
        jqueryTag = ""
        if JQUERY.exists():
            jqueryUrl = QUrl.fromLocalFile(str(JQUERY)).toString()
            jqueryTag = f'<script src="{html.escape(jqueryUrl)}"></script>'
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Debug Output</title>
{jqueryTag}
<style>
  :root {{ color-scheme: dark; }}
  html, body {{ margin:0; min-height:100%; background:#0b0e13; color:#f3f4f6; font-family:Consolas, "Segoe UI", monospace; }}
  header {{ position:sticky; top:0; z-index:2; padding:8px 10px; background:#111827; border-bottom:1px solid #2b3445; font-family:Segoe UI, Arial, sans-serif; }}
  #log {{ padding:10px; display:flex; flex-direction:column; gap:8px; }}
  .entry {{ border:1px solid #293142; border-radius:10px; padding:8px 10px; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.35; }}
  .entry .role {{ color:#9ca3af; font-size:11px; text-transform:uppercase; letter-spacing:.06em; margin-bottom:4px; }}
  .system {{ background:#151922; }}
  .script {{ background:#102036; }}
  .console {{ background:#141f16; }}
  .error {{ background:#2a1414; color:#ffd4d4; }}
  .result {{ background:#171421; }}
</style>
</head>
<body>
<header><strong>SuperGrok Debug Output</strong><br><small>Double-click scripts above. Console output and return values show here.</small></header>
<main id="log"></main>
<script>
(function() {{
  function esc(text) {{
    return String(text == null ? "" : text).replace(/[&<>"']/g, function(ch) {{
      return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch];
    }});
  }}
  function append(role, text) {{
    var safeRole = String(role || 'system').toLowerCase().replace(/[^a-z0-9_-]/g, '');
    var html = '<section class="entry ' + safeRole + '"><div class="role">' + esc(role || 'system') + '</div><div class="body">' + esc(text || '') + '</div></section>';
    if (window.jQuery) {{
      window.jQuery('#log').append(html);
    }} else {{
      document.getElementById('log').insertAdjacentHTML('beforeend', html);
    }}
    window.scrollTo(0, document.body.scrollHeight);
    return true;
  }}
  window.SuperGrokDebug = {{ append: append }};
  append('system', 'Debug pane ready.');
}})();
</script>
</body>
</html>"""

    def _onChatLoaded(self, ok: bool) -> None:
        self._chatReady = bool(ok)
        if not ok:
            return
        if self._pendingToolCards:
            pending = list(self._pendingToolCards)
            self._pendingToolCards.clear()
            QTimer.singleShot(250, lambda: [self.addToolCallCard(commandId, command, reason) for commandId, command, reason in pending])

    def append(self, role: str, text: str) -> None:
        debugLog(str(role or "debug"), str(text).rstrip())
        roleJson = json.dumps(role)
        textJson = json.dumps(str(text).rstrip(), ensure_ascii=False)
        self.view.page().runJavaScript(
            f"window.SuperGrokDebug && window.SuperGrokDebug.append({roleJson}, {textJson});"
        )


class ChatPane(QWidget):
    def __init__(
        self,
        sourceCallback: Callable[[QWebEngineView], None],
        devToolsCallback: Callable[[QWebEnginePage, str, bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.chatView = ManagedWebView("Grok Chat", sourceCallback, devToolsCallback, self)
        self.chatView.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        enableWebSettings(self.chatView.settings(), localContentCanAccessRemote=False)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText(loc("Type a prompt for Grok, then press Enter..."))
        self.sendButton = QPushButton(loc("Send"), self)
        self.probeButton = QPushButton(loc("Probe DOM"), self)
        self.sourceButton = QPushButton(loc("View Source"), self)  # noqa: redundant

        inputRow = QHBoxLayout()
        inputRow.addWidget(self.input, 1)
        inputRow.addWidget(self.sendButton)

        buttonRow = QHBoxLayout()
        buttonRow.addWidget(self.probeButton)
        buttonRow.addWidget(self.sourceButton)
        buttonRow.addStretch(1)

        layout = QVBoxLayout(self)
        title = QLabel(loc("Grok Chat"), self)
        title.setObjectName("ChatTitle")
        layout.addWidget(title)
        layout.addWidget(self.chatView, 1)
        layout.addLayout(inputRow)
        layout.addLayout(buttonRow)

        self.channel = QWebChannel(self.chatView.page())  # noqa: nonconform
        self.chatView.page().setWebChannel(self.channel)
        self.toolCallBridge: ToolCallDecisionBridge | None = None
        self.toolCallManagerRef: Any | None = None
        self._pendingToolCards: list[tuple[str, str, str]] = []
        self._chatReady = False
        self.chatView.page().loadFinished.connect(self._onChatLoaded)
        self.chatView.setHtml(self.buildChatHtml(), QUrl.fromLocalFile(str(ROOT / "chat.html")))

    def buildChatHtml(self) -> str:
        jqueryTag = ""
        if JQUERY.exists():
            jqueryUrl = QUrl.fromLocalFile(str(JQUERY)).toString()
            jqueryTag = f'<script src="{html.escape(jqueryUrl)}"></script>'
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Grok Chat</title>
{jqueryTag}
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  :root {{ color-scheme: dark; }}
  html, body {{ margin: 0; min-height: 100%; background: #101114; color: #f3f4f6; font-family: Segoe UI, Arial, sans-serif; }}
  header {{ position: sticky; top: 0; z-index: 2; padding: 10px 12px; border-bottom: 1px solid #292c33; background: #15171c; }}
  header .small {{ color: #aab0bc; font-size: 12px; margin-top: 3px; }}
  #messages {{ padding: 12px; display: flex; flex-direction: column; gap: 10px; }}
  .message {{ border: 1px solid #2b2f39; border-radius: 12px; padding: 10px 12px; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.42; }}
  .message .role {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: #9ca3af; margin-bottom: 6px; }}
  .message.you {{ background: #162033; }}
  .message.grok {{ background: #171b22; }}
  .message.system {{ background: #201b13; color: #ffe7bf; }}
  .message.debug {{ background: #101f2d; color: #cce7ff; }}
  .toolcall {{ border: 1px solid #475569; border-radius: 12px; background: #131722; padding: 9px 10px; }}
  .toolcall .tool-line {{ display: flex; align-items: center; gap: 8px; color: #e2e8f0; font-size: 13px; }}
  .toolcall .tool-title {{ font-weight: 700; color: #f8fafc; margin-right: 4px; }}
  .toolcall button {{ border: 1px solid #475569; border-radius: 999px; padding: 3px 10px; background: #1e293b; color: #dbeafe; cursor: pointer; font-size: 12px; font-weight: 600; }}
  .toolcall button:hover {{ background: #334155; }}
  .toolcall button.run {{ color: #bbf7d0; }}
  .toolcall button.whitelist {{ color: #fde68a; }}
  .toolcall .command {{ margin-top: 7px; padding: 7px 8px; border-radius: 8px; background: #050816; color: #c4f1ff; font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }}
  .toolcall .status {{ margin-left: auto; color: #a7f3d0; font-size: 12px; }}
</style>
</head>
<body>
<header>
  <strong>SuperGrok Bridge Chat</strong>
  <div class="small">Local chat pane. jQuery is loaded only here/debug panes unless you manually inject it.</div>
</header>
<main id="messages"></main>
<script>
(function() {{
  function escapeText(text) {{
    return String(text).replace(/[&<>"']/g, function(ch) {{
      return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch];
    }});
  }}
  function appendEntry(role, text) {{
    var safeRole = String(role || 'system').toLowerCase().replace(/[^a-z0-9_-]/g, '');
    var html = '<section class="message ' + safeRole + '"><div class="role">' + escapeText(role || 'system') + '</div><div class="body">' + escapeText(text || '') + '</div></section>';
    if (window.jQuery) {{
      window.jQuery('#messages').append(html);
    }} else {{
      document.getElementById('messages').insertAdjacentHTML('beforeend', html);
    }}
    window.scrollTo(0, document.body.scrollHeight);
    return true;
  }}
  function appendToolCall(id, command, reason) {{
    id = String(id || '');
    command = String(command || '').trim();
    if (!id || !command) {{ return false; }}
    var safeId = id.replace(/[^a-zA-Z0-9_-]/g, '');
    var html = ''
      + '<section class="toolcall" id="toolcall-' + escapeText(safeId) + '">'
      + '<div class="tool-line"><span class="tool-title">Tool Call</span>'
      + '<button class="run" data-decision="allow">Execute</button>'
      + '<button class="whitelist" data-decision="always_allow">Always Allow</button>'
      + '<span class="status">queued</span></div>'
      + '<div class="command">' + escapeText(command) + '</div>'
      + '</section>';
    document.getElementById('messages').insertAdjacentHTML('beforeend', html);
    var card = document.getElementById('toolcall-' + safeId);
    card.querySelectorAll('button').forEach(function(button) {{
      button.addEventListener('click', function() {{
        var decision = button.getAttribute('data-decision') || '';
        if (window.toolCallBridge && window.toolCallBridge.toolCallDecision) {{
          window.toolCallBridge.toolCallDecision(id, decision);
        }} else {{
          appendEntry('system', 'ToolCall bridge not ready for ' + id + ' decision=' + decision);
        }}
      }});
    }});
    window.scrollTo(0, document.body.scrollHeight);
    return true;
  }}
  function updateToolCall(id, status) {{
    var safeId = String(id || '').replace(/[^a-zA-Z0-9_-]/g, '');
    var card = document.getElementById('toolcall-' + safeId);
    if (!card) {{ return false; }}
    var statusNode = card.querySelector('.status');
    if (statusNode) {{ statusNode.textContent = String(status || 'updated'); }}
    card.querySelectorAll('button').forEach(function(button) {{ button.disabled = true; button.style.opacity = '0.55'; }});
    return true;
  }}
  window.SuperGrokChat = {{ appendEntry: appendEntry, appendToolCall: appendToolCall, updateToolCall: updateToolCall }};
  if (window.QWebChannel && window.qt && window.qt.webChannelTransport) {{
    new QWebChannel(window.qt.webChannelTransport, function(channel) {{
      window.toolCallBridge = channel.objects.toolCallBridge;
      appendEntry('system', 'ToolCall bridge ready.');
    }});
  }} else {{
    appendEntry('system', 'ToolCall bridge unavailable until QWebChannel is ready.');
  }}
  appendEntry('system', 'Shell loaded. Log into Grok on the right, then type below.');
}})();
</script>
</body>
</html>"""

    def _onChatLoaded(self, ok: bool) -> None:
        self._chatReady = bool(ok)
        if not ok:
            return
        if self._pendingToolCards:
            pending = list(self._pendingToolCards)
            self._pendingToolCards.clear()
            QTimer.singleShot(250, lambda: [self.addToolCallCard(commandId, command, reason) for commandId, command, reason in pending])

    def append(self, role: str, text: str, parseToolCalls: bool = True) -> None:
        roleJson = json.dumps(role)
        cleanText = str(text).rstrip()
        textJson = json.dumps(cleanText, ensure_ascii=False)
        self.chatView.page().runJavaScript(
            f"window.SuperGrokChat && window.SuperGrokChat.appendEntry({roleJson}, {textJson});"
        )
        if parseToolCalls and str(role or "").strip().lower() == "grok" and self.toolCallManagerRef is not None:
            # Regression guard: any Grok message rendered into the local chat must
            # also be parsed for fenced ToolCall blocks. This keeps ```calc,
            # ```bash, and friends from degrading back into plain text.
            QTimer.singleShot(0, lambda message=cleanText: self.toolCallManagerRef.queueCommandsFromMessage(message, source="chat-pane"))

    def setToolCallManager(self, manager: Any) -> None:
        self.toolCallManagerRef = manager
        self.toolCallBridge = ToolCallDecisionBridge(manager, self)
        self.channel.registerObject("toolCallBridge", self.toolCallBridge)

    def addToolCallCard(self, commandId: str, command: str, reason: str) -> None:
        command = str(command or "").strip()
        if not command:
            return
        if not self._chatReady:
            self._pendingToolCards.append((commandId, command, reason))
            return
        commandIdJson = json.dumps(commandId)
        commandJson = json.dumps(command, ensure_ascii=False)
        reasonJson = json.dumps(reason, ensure_ascii=False)
        js = f"""
            (function() {{
              if (window.SuperGrokChat && window.SuperGrokChat.appendToolCall) {{
                return window.SuperGrokChat.appendToolCall({commandIdJson}, {commandJson}, {reasonJson});
              }}
              return false;
            }})();
        """
        self.chatView.page().runJavaScript(js, lambda ok: None if ok else self._pendingToolCards.append((commandId, command, reason)))

    def updateToolCallCard(self, commandId: str, status: str) -> None:
        commandIdJson = json.dumps(commandId)
        statusJson = json.dumps(status, ensure_ascii=False)
        self.chatView.page().runJavaScript(
            f"window.SuperGrokChat && window.SuperGrokChat.updateToolCall({commandIdJson}, {statusJson});"
        )


class GrokPageController:
    def __init__(self, page: QWebEnginePage, chat: ChatPane, debugPane: DebugScriptPane, config: AppConfig) -> None:
        self.page = page
        self.chat = chat
        self.debugPane = debugPane  # noqa: redundant  # noqa: nonconform
        self.config = config  # noqa: redundant  # noqa: nonconform
        self.lastPageText = ""  # noqa: nonconform

    def trace(self, message: str) -> None:
        if self.config.debug:
            self.debugPane.append("system", message)

    def loadBridge(self, callback: Callable[[Any], None] | None = None) -> None:
        if not BRIDGE_JS.exists():
            self.chat.append("system", f"Missing bridge JS: {BRIDGE_JS}")
            return
        script = tracedReadText(BRIDGE_JS, encoding="utf-8")
        self.page.runJavaScript(script, callback or (lambda result: None))

    def runBridgeExpression(self, expression: str, callback: Callable[[Any], None] | None = None) -> None:
        self.loadBridge(lambda _ignored: self.page.runJavaScript(expression, callback or (lambda result: None)))

    def runRawScript(self, scriptName: str, script: str) -> None:
        self.debugPane.append("script", f"Running {scriptName} in Grok page...")
        expression = self.wrapScriptForDebug(script)
        self.page.runJavaScript(expression, lambda result: self.onScriptResult(scriptName, result))

    def wrapScriptForDebug(self, script: str) -> str:
        # Do not use eval() or new Function() here. Grok's page has a strict CSP that
        # blocks unsafe-eval, so script files must be embedded directly into the
        # runJavaScript payload that Qt asks Chromium to execute.
        sourceUrl = "SuperGrokScriptRunner.js"
        return rf"""(function() {{
  const __logs = [];
  const __old = {{ log: console.log, info: console.info, warn: console.warn, error: console.error }};
  function __serialize(value) {{
    try {{
      if (value === undefined) return "undefined";
      if (value === null) return "null";
      if (typeof value === "string") return value;
      if (typeof value === "function") return String(value);
      return JSON.stringify(value, null, 2);
    }} catch (error) {{
      try {{ return String(value); }} catch (_ignored) {{ return "[unserializable]"; }}
    }}
  }}
  function __capture(level, args) {{
    const text = Array.from(args).map(__serialize).join(" ");
    __logs.push({{ level: level, text: text }});
  }}
  console.log = function() {{ __capture("log", arguments); return __old.log.apply(console, arguments); }};
  console.info = function() {{ __capture("info", arguments); return __old.info.apply(console, arguments); }};
  console.warn = function() {{ __capture("warn", arguments); return __old.warn.apply(console, arguments); }};  # noqa: redundant
  console.error = function() {{ __capture("error", arguments); return __old.error.apply(console, arguments); }};  # noqa: redundant
  try {{
    const __result = (function() {{
{script}
//# sourceURL={sourceUrl}
    }}).call(window);
    return JSON.stringify({{ ok: true, result: __serialize(__result), logs: __logs, runner: "direct-runJavaScript-no-eval" }});
  }} catch (error) {{
    return JSON.stringify({{ ok: false, error: String(error && error.message ? error.message : error), stack: String(error && error.stack ? error.stack : ""), logs: __logs, runner: "direct-runJavaScript-no-eval" }});
  }} finally {{
    console.log = __old.log;
    console.info = __old.info;
    console.warn = __old.warn;  # noqa: redundant
    console.error = __old.error;  # noqa: redundant
  }}
}})();"""

    def onScriptResult(self, scriptName: str, result: Any) -> None:
        parsed = result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except Exception as error:
                recordException("supergrok_bridge/app.py:1441", error, extra={"handler": "except Exception:"})
                self.debugPane.append("result", f"{scriptName} returned raw string:\n{result}")
                return

        if not isinstance(parsed, dict):
            self.debugPane.append("result", f"{scriptName} returned: {json.dumps(parsed, ensure_ascii=False)}")
            return

        for entry in parsed.get("logs", []) or []:
            if isinstance(entry, dict):
                self.debugPane.append("console", f"{entry.get('level', 'log')}: {entry.get('text', '')}")

        if parsed.get("ok"):
            self.debugPane.append("result", f"{scriptName} result:\n{parsed.get('result', '')}")
        else:
            self.debugPane.append("error", f"{scriptName} error:\n{parsed.get('error', '')}\n{parsed.get('stack', '')}")

    def sendPrompt(self, prompt: str) -> None:
        self.chat.append("you", prompt)
        self.snapshotPageText(lambda _before: self._sendPromptAfterSnapshot(prompt))

    def _sendPromptAfterSnapshot(self, prompt: str) -> None:
        promptJson = json.dumps(prompt)
        expression = f"window.SuperGrokBridgeDom.sendPrompt({promptJson});"
        self.runBridgeExpression(expression, self.onPromptSent)

    def onPromptSent(self, result: Any) -> None:
        self.chat.append("system", f"bridge send result: {json.dumps(result, ensure_ascii=False)}")

    def snapshotPageText(self, callback: Callable[[str], None] | None = None) -> None:
        self.runBridgeExpression("window.SuperGrokBridgeDom.pageText();", lambda text: self._onSnapshot(text, callback))

    def _onSnapshot(self, text: Any, callback: Callable[[str], None] | None) -> None:
        self.lastPageText = text if isinstance(text, str) else ""
        if callback:
            callback(self.lastPageText)

    def dumpMessages(self) -> None:
        self.runBridgeExpression(
            "window.SuperGrokBridgeDom.messages();",
            lambda result: self.debugPane.append("result", f"messages:\n{json.dumps(result, ensure_ascii=False, indent=2)}"),
        )

    def sayHi(self) -> None:
        self.sendPrompt("hi")

    def injectCode(self, code: str) -> None:
        self.runRawScript("manual_inject.js", code)

    def probeDom(self) -> None:
        expression = "window.SuperGrokBridgeDom.inventory();"
        self.runBridgeExpression(
            expression,
            lambda result: self.debugPane.append("result", f"probe:\n{json.dumps(result, ensure_ascii=False, indent=2)}"),
        )


class SendOnEnterPlainTextEdit(QPlainTextEdit):
    """Plain-text editor where Enter sends and Shift+Enter inserts a newline."""

    enterPressed = Signal()

    def keyPressEvent(self, event: Any) -> None:
        try:
            isEnter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            hasShift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if isEnter and not hasShift:
                event.accept()
                self.enterPressed.emit()
                return
        except Exception as error:
            recordException("supergrok_bridge/app.py:1511", error, extra={"handler": "except Exception:"})
            pass
        super().keyPressEvent(event)



def normalizeProbePayload(result: Any) -> dict[str, Any]:
    """Normalize QWebEngine JavaScript probe returns into one dict.

    Some Qt/PySide builds return JavaScript objects as QVariant maps, while
    others return strings. The probe script now intentionally returns JSON text,
    but this keeps old resident services and manual probes readable too.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        text = result.strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
                return {"ok": False, "reason": f"probe JSON was {type(parsed).__name__}", "raw": text[:1000]}
            except Exception as error:  # swallow-ok
                return {"ok": False, "reason": f"probe string JSON parse failed: {type(error).__name__}: {error}", "raw": text[:1000]}
        return {"ok": False, "reason": "probe returned blank string", "raw": ""}
    if result is None:
        return {"ok": False, "reason": "probe returned null/undefined", "raw": ""}
    return {"ok": False, "reason": f"probe returned {type(result).__name__}", "raw": str(result)[:1000]}


def buildGrokDomSurfaceProbeScript(target: object = "grok") -> str:
    """Return a Grok DOM readiness probe for CLI/service sends.

    Important Grok behavior: before text is typed, the visible form buttons can
    be Attach / Model / Dictation while the real send arrow appears or becomes
    enabled only after the prompt editor receives input.  This probe therefore
    gates on the prompt/editor surface and records button diagnostics, but it no
    longer rejects a ready page merely because the pre-type best button is not a
    send arrow.
    """
    return r"""(function() {
  try {
  function __visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }
  function __queryAll(selectors) {
    const out = [];
    for (const selector of selectors) {
      try {
        const nodes = Array.from(document.querySelectorAll(selector));
        for (const node of nodes) if (__visible(node)) out.push({ selector: selector, el: node });
      } catch (error) {}
    }
    return out;
  }
  function __queryFirst(selectors) {
    const rows = __queryAll(selectors);
    return rows.length ? rows[0] : null;
  }
  const promptSelectors = [
    '#prompt-textarea',
    'textarea#prompt-textarea',
    'div#prompt-textarea[contenteditable="true"]',
    '[data-testid="composer"] [contenteditable="true"]',
    'form [contenteditable="true"]',
    'div.ProseMirror[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'textarea',
    '[contenteditable="true"]',
    '[role="textbox"]',
    'div.ProseMirror',
    '[aria-label*="message" i]',
    '[aria-label*="prompt" i]',
    '[aria-label*="ask" i]'
  ];
  const buttonSelectors = [
    'button[data-testid="send-button"]',
    'button[data-testid="composer-submit-button"]',
    'button#composer-submit-button',
    'button[data-testid="chat-submit"]',
    'button[aria-label*="Send prompt" i]',
    'button[aria-label*="Send message" i]',
    'button[type="submit"][aria-label="Enviar"]',
    'button[aria-label*="enviar" i]',
    'button[aria-label*="send" i]',
    'button[aria-label*="submit" i]',
    'button[type="submit"]',
    'form button[type="submit"]',
    'form button',
    'button:has(svg)'
  ];
  function __buttonInfo(row, promptEl) {
    if (!row || !row.el) return { found: false, looksLikeSend: false, enabled: false, hasSvg: false, excluded: false, score: 0, selector: '' };
    const button = row.el;
    const aria = String(button.getAttribute('aria-label') || '');
    const title = String(button.getAttribute('title') || '');
    const testid = String(button.getAttribute('data-testid') || '');  # noqa: redundant
    const type = String(button.getAttribute('type') || '').toLowerCase();
    const text = String(button.innerText || button.textContent || '').trim();
    const classes = String(button.getAttribute('class') || '');
    const haystack = (aria + ' ' + title + ' ' + testid + ' ' + text + ' ' + classes).toLowerCase();
    const hasSvg = !!button.querySelector('svg');
    const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
    const promptForm = promptEl && promptEl.closest ? promptEl.closest('form') : null;
    const sameForm = !!(promptForm && button.closest && button.closest('form') === promptForm);
    const excluded = /\b(attach|upload|file|files|add files|add files and more|composer-plus|model select|dictation|voice|microphone|mic|sidebar|search|project|history|menu|settings|extended|create an image|write or edit|look something up)\b/.test(haystack);
    let score = 0;
    if (sameForm) score += 45;
    if (/chat-submit|send|submit|enviar|arrow|up/.test(haystack)) score += 120;
    if (testid.toLowerCase() === 'chat-submit') score += 220;
    if (['send-button','composer-submit-button'].indexOf(testid.toLowerCase()) >= 0) score += 260;
    if (type === 'submit') score += 160;
    if (hasSvg) score += 25;
    if (excluded) score -= 500;
    if (disabled) score -= 250;
    return {
      found: true,
      selector: row.selector,
      enabled: !disabled,
      hasSvg: hasSvg,
      excluded: !!excluded,
      sameForm: !!sameForm,
      looksLikeSend: !disabled && !excluded && score >= 90,
      score: score,
      ariaLabel: aria,
      title: title,
      dataTestId: testid,
      type: type,
      text: text.slice(0, 80),
      htmlPreview: String(button.outerHTML || '').slice(0, 500)
    };
  }
  const promptRows = __queryAll(promptSelectors);
  const promptRow = promptRows.length ? promptRows[0] : null;
  const promptEl = promptRow ? promptRow.el : null;
  const buttonRows = __queryAll(buttonSelectors).map(function(row) { return __buttonInfo(row, promptEl); }).sort(function(a, b) { return (b.score || 0) - (a.score || 0); });
  const bestButton = buttonRows.length ? buttonRows[0] : __buttonInfo(null, promptEl);
  const innerText = String(document.body && document.body.innerText || '');
  const textContent = String(document.body && document.body.textContent || '');
  const text = (innerText || textContent).trim();
  const layoutRendered = innerText.trim().length > 20;
  const viewportWidth = Number(window.innerWidth || document.documentElement.clientWidth || 0);
  const viewportHeight = Number(window.innerHeight || document.documentElement.clientHeight || 0);
  const lower = (location.href + '\n' + document.title + '\n' + text.slice(0, 4000)).toLowerCase();
  const authChromePresent = /\b(log in|login|sign in|signin|sign up|signup)\b/.test(lower);
  const hardAuthLikely = /\b(continue with google|accounts\.x\.ai|auth\.openai\.com|authenticate|authentication required|verification|captcha|sign in to continue|log in to continue|login to continue|please sign in|please log in|session expired|access denied)\b/.test(lower) || /\/(login|signin|auth|oauth|captcha)(\b|[/?#])/.test(String(location.href || '').toLowerCase());
  const publicComposerLikely = !!promptRow && /\b(what do you want to know|by messaging grok|ask anything|message grok|message chatgpt|what can i help with)\b/.test(lower);
  const loginLikely = !!hardAuthLikely || (!!authChromePresent && !promptRow);
  const authChromeOnly = !!authChromePresent && !!promptRow && !hardAuthLikely;
  const readyForTyping = !!promptRow && !hardAuthLikely;
  const sendSurfaceReady = !!bestButton.found && !!bestButton.enabled && !!bestButton.looksLikeSend;
  let reason = 'ready';
  if (!layoutRendered) reason = 'AI chat layout not rendered yet; Chromium surface is loaded but not visibly composited';
  else if (!promptRow) reason = 'prompt box not found';
  else if (hardAuthLikely) reason = 'login/auth/captcha blocker likely';
  else if (authChromeOnly && publicComposerLikely) reason = 'public Grok composer ready; sign-in links are present but not blocking typing';
  else if (!sendSurfaceReady) reason = 'prompt ready; send button will be re-probed after typing';
  const __probeResult = {
    ok: !!readyForTyping,
    readyForTyping: !!readyForTyping,
    sendSurfaceReady: !!sendSurfaceReady,
    promptFound: !!promptRow,
    promptSelector: promptRow ? promptRow.selector : '',
    sendButtonFound: !!bestButton.found,
    sendButtonEnabled: !!bestButton.enabled,
    sendButtonHasSvg: !!bestButton.hasSvg,  # noqa: redundant
    sendButtonLooksLikeSend: !!bestButton.looksLikeSend,  # noqa: redundant
    sendButtonExcluded: !!bestButton.excluded,
    sendButton: bestButton,
    loginLikely: !!loginLikely,
    hardAuthLikely: !!hardAuthLikely,
    authChromePresent: !!authChromePresent,
    authChromeOnly: !!authChromeOnly,
    publicComposerLikely: !!publicComposerLikely,
    jqueryPresent: !!window.jQuery,
    url: location.href,
    title: document.title || '',
    reason: reason,
    layoutRendered: !!layoutRendered,
    innerTextLength: innerText.length,
    textContentLength: textContent.length,
    viewportWidth: viewportWidth,
    viewportHeight: viewportHeight,
    bodyPreview: text.slice(0, 1200),
    promptSelectorCount: promptSelectors.length,
    promptCandidateCount: promptRows.length,
    buttonSelectorCount: buttonSelectors.length,  # noqa: redundant
    buttonCandidateCount: buttonRows.length,  # noqa: redundant
    buttonCandidates: buttonRows.slice(0, 8)
  };
  return JSON.stringify(__probeResult);
  } catch (__error) {
    return JSON.stringify({
      ok: false,
      readyForTyping: false,
      sendSurfaceReady: false,
      promptFound: false,
      sendButtonFound: false,
      sendButtonEnabled: false,
      sendButtonHasSvg: false,
      sendButtonLooksLikeSend: false,
      loginLikely: false,
      jqueryPresent: !!window.jQuery,
      reason: 'probe exception',
      error: String(__error && __error.message ? __error.message : __error),
      stack: String(__error && __error.stack ? __error.stack : ''),
      url: String(window.location && window.location.href || ''),
      title: String(document && document.title || ''),
      bodyPreview: String(document && document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 1200),
      layoutRendered: !!(document && document.body && String(document.body.innerText || '').trim().length > 20),
      innerTextLength: document && document.body ? String(document.body.innerText || '').length : 0,
      textContentLength: document && document.body ? String(document.body.textContent || '').length : 0
    });
  }
})();"""


def buildGrokDomSendScript(message: str, sendId: str, *, maxTicks: int = 300, stablePolls: int = 3, target: object = "grok") -> str:
    safeMaxTicks = max(20, int(maxTicks or 300))
    safeStablePolls = max(1, int(stablePolls or 3))
    return rf"""(function() {{
  const __prefix = {json.dumps(CHAT_MODAL_EVENT_PREFIX)};
  const __message = {json.dumps(message, ensure_ascii=False)};
  const __sendId = {json.dumps(sendId)};
  const __target = {json.dumps(normalizeChatTarget(target))};
  const __maxTicks = {safeMaxTicks};
  const __stablePolls = {safeStablePolls};
  window.__SuperGrokChatLastEvent = '';
  window.__SuperGrokChatLastTrace = [];

  function __emit(payload) {{
    payload = payload || {{}};
    payload.sendId = __sendId;
    payload.at = new Date().toISOString();
    const encoded = JSON.stringify(payload);
    try {{ window.__SuperGrokChatLastEvent = encoded; }} catch (_ignored) {{}}
    try {{
      window.__SuperGrokChatLastTrace = window.__SuperGrokChatLastTrace || [];
      window.__SuperGrokChatLastTrace.push(encoded);
      if (window.__SuperGrokChatLastTrace.length > 150) window.__SuperGrokChatLastTrace.shift();
    }} catch (_ignored) {{}}
    try {{ console.log(__prefix + encoded); }} catch (_ignored) {{}}
  }}

  function __visible(el) {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }}

  function __queryAll(selectors, root) {{
    const out = [];
    const scope = root || document;
    for (const selector of selectors) {{
      try {{
        const nodes = Array.from(scope.querySelectorAll(selector));
        for (const node of nodes) if (__visible(node)) out.push({{ selector: selector, el: node }});
      }} catch (_ignored) {{}}
    }}
    return out;
  }}

  function __findEditorRow() {{
    /* Per-target selectors come first so we don't accidentally grab a
       hidden / wrong contenteditable. Generic fallbacks at the bottom. */
    const geminiSelectors = [
      'rich-textarea .ql-editor[contenteditable="true"]',
      'rich-textarea div.ql-editor',
      '.ql-editor[contenteditable="true"]',
      '.input-area-container .ql-editor'
    ];
    const chatgptSelectors = [
      '#prompt-textarea',
      'textarea#prompt-textarea',
      'div#prompt-textarea[contenteditable="true"]'
    ];
    /* Claude (claude.ai) uses a ProseMirror editor in the composer panel.
       data-testid="chat-input" + ProseMirror is the most stable combo today;
       expect refinement after first round-trip. */
    const claudeSelectors = [
      'div[data-testid="chat-input"] div.ProseMirror[contenteditable="true"]',
      'fieldset div.ProseMirror[contenteditable="true"]',
      'div.ProseMirror[contenteditable="true"]'
    ];
    const generic = [
      '[data-testid="composer"] [contenteditable="true"]',
      'form [contenteditable="true"]',
      'div.ProseMirror[contenteditable="true"]',
      'div[contenteditable="true"][role="textbox"]',
      'textarea',
      'div[contenteditable="true"]',
      '[role="textbox"]'
    ];
    let selectors;
    if (__target === 'gemini') selectors = geminiSelectors.concat(generic);
    else if (__target === 'chatgpt') selectors = chatgptSelectors.concat(generic);
    else if (__target === 'claude') selectors = claudeSelectors.concat(generic);
    else selectors = chatgptSelectors.concat(generic);
    const rows = __queryAll(selectors, document);
    return rows.length ? rows[0] : null;
  }}

  function __publicButtonInfo(info) {{
    const out = Object.assign({{}}, info || {{}});
    delete out.element;
    return out;
  }}

  function __buttonInfo(row, editor) {{
    if (!row || !row.el) return {{ found: false, looksLikeSend: false, enabled: false, hasSvg: false, excluded: false, score: 0, selector: '' }};
    const button = row.el;
    const aria = String(button.getAttribute('aria-label') || '');
    const title = String(button.getAttribute('title') || '');
    const testid = String(button.getAttribute('data-testid') || '');  # noqa: redundant
    const type = String(button.getAttribute('type') || '').toLowerCase();
    const text = String(button.innerText || button.textContent || '').trim();
    const classes = String(button.getAttribute('class') || '');
    const meta = (aria + ' ' + title + ' ' + testid + ' ' + text).toLowerCase();
    const classText = classes.toLowerCase();
    const htmlText = String(button.outerHTML || '').toLowerCase();
    const hasSvg = !!button.querySelector('svg');
    const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
    const promptForm = editor && editor.closest ? editor.closest('form') : null;
    const sameForm = !!(promptForm && button.closest && button.closest('form') === promptForm);
    const excluded = (
      /(^|\b)(attach|upload|file|files|add files|add files and more|composer-plus|model select|dictation|voice|microphone|mic|sidebar|search|project|history|menu|settings|extended|create an image|write or edit|look something up)(\b|$)/.test(meta)
      || /(^|[\s_\/-])(attach|attach-button|upload|dictation|voice|microphone|mic|model-select|sidebar|settings|composer-plus|composer-plus-btn|composer-pill)([\s_\/-]|$)/.test(classText)
      || htmlText.indexOf('group/attach-button') >= 0
      || htmlText.indexOf('composer-plus-btn') >= 0
      || htmlText.indexOf('__composer-pill') >= 0  # noqa: redundant
    );
    const positiveMeta = /(^|\b)(send|send message|send prompt|submit|submit message|composer-submit-button|enviar|arrow up|send-button|chat-submit)(\b|$)/.test(meta);
    const positiveClass = /(^|[\s_\/-])(send|send-button|submit|submit-button|composer-submit-button|chat-submit)([\s_\/-]|$)/.test(classText);
    let score = 0;
    if (sameForm) score += 45;
    if (positiveMeta) score += 150;
    if (positiveClass) score += 120;  # noqa: redundant
    if (testid.toLowerCase() === 'chat-submit') score += 260;
    if (['send-button','composer-submit-button'].indexOf(testid.toLowerCase()) >= 0) score += 280;
    if (String(button.id || '').toLowerCase() === 'composer-submit-button') score += 280;
    if (type === 'submit') score += 170;
    if (hasSvg) score += 25;
    if (excluded) score -= 800;
    if (disabled) score -= 250;
    const looksLikeSend = !disabled && !excluded && (score >= 100 || (sameForm && type === 'submit' && hasSvg));
    return {{
      found: true,
      selector: row.selector,
      enabled: !disabled,
      hasSvg: hasSvg,
      excluded: !!excluded,
      sameForm: !!sameForm,
      looksLikeSend: !!looksLikeSend,
      score: score,
      ariaLabel: aria,
      title: title,
      dataTestId: testid,
      type: type,
      text: text.slice(0, 80),
      htmlPreview: String(button.outerHTML || '').slice(0, 500),
      element: button
    }};
  }}

  function __nativeValueSet(el, value) {{
    const text = String(value == null ? '' : value);
    if (!el) return false;
    const tag = String(el.tagName || '').toLowerCase();
    try {{ el.focus(); }} catch (_ignored) {{}}
    try {{ el.click(); }} catch (_ignored) {{}}
    if (tag === 'textarea' || tag === 'input') {{
      let setter = null;
      try {{
        const proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
        setter = descriptor && descriptor.set;
      }} catch (_ignored) {{}}
      try {{
        const previous = String(el.value || '');
        if (setter) setter.call(el, text);
        else el.value = text;
        try {{
          const tracker = el._valueTracker;
          if (tracker && typeof tracker.setValue === 'function') tracker.setValue(previous);
        }} catch (_ignoredTracker) {{}}
      }} catch (_ignored) {{ el.value = text; }}
      try {{ el.setSelectionRange(text.length, text.length); }} catch (_ignored) {{}}
      return true;
    }}
    return false;
  }}

  function __dispatchPromptEvents(el, text) {{
    const value = String(text == null ? '' : text);
    const events = [
      new FocusEvent('focus', {{ bubbles: true, composed: true }}),
      new InputEvent('beforeinput', {{ bubbles: true, cancelable: true, composed: true, inputType: 'insertText', data: value }}),
      new InputEvent('input', {{ bubbles: true, composed: true, inputType: 'insertText', data: value }}),
      new Event('change', {{ bubbles: true, composed: true }}),
      new KeyboardEvent('keydown', {{ bubbles: true, cancelable: true, composed: true, key: ' ', code: 'Space', which: 32, keyCode: 32 }}),
      new KeyboardEvent('keyup', {{ bubbles: true, cancelable: true, composed: true, key: 'Unidentified', code: '', which: 0, keyCode: 0 }})
    ];
    for (const ev of events) {{
      try {{ el.dispatchEvent(ev); }} catch (_ignored) {{}}
    }}
  }}

  function __currentEditorText(editor) {{
    if (!editor) return '';
    const tag = String(editor.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input') return String(editor.value || '');
    return String(editor.innerText || editor.textContent || '');
  }}

  function __setPromptText(text) {{
    if (window.SuperGrokBridgeDom && typeof window.SuperGrokBridgeDom.setPromptText === 'function') {{
      const result = window.SuperGrokBridgeDom.setPromptText(text);
      const row = __findEditorRow();
      const editor = row && row.el;
      if (editor && (!result || result.ok === false || __currentEditorText(editor).indexOf(String(text || '')) < 0)) {{
        __nativeValueSet(editor, text);
        __dispatchPromptEvents(editor, text);
      }}
      const actual = __currentEditorText(editor);
      return Object.assign({{ editorSelector: row ? row.selector : '', actualLength: actual.length, expectedLength: String(text || '').length, preview: actual.slice(0, 120) }}, result || {{ ok: !!editor }});
    }}
    const row = __findEditorRow();
    const editor = row && row.el;
    const value = String(text == null ? '' : text);
    if (!editor) return {{ ok: false, missingSurface: 'prompt', error: 'prompt box not found; Grok may still be loading or may need login' }};
    try {{ editor.focus(); }} catch (_ignored) {{}}
    const tag = String(editor.tagName || '').toLowerCase();
    if (tag === 'textarea' || tag === 'input') {{
      __nativeValueSet(editor, value);
      __dispatchPromptEvents(editor, value);
    }} else {{
      try {{
        const selection = window.getSelection && window.getSelection();
        if (selection && document.createRange) {{
          const range = document.createRange();
          range.selectNodeContents(editor);
          selection.removeAllRanges();
          selection.addRange(range);
        }}
      }} catch (_ignored) {{}}
      try {{ document.execCommand('selectAll', false, null); }} catch (_ignored) {{}}
      try {{ document.execCommand('insertText', false, value); }} catch (_ignored) {{ editor.innerText = value; }}
      __dispatchPromptEvents(editor, value);
    }}
    const actual = __currentEditorText(editor);
    return {{ ok: actual.indexOf(value) >= 0 || value.length === 0, editorSelector: row ? row.selector : '', actualLength: actual.length, expectedLength: value.length, tagName: tag, preview: actual.slice(0, 120) }};
  }}

  function __findSendButton(editor) {{
    const selectors = [
      'button[data-testid="send-button"]',
      'button[data-testid="composer-submit-button"]',
      'button#composer-submit-button',
      'button[data-testid="chat-submit"]',
      'button[aria-label*="Send prompt" i]',
      'button[aria-label*="Send message" i]',
      'button[type="submit"][aria-label="Enviar"]',
      'button[aria-label*="enviar" i]',
      'button[aria-label*="send" i]',
      'button[aria-label*="submit" i]',
      'button[type="submit"]',
      'form button[type="submit"]',
      'form button',
      'button:has(svg)'
    ];
    const promptForm = editor && editor.closest ? editor.closest('form') : null;
    let rawRows = [];
    if (promptForm) rawRows = rawRows.concat(__queryAll(selectors, promptForm));
    rawRows = rawRows.concat(__queryAll(selectors, document));
    const seen = new Set();
    const rows = [];
    for (const row of rawRows) {{
      if (!row || !row.el || seen.has(row.el)) continue;
      seen.add(row.el);
      rows.push(__buttonInfo(row, editor));
    }}
    rows.sort(function(a, b) {{ return (b.score || 0) - (a.score || 0); }});
    const best = rows.length ? rows[0] : __buttonInfo(null, editor);
    return {{ best: best, candidates: rows.slice(0, 8) }};
  }}

  function __pressEnter(editor) {{
    if (!editor) return {{ ok: false, method: 'enter', error: 'no editor for enter fallback' }};
    editor.focus();
    const events = [
      new KeyboardEvent('keydown', {{ bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }}),
      new KeyboardEvent('keypress', {{ bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }}),
      new KeyboardEvent('keyup', {{ bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', which: 13, keyCode: 13 }})
    ];
    for (const ev of events) editor.dispatchEvent(ev);
    return {{ ok: true, method: 'enter' }};
  }}

  function __attemptSubmit(editor, attempt) {{
    const found = __findSendButton(editor);
    const info = found.best || {{ found: false }};
    const publicInfo = __publicButtonInfo(info);
    __emit({{ eventType: 'trace', stage: 'find-send-button', attempt: attempt, button: publicInfo, candidates: (found.candidates || []).map(__publicButtonInfo) }});
    function __trustedishClick(button) {{
      try {{ button.scrollIntoView({{ block: 'center', inline: 'center' }}); }} catch (_ignored) {{}}
      try {{ button.focus(); }} catch (_ignored) {{}}
      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {{
        try {{
          const ev = type.indexOf('pointer') === 0
            ? new PointerEvent(type, {{ bubbles: true, cancelable: true, composed: true, pointerId: 1, pointerType: 'mouse', isPrimary: true }})
            : new MouseEvent(type, {{ bubbles: true, cancelable: true, composed: true, view: window }});
          button.dispatchEvent(ev);
        }} catch (_ignored) {{}}
      }}
      try {{ button.click(); }} catch (_ignored) {{}}
    }}
    if (info.found && info.enabled && info.looksLikeSend && !info.excluded && info.element) {{
      __trustedishClick(info.element);
      return {{ ok: true, method: 'button', button: publicInfo }};
    }}
    const maxAttempts = (__target === 'chatgpt') ? 24 : 8;
    if (attempt < maxAttempts) {{
      if (info.found && info.excluded) __emit({{ eventType: 'warn', warning: 'ignoring non-send button candidate', button: publicInfo }});
      if (__target === 'chatgpt') {{
        try {{
          const currentText = __currentEditorText(editor || (__findEditorRow() && __findEditorRow().el));
          if (attempt === 1 || attempt % 4 === 0 || currentText.indexOf(__message) < 0) {{
            const retyped = __setPromptText(__message);
            __emit({{ eventType: 'trace', stage: 'chatgpt-refill-before-submit', attempt: attempt, currentTextLength: currentText.length, retyped: retyped }});
          }} else if (editor) {{
            __dispatchPromptEvents(editor, __message);
          }}
        }} catch (_ignoredRetype) {{}}
      }}
      window.setTimeout(function() {{
        const clicked = __attemptSubmit(editor || (__findEditorRow() && __findEditorRow().el), attempt + 1);
        if (clicked && clicked.pending) return;
        __afterSubmit(clicked);
      }}, (__target === 'chatgpt') ? 700 : 500);
      return {{ pending: true }};
    }}
    if (__target === 'chatgpt') {{
      __emit({{ eventType: 'error', ok: false, status: 'send-button-not-enabled', error: 'ChatGPT prompt text was filled but React did not enable a real send button; requestSubmit fallback is disabled because it clears the composer without sending. Qt trusted paste/Enter fallback should already have been attempted.', button: publicInfo, candidates: (found.candidates || []).map(__publicButtonInfo), editorTextLength: __currentEditorText(editor).length, editorPreview: __currentEditorText(editor).slice(0, 300) }});
      return {{ ok: false, method: 'none', error: 'ChatGPT send button not enabled' }};
    }}
    __emit({{ eventType: 'warn', warning: 'no trustworthy send button after retries; trying Enter key fallback', button: publicInfo, candidates: (found.candidates || []).map(__publicButtonInfo) }});
    return __pressEnter(editor);
  }}

  function __cleanCandidateText(text) {{
    return String(text || '').replace(/\s+/g, ' ').trim();
  }}

  function __stripPromptFromCandidate(text, messageText) {{
    let clean = __cleanCandidateText(text);
    const prompt = __cleanCandidateText(messageText);
    if (!clean || !prompt) return clean;
    if (clean === prompt) return '';
    if (clean.indexOf(prompt) === 0) clean = __cleanCandidateText(clean.slice(prompt.length));
    clean = clean.replace(/^you said:\s*/i, '');
    clean = clean.replace(/^chatgpt said:\s*/i, '');
    clean = clean.replace(/^grok said:\s*/i, '');  # noqa: redundant
    return __cleanCandidateText(clean);
  }}

  function __isBadAnswerCandidate(text, messageText) {{
    const clean = __cleanCandidateText(text);
    if (!clean) return true;
    const lowered = clean.toLowerCase();
    const prompt = __cleanCandidateText(messageText).toLowerCase();
    if (prompt && lowered === prompt) return true;
    const generic = new Set([
      'refer to the following content:',
      'refer to the following content',
      'refer to following content:',
      'refer to following content',
      'what do you want to know?',
      'what do you want to know',
      'message chatgpt',
      'ask anything',
      'what can i help with',
      'new chat',
      'fast',
      'private',
      'imagine',
      'sign in',
      'sign up'
    ]);
    if (generic.has(lowered)) return true;
    if (/^refer to (the )?following content:?$/i.test(clean)) return true;
    if (/^by messaging grok, you agree to/i.test(clean)) return true;
    if (/^chatgpt can make mistakes/i.test(clean)) return true;
    if (/^toggle sidebar\b/i.test(clean)) return true;
    return false;
  }}

  function __messages() {{
    const scope = document.querySelector('main') || document;
    const seen = new Set();
    const out = [];
    function pushText(text) {{
      let clean = __stripPromptFromCandidate(text, __message);
      if (!clean || seen.has(clean) || __isBadAnswerCandidate(clean, __message)) return;
      seen.add(clean);
      out.push(clean);
    }}
    function collect(selectors) {{
      for (const selector of selectors) {{
        let nodes = [];
        try {{ nodes = Array.from(scope.querySelectorAll(selector)); }} catch (_ignored) {{ nodes = []; }}
        for (const el of nodes) {{
          if (!__visible(el)) continue;
          try {{
            if (el.closest && el.closest('nav, aside, header, footer, [role="navigation"], [data-sidebar], button, a')) continue;
          }} catch (_ignored) {{}}
          pushText(el.innerText || el.textContent || '');
        }}
      }}
    }}
    if (__target === 'chatgpt') {{
      collect([
        '[data-message-author-role="assistant"] .markdown',
        '[data-message-author-role="assistant"] [class*="markdown" i]',
        '[data-message-author-role="assistant"]',
        '[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]',
        '[data-testid^="conversation-turn-"] .markdown',
        '[data-testid*="conversation-turn" i] [class*="markdown" i]',
        'article[data-testid^="conversation-turn-"]',
        'main article',
        'main [class*="markdown" i]',
        'main [data-testid*="message" i]'
      ]);
      return out;
    }}
    if (__target === 'gemini') {{
      /* Gemini (gemini.google.com) Angular DOM. Selectors are educated guesses
         based on observed DOM patterns; expect to refine after first round-trip
         test. The "model-response-text" / message-content classes have been
         stable for ~12+ months but Google renames them in waves. */
      collect([
        'message-content .markdown',
        'model-response message-content',
        '.model-response-text',
        '[data-test-id="model-response-text"]',
        '.conversation-container .response-container .markdown',
        '.response-container-content .markdown',
        'message-content[data-test-id*="response" i]',
        '.markdown.markdown-main-panel'
      ]);
      return out;
    }}
    if (__target === 'claude') {{
      /* Claude (claude.ai) — Claude wraps assistant turns in elements that carry
         the "font-claude-message" or "font-claude-response" class, and Claude's
         own copy buttons live inside [data-testid="action-bar"]. Whitebox: the
         font-* classes have been stable through several DOM refactors; the
         data-testid surface is newer. */
      collect([
        '[data-testid="message"] .font-claude-message',
        '.font-claude-response',
        '.font-claude-message',
        '[data-testid*="assistant" i] .markdown',
        '[data-test-render-count] .font-claude-message',
        'div.font-claude-message .grid-cols-1',
        '[data-is-streaming]'
      ]);
      return out;
    }}
    try {{
      if (window.SuperGrokBridgeDom && typeof window.SuperGrokBridgeDom.messages === 'function') {{
        window.SuperGrokBridgeDom.messages().forEach(function(item) {{ pushText(item && item.text); }});
        if (out.length) return out;
      }}
    }} catch (_ignored) {{}}
    collect(['[data-message-author-role="assistant"], [data-testid*="conversation-turn" i], article, [data-testid*="message" i], [class*="response" i], [class*="answer" i]']);
    return out;
  }}

  function __bestCandidate(beforeTexts, messageText) {{
    const before = new Set((beforeTexts || []).map(__cleanCandidateText).filter(Boolean));
    const current = __messages();
    /* Only consider candidates whose CLEANED TEXT wasn't in the pre-submit set.
       Works whether the page adds a new DOM node (count grows) or updates an
       existing assistant node in place (count stays, text changes — Gemini's
       pattern). The original code fell back to "return the latest message" when
       fresh was empty, which caused an off-by-one bug: each turn returned the
       PRIOR reply. Returning empty here lets polling continue until the new text
       genuinely appears. */
    const fresh = [];
    for (let i = current.length - 1; i >= 0; i--) {{
      const text = __cleanCandidateText(current[i]);
      if (!text) continue;
      if (__isBadAnswerCandidate(text, messageText)) continue;
      if (!before.has(text)) fresh.push(text);
    }}
    if (fresh.length) {{
      fresh.sort(function(a, b) {{ return b.length - a.length; }});
      return fresh[0];
    }}
    return '';
  }}

  function __startResponsePolling(beforeTexts, started, editor, clicked) {{
    let last = '';
    let stable = 0;
    let ticks = 0;
    let lastEditorText = '';
    const timer = window.setInterval(function() {{
      ticks += 1;
      const pageTextRaw = String(document.body && (document.body.innerText || document.body.textContent) || '');
      const pageText = pageTextRaw.toLowerCase();
      const authBlocked = /\b(continue with google|auth.openai.com|authentication required|verification|captcha|sign in to continue|log in to continue|login to continue|please sign in|please log in|session expired|access denied)\b/.test(pageText) || /\/(login|signin|auth|oauth|captcha)(\b|[/?#])/.test(String(location.href || '').toLowerCase());
      if (authBlocked) {{
        window.clearInterval(timer);
        __emit({{ eventType: 'error', ok: false, status: 'auth-blocked', error: (__target === 'chatgpt' ? 'ChatGPT requested login/auth/captcha after submit' : 'Grok requested login/auth/captcha after submit'), durationMs: Date.now() - started, answer: '', responseId: '', rawLength: 0, url: location.href, bodyPreview: pageTextRaw.slice(0, 1000) }});
        return;
      }}
      const candidate = __bestCandidate(beforeTexts, __message);
      if (candidate && candidate === last) stable += 1;
      else {{ last = candidate; stable = candidate ? 1 : 0; }}
      if (ticks % 5 === 0) {{
        lastEditorText = __currentEditorText(editor || (__findEditorRow() && __findEditorRow().el));
        const currentCandidates = __messages();
        const tracePayload = {{ eventType: 'trace', stage: 'poll-response', target: __target, ticks: ticks, stable: stable, preview: String(last || '').slice(0, 240), candidateCount: currentCandidates.length, candidates: currentCandidates.slice(-6).map(function(item) {{ return String(item || '').slice(0, 360); }}), editorTextLength: lastEditorText.length, editorStillContainsPrompt: lastEditorText.indexOf(__message) >= 0, clickMethod: clicked && clicked.method, bodyPreview: String(document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 2000) }};
        if (ticks % 20 === 0 && document.documentElement) tracePayload.documentHtmlPreview = String(document.documentElement.outerHTML || '').slice(0, 25000);
        __emit(tracePayload);
      }}
      if (last && stable >= __stablePolls) {{
        window.clearInterval(timer);
        __emit({{ eventType: 'complete', ok: true, status: 'dom', durationMs: Date.now() - started, answer: last, responseId: '', rawLength: String(last).length, url: location.href }});
      }} else if (ticks === 25) {{
        lastEditorText = __currentEditorText(editor || (__findEditorRow() && __findEditorRow().el));
        if (lastEditorText.indexOf(__message) >= 0 && !last) {{
          window.clearInterval(timer);
          __emit({{ eventType: 'error', ok: false, status: 'submit-not-accepted', error: (__target === 'chatgpt' ? 'ChatGPT prompt was filled but did not submit; prompt text is still in the editor and no response stream started' : 'Grok prompt was filled but did not submit; prompt text is still in the editor and no response stream started'), durationMs: Date.now() - started, answer: '', responseId: '', rawLength: 0, url: location.href, editorTextLength: lastEditorText.length, editorPreview: lastEditorText.slice(0, 300), clicked: clicked }});
        }}
      }} else if (ticks >= __maxTicks) {{
        window.clearInterval(timer);
        __emit({{ eventType: last ? 'complete' : 'error', ok: !!last, status: 'dom-timeout', error: last ? '' : (__target === 'chatgpt' ? 'timed out waiting for visible ChatGPT response' : 'timed out waiting for visible Grok response'), durationMs: Date.now() - started, answer: last || '', responseId: '', rawLength: String(last || '').length, url: location.href, editorTextLength: __currentEditorText(editor || (__findEditorRow() && __findEditorRow().el)).length }});
      }}
    }}, 650);
  }}

  let __beforeTexts = [];
  let __started = Date.now();
  function __afterSubmit(clicked) {{
    __emit({{ eventType: 'trace', stage: 'after-click', clicked: clicked }});
    if (clicked && clicked.ok !== false) {{
      try {{ window.__SuperGrokChatSubmitAccepted = clicked; }} catch (_ignoredAccepted) {{}}
    }}
    if (!clicked || clicked.ok === false) {{
      __emit({{ eventType: 'error', missingSurface: (clicked && clicked.missingSurface) || 'send-button', error: (clicked && clicked.error) || (__target === 'chatgpt' ? 'failed to click ChatGPT submit button' : 'failed to click Grok submit button'), durationMs: Date.now() - __started, url: location.href, button: clicked && clicked.button }});
      return;
    }}
    __startResponsePolling(__beforeTexts, __started, (__findEditorRow() && __findEditorRow().el), clicked);
  }}

  (function() {{
    __started = Date.now();
    __beforeTexts = __messages();
    __emit({{ eventType: 'trace', stage: 'before-fill', messageLength: __message.length, beforeCount: __beforeTexts.length, url: location.href, jqueryPresent: !!window.jQuery }});
    const editorRow = __findEditorRow();
    const editor = editorRow && editorRow.el;
    const typed = __setPromptText(__message);
    __emit({{ eventType: 'trace', stage: 'after-fill', typed: typed, editorSelector: editorRow ? editorRow.selector : '' }});
    if (!typed || typed.ok === false) {{
      __emit({{ eventType: 'error', missingSurface: (typed && typed.missingSurface) || 'prompt', error: (typed && typed.error) || 'failed to fill prompt box', durationMs: Date.now() - __started, url: location.href }});
      return;
    }}
    window.setTimeout(function() {{
      const clicked = __attemptSubmit(editor || (__findEditorRow() && __findEditorRow().el), 1);
      if (clicked && clicked.pending) return;
      __afterSubmit(clicked);
    }}, (__target === 'chatgpt') ? 1100 : 550);
  }})();
  return 'GrokChat DOM send started: ' + __sendId;
}})();"""


class GrokBridgeChatJob(QObject):
    """Headless/service chat operation that reuses the live Grok page DOM."""

    def __init__(self, page: QWebEnginePage, message: str, debugPane: Any, timeoutSeconds: int, callback: Callable[[dict[str, Any]], None], parent: QObject | None = None, attachments: object = None, target: object = "grok") -> None:
        super().__init__(parent)
        self.page = page
        self.target = normalizeChatTarget(target)
        self.providerLabel = chatProviderLabel(self.target)  # noqa: nonconform
        self.rawMessage = str(message or "")  # noqa: nonconform
        self.attachments = attachments if isinstance(attachments, list) else []  # noqa: nonconform
        self.message = bridgeAttachmentPromptText(self.rawMessage, self.attachments)  # noqa: nonconform
        self.debugPane = debugPane  # noqa: nonconform
        self.timeoutSeconds = max(30, int(timeoutSeconds or 240))  # noqa: nonconform
        self.callback = callback
        self.sendId = f"cli-{int(time.time() * 1000)}"  # noqa: nonconform
        self.startedAt = time.monotonic()  # noqa: nonconform
        self.lastRaw = ""  # noqa: nonconform
        self.done = False  # noqa: nonconform
        self.surfaceAttempts = 0  # noqa: nonconform
        self.reloadAttempts = 0  # noqa: nonconform
        self.stage = "created"  # noqa: redundant  # noqa: nonconform
        self.lastReason = ""  # noqa: redundant  # noqa: nonconform
        self.lastProbeSummary: dict[str, Any] = {}
        self.lastTraceAt = 0.0  # noqa: nonconform
        self.compositorKickAttempts = 0  # noqa: nonconform
        self.domSendStarted = False  # noqa: nonconform
        self.maxSurfaceAttempts = 3  # noqa: nonconform
        self.maxReloadAttempts = 1  # noqa: nonconform
        self.pollTimer = QTimer(self)  # noqa: nonconform
        self.pollTimer.setInterval(500)
        self.pollTimer.timeout.connect(self.pollChatEvent)

    def begin(self) -> None:
        self.stage = "begin"
        self.lastReason = "starting CLI chat"
        self.log("grokchat", f"{self.sendId}: begin {self.providerLabel} CLI chat chars={len(self.message)} attachments={len(self.attachments)} timeout={self.timeoutSeconds}s")
        self.probeBeforeSend()

    def log(self, kind: str, text: str) -> None:
        try:
            debugLog(str(kind or "grokchat"), text)
            if self.debugPane is not None and hasattr(self.debugPane, "append"):
                self.debugPane.append(kind, text)
            if str(kind).lower() in {"warn", "warning", "error"}:
                warnLog(text)
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.log", error, extra={"handler": "except Exception as error:"})

    def currentUrl(self) -> str:
        try:
            return self.page.url().toString()
        except Exception:  # swallow-ok
            return ""

    def probeBeforeSend(self) -> None:
        if self.done:
            return
        if (time.monotonic() - self.startedAt) > self.timeoutSeconds:
            self.revealAndFinish(f"timed out waiting for {self.providerLabel} DOM surfaces", probe={})
            return
        self.surfaceAttempts += 1
        self.stage = "surface-probe"
        self.lastReason = f"surface probe attempt={self.surfaceAttempts} reloads={self.reloadAttempts}"
        self.log("grokchat", f"{self.sendId}: surface probe attempt={self.surfaceAttempts} reloads={self.reloadAttempts} url={self.currentUrl()}")
        try:
            self.page.runJavaScript(buildGrokDomSurfaceProbeScript(self.target), lambda result: self.handleSurfaceProbe(result))
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.probeBeforeSend", error, extra={"handler": "except Exception as error:"})
            self.finish({"ok": False, "error": f"{type(error).__name__}: {error}", "sendId": self.sendId})

    def handleSurfaceProbe(self, result: Any) -> None:
        if self.done:
            return
        probe = normalizeProbePayload(result)
        self.lastProbeSummary = dict(probe or {})
        self.lastReason = str(probe.get("reason") or self.lastReason or "surface probe returned")
        self.log("grokchat", f"{self.sendId}: surface probe {json.dumps(probe, ensure_ascii=False, default=str)[:4000]}")
        if not probe.get("layoutRendered", True):
            self.log("warn", f"{self.sendId}: {self.providerLabel} DOM loaded but layout is not rendered/composited; innerTextLength={probe.get('innerTextLength')} textContentLength={probe.get('textContentLength')} viewport={probe.get('viewportWidth')}x{probe.get('viewportHeight')} url={probe.get('url')}")
        if not probe.get("promptFound"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} prompt text area not found; reason={probe.get('reason')} promptCandidates={probe.get('promptCandidateCount')} url={probe.get('url')}")
        if not probe.get("sendButtonFound"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} send button not found; reason={probe.get('reason')} url={probe.get('url')}")
        elif not probe.get("sendButtonEnabled"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} send button exists but is disabled; button={json.dumps(probe.get('sendButton'), ensure_ascii=False, default=str)[:1000]}")
        elif not probe.get("sendButtonHasSvg"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} send button does not contain an SVG arrow/icon; button={json.dumps(probe.get('sendButton'), ensure_ascii=False, default=str)[:1000]}")
        elif not probe.get("sendButtonLooksLikeSend"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} candidate button does not look like a send button; button={json.dumps(probe.get('sendButton'), ensure_ascii=False, default=str)[:1000]}")
        if probe.get("loginLikely"):
            self.log("warn", f"{self.sendId}: {self.providerLabel} page looks like login/auth/captcha; url={probe.get('url')} title={probe.get('title')}")
        if not probe.get("jqueryPresent"):
            self.log("grokchat", f"{self.sendId}: jQuery not present in {self.providerLabel} page; native DOM probe is being used")
        if not probe.get("layoutRendered", True):
            self.tryCompositorKick(probe)
        if probe.get("ok"):
            self.runDomSend()
            return
        if self.surfaceAttempts < self.maxSurfaceAttempts:
            delayMs = 2000 + (self.surfaceAttempts * 1000)
            self.log("warn", f"{self.sendId}: {self.providerLabel} DOM surface not ready ({probe.get('reason')}); retrying in {delayMs}ms")
            QTimer.singleShot(delayMs, self.probeBeforeSend)
            return
        if self.reloadAttempts < self.maxReloadAttempts:
            self.reloadAttempts += 1
            self.surfaceAttempts = 0
            self.log("warn", f"{self.sendId}: {self.providerLabel} DOM surface still missing; reloading page once and retrying")
            try:
                self.page.loadFinished.connect(self._afterReloadProbe)
            except Exception:  # swallow-ok
                pass
            self.page.triggerAction(QWebEnginePage.WebAction.Reload)
            QTimer.singleShot(12000, self.probeBeforeSend)
            return
        self.revealAndFinish(str(probe.get("reason") or f"{self.providerLabel} DOM surface missing after retries/reload"), probe=probe)

    def tryCompositorKick(self, probe: dict[str, Any]) -> None:
        """Nudge QtWebEngine into a real composited surface without showing a normal repair window.

        Windows Chromium sometimes reports a loaded document while React has not
        painted into the widget yet.  Keeping the window fully transparent can
        be enough to suppress composition.  This kick keeps the bridge behind
        the user's windows, but makes the surface real/opaque long enough for
        Chromium to hydrate the page.
        """
        if self.compositorKickAttempts >= 2:
            return
        self.compositorKickAttempts += 1
        self.stage = "compositor-kick"
        self.lastReason = str(probe.get("reason") or "layout not composited")
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "kickCompositorForCli"):
                parent.kickCompositorForCli(reason=f"{self.sendId}: {self.lastReason}")
                self.log("warn", f"{self.sendId}: requested compositor kick attempt={self.compositorKickAttempts} because layoutRendered=false")
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.tryCompositorKick", error, extra={"handler": "except Exception as error:"})

    @Slot(bool)
    def _afterReloadProbe(self, ok: bool) -> None:
        try:
            self.page.loadFinished.disconnect(self._afterReloadProbe)
        except Exception:  # swallow-ok
            pass
        self.log("grokchat", f"{self.sendId}: reload finished ok={bool(ok)}; waiting briefly before surface probe")
        QTimer.singleShot(2500, self.probeBeforeSend)

    def runDomSend(self) -> None:
        if self.done:
            return
        if self.domSendStarted:
            self.log("warn", f"{self.sendId}: DOM send bootstrap already started; ignoring duplicate probe/reload callback")
            return
        self.domSendStarted = True
        try:
            self.stage = "dom-send"
            self.lastReason = "DOM surfaces ready; sending prompt"
            maxTicks = max(45, int((self.timeoutSeconds * 1000) / 650))
            self.pollTimer.start()
            self.log("grokchat", f"{self.sendId}: DOM surfaces ready; sending prompt")
            self.page.runJavaScript(
                buildGrokDomSendScript(self.message, self.sendId, maxTicks=maxTicks, target=self.target),
                lambda result: self.log("grokchat", f"{self.sendId}: JS bootstrap returned: {result}"),
            )
            if self.target == "chatgpt":
                QTimer.singleShot(5200, self.tryChatGptTrustedInputFallback)
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.runDomSend", error, extra={"handler": "except Exception as error:"})
            self.finish({"ok": False, "error": f"{type(error).__name__}: {error}", "sendId": self.sendId})

    @Slot()
    def pollChatEvent(self) -> None:
        if self.done:
            self.pollTimer.stop()
            return
        elapsed = time.monotonic() - self.startedAt
        if elapsed > self.timeoutSeconds:
            self.revealAndFinish(f"timed out after {self.timeoutSeconds}s waiting for chat service response", probe={})
            return
        self.stage = "poll-response"
        if elapsed - self.lastTraceAt >= 5.0:
            self.lastTraceAt = elapsed
            self.log("grokchat", f"{self.sendId}: polling Grok response elapsed={elapsed:.1f}s lastReason={self.lastReason}")
        self.page.runJavaScript("window.__SuperGrokChatLastEvent || ''", lambda raw: self.handlePolledChatEvent(raw))

    def handlePolledChatEvent(self, raw: Any) -> None:
        if self.done or not raw:
            return
        rawText = str(raw)
        if rawText == self.lastRaw:
            return
        self.lastRaw = rawText
        try:
            payload = json.loads(rawText)
            if not isinstance(payload, dict):
                return
            if str(payload.get("sendId") or "") != self.sendId:
                return
            eventType = str(payload.get("eventType") or "complete")
            if eventType == "trace":
                self.log("grokchat", json.dumps(payload, ensure_ascii=False))
                return
            if eventType == "warn":
                self.log("warn", json.dumps(payload, ensure_ascii=False))
                return
            if eventType == "complete" and payload.get("ok"):
                answer = str(payload.get("answer") or "").strip()
                if isBadGrokAnswerCandidate(answer, self.message):
                    self.log("warn", f"{self.sendId}: ignored generic/wrapper DOM answer candidate: {answer!r}; waiting for network stream or better DOM text")
                    return
                toolCalls = executeCliToolCallsFromAnswer(answer, timeoutSeconds=min(self.processTtlSecondsForTools(), self.timeoutSeconds))
                attachments = extractReturnedAttachmentsFromAnswer(answer)
                self.finish({"ok": True, "answer": answer, "toolCalls": toolCalls, "attachments": attachments, "sendId": self.sendId, "payload": payload})
            else:
                self.revealAndFinish(str(payload.get("error") or "unknown Grok bridge error"), probe=payload)
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.handlePolledChatEvent", error, extra={"handler": "except Exception as error:"})
            self.log("error", f"{self.sendId}: CLI chat event parse failed: {type(error).__name__}: {error}")

    def finishFromNetworkAnswer(self, answer: str, *, responseId: str = "", url: str = "") -> bool:
        """Complete a CLI chat from the captured response stream.

        DOM polling is fragile because Grok can render responses through React
        surfaces that are not simple <article> text. The fetch/XHR hook already
        captures the real /responses stream, so use that as the authoritative
        CLI answer when it appears while this job is active.
        """
        if self.done:
            return False
        clean = str(answer or "").strip()
        if not clean or isBadGrokAnswerCandidate(clean, self.message):
            if clean:
                self.log("warn", f"{self.sendId}: ignored generic/wrapper network answer candidate: {clean!r}")
            return False
        self.log("grokchat", f"{self.sendId}: network response stream produced answer chars={len(clean)} responseId={responseId}")
        toolCalls = executeCliToolCallsFromAnswer(clean, timeoutSeconds=min(self.processTtlSecondsForTools(), self.timeoutSeconds))
        attachments = extractReturnedAttachmentsFromAnswer(clean)
        self.finish({
            "ok": True,
            "answer": clean,
            "toolCalls": toolCalls,
            "attachments": attachments,
            "sendId": self.sendId,
            "source": "network-response-stream",
            "responseId": str(responseId or ""),
            "url": str(url or self.currentUrl()),
        })
        return True

    def tryChatGptTrustedInputFallback(self) -> None:
        # Use Qt/browser-level paste+Enter for ChatGPT only when synthetic DOM events did not click a real send button.
        if self.done or self.target != "chatgpt":
            return
        probeScript = r"""
(function() {
  function textOf(el) { return String((el && (el.value || el.innerText || el.textContent)) || ''); }
  let editor = null;
  for (const sel of ['#prompt-textarea', 'textarea#prompt-textarea', 'textarea', '[contenteditable="true"][role="textbox"]', '[contenteditable="true"]', '[role="textbox"]']) {
    try { editor = Array.from(document.querySelectorAll(sel)).find(function(el) {
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
    }); } catch (_ignored) { editor = null; }
    if (editor) break;
  }
  const assistantCount = document.querySelectorAll('[data-message-author-role="assistant"], [data-testid^="conversation-turn-"] [data-message-author-role="assistant"]').length;
  return JSON.stringify({
    ok: true,
    accepted: !!window.__SuperGrokChatSubmitAccepted,
    acceptedMethod: window.__SuperGrokChatSubmitAccepted && window.__SuperGrokChatSubmitAccepted.method || '',
    lastEvent: String(window.__SuperGrokChatLastEvent || '').slice(0, 500),
    assistantCount: assistantCount,
    editorTextLength: textOf(editor).length,
    editorPreview: textOf(editor).slice(0, 200),
    url: location.href,
    title: document.title
  });
})();
"""
        def afterProbe(raw: Any) -> None:
            if self.done or self.target != "chatgpt":
                return
            try:
                probe = json.loads(str(raw or "{}")) if not isinstance(raw, dict) else raw
            except Exception:  # swallow-ok
                probe = {"ok": False, "raw": str(raw or "")[:500]}
            try:
                if bool(probe.get("accepted")):
                    self.log("grokchat", f"{self.sendId}: skipping ChatGPT trusted input fallback because DOM reported submit accepted method={probe.get('acceptedMethod')}")
                    return
                parent = self.parent()
                if parent is None or not hasattr(parent, "sendTrustedChatGptInput"):
                    self.log("warn", f"{self.sendId}: ChatGPT Qt trusted input fallback unavailable; parent has no sendTrustedChatGptInput probe={json.dumps(probe, ensure_ascii=False)[:800]}")
                    return
                self.log("grokchat", f"{self.sendId}: attempting ChatGPT Qt trusted paste/Enter fallback probe={json.dumps(probe, ensure_ascii=False)[:800]}")
                parent.sendTrustedChatGptInput(self.message, self.sendId, self.log)
            except Exception as error:
                recordException("supergrok_bridge/app.py:GrokBridgeChatJob.tryChatGptTrustedInputFallback.afterProbe", error, extra={"handler": "except Exception as error:"})
                self.log("error", f"{self.sendId}: ChatGPT trusted input fallback failed after probe: {type(error).__name__}: {error}")
        try:
            self.page.runJavaScript(probeScript, afterProbe)
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.tryChatGptTrustedInputFallback", error, extra={"handler": "except Exception as error:"})
            self.log("error", f"{self.sendId}: ChatGPT trusted input fallback probe failed: {type(error).__name__}: {error}")

    def processTtlSecondsForTools(self) -> int:
        try:
            parent = self.parent()
            config = getattr(parent, "config", None)
            return max(1, int(getattr(config, "processTtlSeconds", PROCESS_DEFAULT_TTL_SECONDS) or PROCESS_DEFAULT_TTL_SECONDS))
        except Exception:  # swallow-ok
            return PROCESS_DEFAULT_TTL_SECONDS

    def revealAndFinish(self, errorText: str, *, probe: dict[str, Any]) -> None:
        shown = False
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "revealForHumanRepair"):
                parent.revealForHumanRepair(str(errorText or f"{self.providerLabel} bridge needs attention"))
                shown = True
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.revealAndFinish", error, extra={"handler": "except Exception as error:"})
        self.finish({
            "ok": False,
            "eventType": "error",
            "sendId": self.sendId,
            "error": str(errorText or f"{self.providerLabel} bridge failed"),
            "hint": f"{self.providerLabel} may need login, captcha, or DOM selector repair. A visible bridge window was requested so the user can fix it. Run: python start.py --serve-bridge --show-bridge --target {self.target}",
            "shownForRepair": shown,
            "probe": probe,
        })

    def finish(self, response: dict[str, Any]) -> None:
        if self.done:
            return
        self.stage = "finished"
        self.lastReason = str(response.get("error") or ("ok" if response.get("ok") else "finished"))
        self.done = True
        self.pollTimer.stop()
        response.setdefault("sendId", self.sendId)
        response.setdefault("elapsedSeconds", round(time.monotonic() - self.startedAt, 3))
        try:
            sessionLog({
                "eventType": "grok-cli-chat",
                "sendId": self.sendId,
                "ok": bool(response.get("ok")),
                "url": self.currentUrl(),
                "target": self.target,
                "request": {"message": self.message},
                "response": {
                    "answer": str(response.get("answer") or ""),
                    "error": str(response.get("error") or ""),
                    "payload": response,
                },
            })
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.finish.sessionLog", error, extra={"handler": "except Exception as error:"})
        try:
            self.callback(response)
        except Exception as error:
            recordException("supergrok_bridge/app.py:GrokBridgeChatJob.finish", error, extra={"handler": "except Exception as error:"})


class BridgeCommandServer(QObject):
    """Qt-owned localhost JSON-line server for resident Grok Bridge CLI calls."""

    def __init__(self, window: Any, port: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.window = window  # noqa: nonconform
        self.port = int(port or BRIDGE_SERVICE_PORT)  # noqa: nonconform
        self.server = QTcpServer(self)  # noqa: nonconform
        self.startedAt = time.time()  # noqa: nonconform
        self.server.newConnection.connect(self.acceptPendingConnections)
        self.buffers: dict[int, bytes] = {}
        self.sockets: dict[int, QTcpSocket] = {}
        self.pendingChatSockets: set[int] = set()
        self.chatResults: dict[str, dict[str, Any]] = {}
        self.chatStarted: dict[str, float] = {}
        self.currentRequestIds: dict[int, str] = {}

    def start(self) -> bool:
        if self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), self.port):
            self.log("bridge-service", f"listening on {BRIDGE_SERVICE_HOST}:{self.server.serverPort()}")
            emitDebuggerHeartbeat(eventKind="bridge-service", reason="listening", caller="BridgeCommandServer.start", varDump={"port": int(self.server.serverPort())})
            return True
        error = self.server.errorString()
        self.log("error", f"bridge service listen failed on {BRIDGE_SERVICE_HOST}:{self.port}: {error}")
        emitDebuggerHeartbeat(eventKind="bridge-service-error", reason=error, caller="BridgeCommandServer.start", varDump={"port": self.port})
        return False

    def log(self, kind: str, text: str) -> None:
        try:
            debugLog(str(kind or "bridge-service"), text)
            debugPane = getattr(self.window, "debugPane", None)
            if debugPane is not None and hasattr(debugPane, "append"):
                debugPane.append(kind, text)
            if getattr(getattr(self.window, "config", None), "debug", False) or str(kind).lower() in {"warn", "warning", "error"}:
                print(f"[BRIDGE-SERVICE:{kind}] {text}", file=sys.stderr, flush=True)
        except Exception as error:
            recordException("supergrok_bridge/app.py:BridgeCommandServer.log", error, extra={"handler": "except Exception as error:"})

    @Slot()
    def acceptPendingConnections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            key = id(socket)
            socket.setParent(self)
            self.sockets[key] = socket
            self.buffers[key] = b""
            socket.readyRead.connect(lambda sock=socket: self.readSocket(sock))
            socket.disconnected.connect(lambda sock=socket: self.dropSocket(sock))

    @Slot()
    def readSocket(self, socket: QTcpSocket) -> None:
        key = id(socket)
        try:
            chunk = bytes(socket.readAll().data())
            if not chunk:
                return
            data = self.buffers.get(key, b"") + chunk
            while b"\n" in data:
                line, data = data.split(b"\n", 1)
                if line.strip():
                    self.handleLine(socket, line.decode("utf-8", "replace"))
            self.buffers[key] = data
        except Exception as error:
            recordException("supergrok_bridge/app.py:BridgeCommandServer.readSocket", error, extra={"handler": "except Exception as error:"})
            self.respond(socket, {"ok": False, "error": f"{type(error).__name__}: {error}"})

    def dropSocket(self, socket: QTcpSocket) -> None:
        key = id(socket)
        if key in self.pendingChatSockets:
            self.log("warn", f"chat response socket disconnected before job finished; keeping job alive key={key}")
            return
        self.buffers.pop(key, None)
        self.sockets.pop(key, None)
        self.currentRequestIds.pop(key, None)  # noqa: redundant
        try:
            socket.deleteLater()
        except RuntimeError:  # swallow-ok
            pass

    def peerSummary(self, socket: QTcpSocket) -> dict[str, Any]:
        try:
            return {"address": socket.peerAddress().toString(), "port": int(socket.peerPort())}
        except Exception as error:  # swallow-ok
            return {"error": f"{type(error).__name__}: {error}"}

    def logBridgeRequest(self, socket: QTcpSocket, line: str, request: dict[str, Any] | None = None, error: Exception | None = None) -> str:
        requestId = f"bridge-server-{int(time.time() * 1000)}-{id(socket)}"
        payload: dict[str, Any] = {
            "eventType": "bridge-server-request",
            "requestId": requestId,
            "direction": "client-to-bridge",
            "peer": self.peerSummary(socket),
            "request": {"headers": {}, "rawLine": line, "body": request if request is not None else None},
        }
        if error is not None:
            payload["error"] = f"{type(error).__name__}: {error}"
            payload["traceback"] = traceback.format_exc()
        debugJson("bridge-server-request", payload)
        sessionLog(payload)
        return requestId

    def logBridgeResponse(self, socket: QTcpSocket, payload: dict[str, Any], requestId: str | None = None, error: Exception | None = None) -> None:
        event: dict[str, Any] = {
            "eventType": "bridge-server-response",
            "requestId": requestId or f"bridge-server-{int(time.time() * 1000)}-{id(socket)}",
            "direction": "bridge-to-client",
            "peer": self.peerSummary(socket),
            "response": {"headers": {}, "body": payload, "bodyText": safeJson(payload)},
        }
        if error is not None:
            event["error"] = f"{type(error).__name__}: {error}"
            event["traceback"] = traceback.format_exc()
        debugJson("bridge-server-response", event)
        sessionLog(event)

    def handleLine(self, socket: QTcpSocket, line: str) -> None:
        requestId = ""
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            requestId = self.logBridgeRequest(socket, line, request=request)
            self.currentRequestIds[id(socket)] = requestId
        except Exception as error:  # swallow-ok
            requestId = self.logBridgeRequest(socket, line, request=None, error=error)
            self.currentRequestIds[id(socket)] = requestId
            self.respond(socket, {"ok": False, "error": f"invalid JSON request: {type(error).__name__}: {error}"}, requestId=requestId)
            return
        action = str(request.get("action") or "status").strip().lower()
        if action == "status":
            self.respond(socket, self.statusPayload())
            return
        if action == "shutdown":
            reason = str(request.get("reason") or "requested")
            self.log("bridge-service", f"shutdown requested: {reason}")
            self.respond(socket, {"ok": True, "action": "shutdown", "reason": reason})
            QTimer.singleShot(100, QApplication.instance().quit)
            return
        if action in {"show", "reveal"}:
            reason = str(request.get("reason") or "requested")
            shown = False
            try:
                if hasattr(self.window, "revealForHumanRepair"):
                    self.window.revealForHumanRepair(reason)
                    shown = True
            except Exception as error:
                recordException("supergrok_bridge/app.py:BridgeCommandServer.show", error, extra={"handler": "except Exception as error:"})
            self.respond(socket, {"ok": shown, "action": action, "shown": shown, "reason": reason})
            return
        if action in {"chat-result", "chat-status"}:
            jobId = str(request.get("jobId") or "").strip()
            if not jobId:
                self.respond(socket, {"ok": False, "error": "jobId is required"})
                return
            if jobId in self.chatResults:
                result = self.chatResults.pop(jobId)
                self.chatStarted.pop(jobId, None)
                self.respond(socket, {"ok": True, "pending": False, "jobId": jobId, "result": result})
                return
            if jobId in self.chatStarted:
                self.respond(socket, {"ok": True, "pending": True, "jobId": jobId, "elapsedSeconds": round(max(0.0, time.time() - self.chatStarted.get(jobId, time.time())), 3), "progress": self.window.activeBridgeChatProgress() if hasattr(self.window, "activeBridgeChatProgress") else {}})
                return
            self.respond(socket, {"ok": False, "error": f"unknown chat job {jobId!r}", "jobId": jobId})
            return
        if action == "chat":
            target = normalizeChatTarget(request.get("target") or getattr(getattr(self.window, "config", None), "target", "grok"))
            if target not in {"grok", "chatgpt", "gemini", "claude"}:
                self.respond(socket, {"ok": False, "error": f"unsupported target {target!r}; expected grok, chatgpt, gemini, or claude"})
                return
            message = str(request.get("message") or "")
            attachments = request.get("attachments") if isinstance(request.get("attachments"), list) else []
            if not message.strip():
                self.respond(socket, {"ok": False, "error": "message is blank"})
                return
            timeoutSeconds = max(5, int(request.get("timeoutSeconds") or 240))
            self.log("bridge-service", f"chat request chars={len(message)} attachments={len(attachments)} timeout={timeoutSeconds}s")
            if bool(request.get("async")):
                jobId = str(request.get("jobId") or f"chat-{int(time.time() * 1000)}").strip()
                self.chatStarted[jobId] = time.time()
                def _storeChatResult(response: dict[str, Any], *, _jobId: str = jobId) -> None:
                    self.chatResults[_jobId] = dict(response or {})
                    self.log("bridge-service", f"chat job complete jobId={_jobId} ok={bool((response or {}).get('ok'))}")
                self.window.enqueueBridgeChat(message, _storeChatResult, timeoutSeconds=timeoutSeconds, attachments=attachments, target=target)
                self.respond(socket, {"ok": True, "accepted": True, "jobId": jobId, "pending": True})
                return
            key = id(socket)
            self.pendingChatSockets.add(key)
            def _directChatResponse(response: dict[str, Any], *, sock: QTcpSocket = socket, sockKey: int = key) -> None:
                self.pendingChatSockets.discard(sockKey)
                self.respond(sock, response)
            self.window.enqueueBridgeChat(message, _directChatResponse, timeoutSeconds=timeoutSeconds, attachments=attachments, target=target)
            return
        self.respond(socket, {"ok": False, "error": f"unknown action {action!r}"})

    def statusPayload(self) -> dict[str, Any]:
        url = ""
        try:
            url = self.window.grokView.url().toString()
        except Exception as error:
            recordException("supergrok_bridge/app.py:BridgeCommandServer.statusPayload", error, extra={"handler": "except Exception as error:"})
        return {
            "ok": True,
            "service": "SuperGrok Bridge",
            "pid": int(os.getpid() or 0),
            "port": int(self.server.serverPort() or self.port),
            "url": url,
            "target": normalizeChatTarget(getattr(getattr(self.window, "config", None), "target", "grok")),
            "loaded": bool(getattr(self.window, "grokLoadFinishedSeen", False)),
            "loadOk": bool(getattr(self.window, "grokLoadOk", False)),
            "activeChat": bool(getattr(self.window, "activeCliChatJob", None) is not None),
            "activeChatProgress": self.window.activeBridgeChatProgress() if hasattr(self.window, "activeBridgeChatProgress") else {},
            "queuedChats": len(getattr(self.window, "cliChatQueue", []) or []),
            "profileRoot": str(getattr(self.window, "grokProfileRoot", "") or ""),
            "root": str(ROOT.resolve()),
            "windowMode": str(getattr(getattr(self.window, "config", None), "windowMode", "visible") or "visible"),
            "offscreenMode": str(getattr(getattr(self.window, "config", None), "offscreenMode", "auto") or "auto"),
            "qtQpaPlatform": str(os.environ.get("QT_QPA_PLATFORM") or ""),
            "startedAt": float(getattr(self, "startedAt", 0.0) or 0.0),
            "uptimeSeconds": round(max(0.0, time.time() - float(getattr(self, "startedAt", time.time()) or time.time())), 3),
            "sourceSignature": BRIDGE_LOADED_SOURCE_SIGNATURE,
        }

    def respond(self, socket: QTcpSocket, payload: dict[str, Any], requestId: str | None = None) -> None:
        key = id(socket)
        requestId = requestId or self.currentRequestIds.get(key) or f"bridge-server-{int(time.time() * 1000)}-{key}"
        try:
            self.logBridgeResponse(socket, payload, requestId=requestId)
            try:
                _ = socket.state()
            except RuntimeError as socket_error:
                self.pendingChatSockets.discard(key)
                self.log("warn", f"could not respond because client socket was already deleted: {socket_error}")
                return
            line = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            socket.write(line)
            socket.flush()
            def _closeSocket(sock: QTcpSocket = socket, sockKey: int = key) -> None:
                self.pendingChatSockets.discard(sockKey)
                try:
                    sock.disconnectFromHost()
                except RuntimeError:  # swallow-ok
                    pass
                self.currentRequestIds.pop(sockKey, None)
            QTimer.singleShot(100, _closeSocket)
        except Exception as error:
            self.pendingChatSockets.discard(key)
            self.logBridgeResponse(socket, payload, requestId=requestId, error=error)
            recordException("supergrok_bridge/app.py:BridgeCommandServer.respond", error, extra={"handler": "except Exception as error:"})


class GrokChatDialog(QDialog):
    """Minimal Grok chat MVP that drives the live Grok page DOM.

    Sending is intentionally browser-native: fill the ProseMirror editor, then
    click Grok's real submit button. The JavaScript network hook still captures
    the underlying REST request/response for debugging.
    """

    def __init__(self, page: QWebEnginePage, database: RequestDatabase, debugPane: DebugScriptPane, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page = page
        self.database = database  # noqa: nonconform
        self.debugPane = debugPane  # noqa: redundant  # noqa: nonconform
        self.template: dict[str, Any] = {}
        self.parentResponseId = ""  # noqa: nonconform
        self.setWindowTitle(loc("GrokChat MVP"))
        self.resize(760, 620)

        self.conversationIdBox = QLineEdit(self)  # noqa: nonconform
        self.conversationIdBox.setPlaceholderText(loc("conversation id, auto-detected from URL or captured /responses request"))
        self.conversationIdBox.setClearButtonEnabled(True)

        self.replyBox = QPlainTextEdit(self)  # noqa: nonconform
        self.replyBox.setReadOnly(True)
        self.replyBox.setPlaceholderText(loc("Grok's clean reply will appear here."))
        self.replyBox.setMinimumHeight(230)
        self.replyBox.setFont(QFont("Consolas", 10))

        self.messageBox = SendOnEnterPlainTextEdit(self)  # noqa: nonconform
        self.messageBox.setPlaceholderText(loc("Type a message. Enter sends. Shift+Enter inserts a newline."))
        self.messageBox.setMinimumHeight(110)
        self.messageBox.setFont(QFont("Consolas", 10))

        self.detectButton = QPushButton(loc("Auto Detect"), self)  # noqa: nonconform
        self.sendButton = QPushButton(loc("Send"), self)
        self.closeButton = QPushButton(loc("Close"), self)  # noqa: redundant  # noqa: nonconform
        self.statusLabel = QLabel(loc(""), self)  # noqa: redundant  # noqa: nonconform

        topRow = QHBoxLayout()
        topRow.addWidget(QLabel(loc("Conversation ID"), self))
        topRow.addWidget(self.conversationIdBox, 1)
        topRow.addWidget(self.detectButton)

        buttonRow = QHBoxLayout()
        buttonRow.addWidget(self.statusLabel, 1)
        buttonRow.addWidget(self.sendButton)
        buttonRow.addWidget(self.closeButton)

        layout = QVBoxLayout(self)
        layout.addLayout(topRow)
        layout.addWidget(QLabel(loc("Grok Reply"), self))
        layout.addWidget(self.replyBox, 1)
        layout.addWidget(QLabel(loc("Message"), self))
        layout.addWidget(self.messageBox)
        layout.addLayout(buttonRow)

        self.detectButton.clicked.connect(self.autoDetect)
        self.sendButton.clicked.connect(self.sendMessage)
        self.closeButton.clicked.connect(self.close)  # noqa: redundant
        self.messageBox.enterPressed.connect(self.sendMessage)  # noqa: redundant

        self.activeSendId = ""  # noqa: nonconform
        self.pendingSendStartedAt = 0.0  # noqa: nonconform
        self.lastChatEventRaw = ""  # noqa: redundant  # noqa: nonconform
        self.pollTimer = QTimer(self)  # noqa: nonconform
        self.pollTimer.setInterval(500)
        self.pollTimer.timeout.connect(self.pollChatEvent)

        self.autoDetect()

    @Slot()
    def autoDetect(self) -> None:
        seed = self.database.latestGrokChatSeed()
        pageUrl = self.page.url().toString() if self.page is not None else ""
        conversationId = extractConversationId(pageUrl) or str(seed.get("conversationId", ""))
        parentResponseId = extractRid(pageUrl) or str(seed.get("parentResponseId", ""))
        self.template = seed.get("template", {}) if isinstance(seed.get("template"), dict) else {}
        self.parentResponseId = parentResponseId
        if conversationId:
            self.conversationIdBox.setText(conversationId)
        source = f"request #{seed.get('requestId')}" if seed.get("requestId") else "current URL"
        self.statusLabel.setText(f"Detected parent={self.parentResponseId[:8] or 'none'} from {source}")
        if seed.get("lastAnswer") and not self.replyBox.toPlainText().strip():
            self.replyBox.setPlainText(str(seed.get("lastAnswer")))

    @Slot()
    def sendMessage(self) -> None:
        conversationId = self.conversationIdBox.text().strip()
        message = self.messageBox.toPlainText().strip()
        if not conversationId:
            # Keep the conversation id visible because it is useful context, but the
            # DOM-send path can still work from the currently loaded Grok page.
            pageUrl = self.page.url().toString() if self.page is not None else ""
            conversationId = extractConversationId(pageUrl)
            if conversationId:
                self.conversationIdBox.setText(conversationId)
        if not message:
            QMessageBox.warning(self, "GrokChat", "Enter a message first.")
            return

        self.activeSendId = f"dom-{int(time.time() * 1000)}"
        self.pendingSendStartedAt = time.monotonic()
        self.lastChatEventRaw = ""
        self.replyBox.setPlainText("Sending through the live Grok page DOM...\nFilling ProseMirror, then clicking Grok's submit button.")
        self.statusLabel.setText(f"DOM send {self.activeSendId}...")
        self.debugPane.append("grokchat", f"{self.activeSendId}: DOM send start conversation={conversationId or 'current-page'} chars={len(message)}")
        self.messageBox.clear()
        self.pollTimer.start()
        self.page.runJavaScript(
            self.buildSendScript(message, self.activeSendId),
            lambda result: self.debugPane.append("grokchat", f"{self.activeSendId}: JS bootstrap returned: {result}"),
        )

    def buildSendScript(self, message: str, sendId: str) -> str:
        return buildGrokDomSendScript(message, sendId, maxTicks=120)

    @Slot()
    def pollChatEvent(self) -> None:
        if not self.activeSendId:
            self.pollTimer.stop()
            return
        if self.pendingSendStartedAt and (time.monotonic() - self.pendingSendStartedAt) > 90.0:
            self.pollTimer.stop()
            self.debugPane.append("error", f"{self.activeSendId}: GrokChat timed out waiting for JS event after 90s")
            self.statusLabel.setText(loc("Timed out waiting for GrokChat JS event"))
            return
        self.page.runJavaScript(
            "window.__SuperGrokChatLastEvent || ''",
            lambda raw: self.handlePolledChatEvent(raw),
        )

    def handlePolledChatEvent(self, raw: Any) -> None:
        if not raw or not self.activeSendId:
            return
        rawText = str(raw)
        if rawText == self.lastChatEventRaw:
            return
        self.lastChatEventRaw = rawText
        try:
            payload = json.loads(rawText)
            if isinstance(payload, dict) and payload.get("sendId") == self.activeSendId:
                self.handleChatEvent(payload)
        except Exception as error:
            recordException("supergrok_bridge/app.py:1814", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("error", f"{self.activeSendId}: polled GrokChat event parse failed: {type(error).__name__}: {error}")

    def handleChatEvent(self, payload: dict[str, Any]) -> None:
        sendId = str(payload.get("sendId") or "")
        if self.activeSendId and sendId and sendId != self.activeSendId:
            return
        eventType = str(payload.get("eventType") or "complete")
        if eventType == "trace":
            stage = str(payload.get("stage") or "trace")
            self.statusLabel.setText(f"{stage}...")
            self.debugPane.append("grokchat", json.dumps(payload, ensure_ascii=False, indent=2))
            return
        self.pollTimer.stop()
        if eventType == "complete" and payload.get("ok"):
            answer = str(payload.get("answer") or "").strip()
            if not answer:
                answer = "[No clean answer parsed. Raw preview follows.]\n" + str(payload.get("rawPreview", ""))
            self.replyBox.setPlainText(answer)
            responseId = str(payload.get("responseId") or "")
            if responseId:
                self.parentResponseId = responseId
            self.statusLabel.setText(f"Done HTTP {payload.get('status')} in {payload.get('durationMs')}ms parent={self.parentResponseId[:8] or 'none'}")
            self.debugPane.append("grokchat", json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.replyBox.setPlainText(f"ERROR: {payload.get('error', 'unknown error')}\n{payload.get('stack', '')}")
            self.statusLabel.setText(loc("Send failed"))
            self.debugPane.append("error", json.dumps(payload, ensure_ascii=False, indent=2))
        self.activeSendId = ""


class CommandApprovalDialog(QDialog):
    def __init__(self, command: str, reason: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.command = command
        self.reason = reason  # noqa: nonconform
        self.decision = "deny"
        self.setWindowTitle(loc("ToolCall Approval"))
        self.resize(880, 520)
        self.commandView = QWebEngineView(self)  # noqa: nonconform
        enableWebSettings(self.commandView.settings(), localContentCanAccessRemote=False)
        self.commandView.setHtml(self.buildCommandHtml(command, reason), QUrl.fromLocalFile(str(ROOT / "toolcall_command.html")))
        allowButton = QPushButton(loc("Allow Once"), self)
        denyButton = QPushButton(loc("Deny"), self)
        alwaysButton = QPushButton(loc("Always Allow"), self)  # noqa: redundant
        allowButton.clicked.connect(lambda: self.finish("allow"))
        denyButton.clicked.connect(lambda: self.finish("deny"))
        alwaysButton.clicked.connect(lambda: self.finish("always_allow"))  # noqa: redundant
        buttons = QHBoxLayout()
        buttons.addWidget(QLabel(loc("Command was not auto-run. Choose a decision."), self))
        buttons.addStretch(1)
        buttons.addWidget(allowButton)
        buttons.addWidget(alwaysButton)
        buttons.addWidget(denyButton)  # noqa: redundant
        layout = QVBoxLayout(self)
        layout.addWidget(self.commandView, 1)
        layout.addLayout(buttons)

    def buildCommandHtml(self, command: str, reason: str) -> str:
        prismCss = QUrl.fromLocalFile(str(PRISM / "prism.min.css")).toString() if (PRISM / "prism.min.css").exists() else ""
        prismJs = QUrl.fromLocalFile(str(PRISM / "prism.min.js")).toString() if (PRISM / "prism.min.js").exists() else ""
        bashJs = QUrl.fromLocalFile(str(PRISM / "components" / "prism-bash.min.js")).toString() if (PRISM / "components" / "prism-bash.min.js").exists() else ""
        cssTag = f'<link rel="stylesheet" href="{html.escape(prismCss)}">' if prismCss else ""
        jsTag = f'<script src="{html.escape(prismJs)}"></script>' if prismJs else ""
        bashTag = f'<script src="{html.escape(bashJs)}"></script>' if bashJs else ""
        escapedCommand = html.escape(command, quote=False)
        escapedReason = html.escape(reason, quote=False)
        return f'''<!doctype html><html><head><meta charset="utf-8"><title>ToolCall Command</title>{cssTag}<style>html,body{{margin:0;min-height:100%;background:#0b1020;color:#f8fafc;font-family:Segoe UI,Arial,sans-serif}}.wrap{{padding:14px}}.reason{{margin-bottom:12px;color:#cbd5e1}}pre{{margin:0;border:1px solid #334155;border-radius:12px;background:#111827;padding:16px;white-space:pre-wrap;overflow-wrap:anywhere}}code{{font-family:Consolas,"Cascadia Mono","Courier New",monospace!important;font-size:14px;line-height:1.45;white-space:pre-wrap!important}}</style></head><body><div class="wrap"><div class="reason">{escapedReason}</div><pre><code class="language-bash">{escapedCommand}</code></pre></div>{jsTag}{bashTag}<script>if(window.Prism)Prism.highlightAll();</script></body></html>'''

    def finish(self, decision: str) -> None:
        self.decision = decision
        self.accept()


class ToolCallDecisionBridge(QObject):
    def __init__(self, manager: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.manager = manager  # noqa: nonconform

    @Slot(str, str)
    def toolCallDecision(self, commandId: str, decision: str) -> None:
        self.manager.handleInlineDecision(commandId, decision)


class ToolCallManager(QObject):
    READ_ONLY_AUTO_ROOTS = {"dir", "ls", "pwd", "cd", "echo", "cat", "type", "grep", "findstr", "find", "where", "which", "whoami", "hostname", "ver", "uname", "date", "time", "tree", "head", "tail", "wc", "sort", "uniq", "more", "less", "calc"}

    def __init__(self, policyDb: CommandPolicyDatabase, processDb: ProcessDatabase, controller: GrokPageController, debugPane: DebugScriptPane, chat: ChatPane, processTtlSeconds: int = PROCESS_DEFAULT_TTL_SECONDS, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.policyDb = policyDb  # noqa: nonconform
        self.processDb = processDb  # noqa: nonconform
        self.controller = controller  # noqa: redundant  # noqa: nonconform
        self.debugPane = debugPane  # noqa: redundant  # noqa: nonconform
        self.chat = chat
        self.parentWidget = parent  # noqa: redundant  # noqa: nonconform
        self.processTtlSeconds = max(1, int(processTtlSeconds or PROCESS_DEFAULT_TTL_SECONDS))
        self.runningProcesses: dict[str, QProcess] = {}
        self.processStates: dict[str, dict[str, Any]] = {}
        self.seenResponseIds: set[str] = set()
        self.pendingCommands: dict[str, str] = {}
        self.pendingCounter = 0  # noqa: nonconform
        self.watchdogTimer = QTimer(self)  # noqa: nonconform
        self.watchdogTimer.setInterval(2000)
        self.watchdogTimer.timeout.connect(self.pollProcessWatchdog)
        self.watchdogTimer.start()

    def trace(self, command: str, decision: str, reason: str) -> None:
        root = commandRoot(command)
        line = f"command={command!r} root={root!r} decision={decision} reason={reason}"
        self.debugPane.append("toolcall", line)
        writeTrafficLog({"eventType": "toolcall-decision", "command": command, "root": root, "decision": decision, "reason": reason})

    def handleGrokPacket(self, payload: dict[str, Any]) -> None:
        parsed = self.extractAssistantMessage(payload)
        message = parsed.get("message", "")
        responseId = parsed.get("responseId", "")
        if responseId and responseId in self.seenResponseIds:
            return
        if responseId:
            self.seenResponseIds.add(responseId)
        if message:
            # Always render Grok's normal message first, then append parsed ToolCall
            # cards directly underneath it. This keeps mixed prose + commands readable.
            self.controller.chat.append("grok", message, parseToolCalls=False)
            self.queueCommandsFromMessage(message, responseId=responseId, source="network-packet")

    def queueCommandsFromMessage(self, message: str, responseId: str = "", source: str = "unknown") -> None:
        commands = self.parseCommands(message or "")
        writeTrafficLog({"eventType": "toolcall-parse", "responseId": responseId, "source": source, "count": len(commands), "commands": commands})
        for command in commands:
            self.evaluateCommand(command)

    def activeTarget(self) -> str:
        try:
            config = getattr(self.parentWidget, "config", None)
            return normalizeChatTarget(getattr(config, "target", "") or chatTargetFromUrl(getattr(config, "initialUrl", "")))
        except Exception:  # swallow-ok
            return "grok"

    def extractAssistantMessage(self, payload: dict[str, Any]) -> dict[str, str]:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        body = response.get("body", "")
        url = str(payload.get("url") or response.get("url") or "")
        target = "chatgpt" if chatTargetFromUrl(url) == "chatgpt" else self.activeTarget()
        return parseAssistantResponseStream(body if isinstance(body, str) else "", target)

    def parseCommands(self, message: str) -> list[str]:
        """Parse ToolCall candidates from Grok prose.

        Rules:
        - Render the Grok message normally first; parsed commands become separate cards.
        - Every command line inside ```toolcall / ```bash / ```cmd / etc. becomes
          its own ToolCall card.
        - Unlabeled triple-backtick blocks count only when every nonblank line looks
          like a simple command with an allowlisted or saved-whitelisted root.
        - Inline single-backtick snippets count only for one-line shell commands
          with a known command root, e.g. `python grok_test.py` or `cat file.txt`.
        - Normal code/example blocks like ```python, ```json, and ```output are ignored.
        """
        found: list[str] = []
        acceptedLabels = {"toolcall", "tool", "command", "commands", "bash", "sh", "shell", "cmd", "bat", "powershell", "ps1", "zsh", "calc"}
        ignoredCodeLabels = {"python", "py", "javascript", "js", "json", "html", "css", "xml", "yaml", "yml", "sql", "php", "java", "cpp", "c", "csharp", "cs", "go", "rust", "ruby", "rb", "perl", "r", "lua", "text", "output"}
        knownInlineRoots = loadCommonCommandRoots()

        def cleanLine(line: str) -> str:
            command = (line or "").strip()
            if command.startswith("$ "):
                command = command[2:].strip()
            return command

        pattern = re.compile(r"```(?P<label>[^`\r\n]*)\r?\n(?P<body>[\s\S]*?)```", re.IGNORECASE)
        spans: list[tuple[int, int]] = []
        for match in pattern.finditer(message or ""):
            spans.append(match.span())
            label = (match.group("label") or "").strip().lower()
            if label in ignoredCodeLabels:
                continue
            block = match.group("body") or ""
            commands: list[str] = []
            for line in block.splitlines():
                command = cleanLine(line)
                if not command or command.startswith("#"):
                    continue
                commands.append(command)
            if not commands:
                continue
            if label == "calc":
                expression = " ".join(commands).strip()
                # Keep calc fences safe and executable: turn simple arithmetic into
                # a normal Python one-shot command that still goes through the
                # ToolCall card, Execute button, subprocess DB row, TTL watchdog,
                # stdout/stderr capture, and result-send path.
                if expression and re.fullmatch(r"[0-9+\-*/%(). \t]+", expression):
                    found.append(f"{shlex.quote(sys.executable)} -c {shlex.quote('print(' + expression + ')')}")
                elif expression:
                    found.append("echo Unsupported calc expression")
                continue
            if label in acceptedLabels:
                found.extend(commands)
                continue
            if label:
                continue
            simpleCommands: list[str] = []
            for command in commands:
                root = commandRoot(command)
                if not root:
                    simpleCommands = []
                    break
                if self.policyDb.decisionFor(root) == "always_allow" or isKnownToolCommand(command):
                    simpleCommands.append(command)
                    continue
                simpleCommands = []
                break
            found.extend(simpleCommands)

        # Inline commands in prose, e.g. Want me to run it next? (`python grok_test.py`)
        # Skip snippets that were already inside fenced blocks.
        for match in re.finditer(r"`([^`\r\n]{1,240})`", message or ""):
            start = match.start()
            if any(a <= start < b for a, b in spans):
                continue
            command = cleanLine(match.group(1))
            if not command or command.startswith("#") or hasShellControlOperators(command):
                continue
            root = commandRoot(command)
            if root and (root in knownInlineRoots or self.policyDb.decisionFor(root) == "always_allow"):
                found.append(command)

        unique: list[str] = []
        seen: set[str] = set()
        for foundCommand in found:
            command = (foundCommand or "").strip()
            if command and command not in seen:
                seen.add(command)
                unique.append(command)
        return unique

    def evaluateCommand(self, command: str) -> None:
        command = (command or "").strip()
        root = commandRoot(command)
        if not command or not root:
            self.trace(command, "blocked", "empty command after trim")
            return
        saved = self.policyDb.decisionFor(root)
        if saved == "always_allow":
            self.trace(command, "auto-run", "saved always allow")
            self.runCommand(command, autoSend=True)
            return
        if root in self.READ_ONLY_AUTO_ROOTS and not hasShellControlOperators(command):
            self.queueInlineApproval(command, root, "read-only allowlist; waiting for Run or Whitelist")
            return
        self.queueInlineApproval(command, root, "not whitelisted; waiting for Run or Whitelist")

    def nextCommandId(self) -> str:
        self.pendingCounter += 1
        return f"tc-{int(time.time() * 1000)}-{self.pendingCounter}"

    def queueInlineApproval(self, command: str, root: str, reason: str) -> None:
        command = (command or "").strip()
        if not command:
            self.trace(command, "blocked", "empty command before inline queue")
            return
        commandId = self.nextCommandId()
        self.pendingCommands[commandId] = command
        self.trace(command, "approval-required", reason)
        self.chat.addToolCallCard(commandId, command, f"Parsed ToolCall root={root}: {reason}")
        writeTrafficLog({"eventType": "toolcall-queued", "commandId": commandId, "command": command, "root": root, "reason": reason})

    @Slot(str, str)
    def handleInlineDecision(self, commandId: str, decision: str) -> None:
        commandId = str(commandId or "").strip()
        decision = str(decision or "").strip().lower()
        command = self.pendingCommands.pop(commandId, "").strip()
        if not command:
            self.debugPane.append("toolcall", f"inline decision ignored: commandId={commandId!r} decision={decision!r} empty/missing command")
            writeTrafficLog({"eventType": "toolcall-inline-decision", "commandId": commandId, "decision": decision, "result": "missing-command"})
            self.chat.updateToolCallCard(commandId, "missing/blank")
            return
        root = commandRoot(command)
        if decision == "always_allow":
            self.policyDb.alwaysAllow(root)
            self.trace(command, "auto-run", "inline whitelist and run")
            self.chat.updateToolCallCard(commandId, "whitelisted; running")
            self.runCommand(command, autoSend=True)
            return
        if decision == "allow":
            self.trace(command, "allowed-once", "inline run")
            self.chat.updateToolCallCard(commandId, "running")
            self.runCommand(command, autoSend=True)
            return
        self.trace(command, "blocked", f"unknown inline decision {decision!r}")
        self.chat.updateToolCallCard(commandId, f"unknown decision: {decision}")

    def shellProgram(self) -> tuple[str, list[str]]:
        return _shellProgramForToolCalls()

    def runCommand(self, command: str, autoSend: bool = True) -> None:
        program, baseArgs = self.shellProgram()
        arguments = baseArgs + [command]
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(arguments)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        processKey = f"proc-{int(time.time() * 1000)}-{len(self.runningProcesses) + 1}"
        state: dict[str, Any] = {
            "stdout": "",
            "stderr": "",
            "command": command,
            "program": program,
            "arguments": arguments,
            "started": time.time(),
            "expiresAt": time.time() + self.processTtlSeconds,
            "autoSend": autoSend,
            "processKey": processKey,
            "finished": False,
        }
        self.processDb.createProcess(processKey, command, program, arguments, self.processTtlSeconds)

        def readStdout() -> None:
            try:
                state["stdout"] += bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            except RuntimeError as error:
                recordException("supergrok_bridge/app.py:2114", error, extra={"handler": "except RuntimeError as error:"})
                self.trace(command, "stdout-read-runtime-error", str(error))
            except Exception as error:
                recordException("supergrok_bridge/app.py:2116", error, extra={"handler": "except Exception as error:"})
                self.trace(command, "stdout-read-error", f"{type(error).__name__}: {error}")

        def readStderr() -> None:
            try:
                state["stderr"] += bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
            except RuntimeError as error:
                recordException("supergrok_bridge/app.py:2122", error, extra={"handler": "except RuntimeError as error:"})
                self.trace(command, "stderr-read-runtime-error", str(error))
            except Exception as error:
                recordException("supergrok_bridge/app.py:2124", error, extra={"handler": "except Exception as error:"})
                self.trace(command, "stderr-read-error", f"{type(error).__name__}: {error}")

        def started() -> None:
            pid = int(process.processId() or 0)
            state["pid"] = pid
            self.processDb.markStarted(processKey, pid)
            self.trace(command, "started", f"processKey={processKey} pid={pid} ttl={self.processTtlSeconds}s")
            writeTrafficLog({"eventType": "toolcall-started", "processKey": processKey, "pid": pid, "command": command})

        def failedToStart(error: Any) -> None:
            message = enumName(error)
            state["finished"] = True
            self.runningProcesses.pop(processKey, None)
            self.processStates.pop(processKey, None)
            self.processDb.markFault(processKey, f"failed to start: {message}", state["stdout"], state["stderr"])
            self.debugPane.append("toolcall", f"failed to start {processKey}: {message}\n{command}")
            writeTrafficLog({"eventType": "toolcall-fault", "processKey": processKey, "command": command, "error": message})
            try:
                process.deleteLater()
            except RuntimeError as error:
                recordException("supergrok_bridge/app.py:2144", error, extra={"handler": "except RuntimeError:"})
                pass

        def finish(exitCode: int, exitStatus: Any) -> None:
            if state.get("finished") == "timed_out":
                return
            state["finished"] = True
            readStdout(); readStderr()
            elapsed = time.time() - float(state["started"])
            result = {"command": state["command"], "exitCode": int(exitCode), "exitStatus": enumName(exitStatus), "elapsedSeconds": round(elapsed, 3), "stdout": state["stdout"], "stderr": state["stderr"]}
            self.processDb.markCompleted(processKey, int(exitCode), enumName(exitStatus), state["stdout"], state["stderr"])
            self.debugPane.append("toolcall", "completed:\n" + json.dumps({"processKey": processKey, **result}, ensure_ascii=False, indent=2))
            writeTrafficLog({"eventType": "toolcall-result", "processKey": processKey, **result})
            self.runningProcesses.pop(processKey, None)
            self.processStates.pop(processKey, None)
            try:
                process.deleteLater()
            except RuntimeError as error:
                recordException("supergrok_bridge/app.py:2161", error, extra={"handler": "except RuntimeError:"})
                pass
            if state["autoSend"]:
                self.sendResultToGrok(result)

        process.started.connect(started)
        process.errorOccurred.connect(failedToStart)
        process.readyReadStandardOutput.connect(readStdout)  # noqa: redundant
        process.readyReadStandardError.connect(readStderr)  # noqa: redundant
        process.finished.connect(finish)
        self.runningProcesses[processKey] = process
        self.processStates[processKey] = state
        self.debugPane.append("toolcall", f"spawn: {command}")
        writeTrafficLog({"eventType": "toolcall-spawn", "processKey": processKey, "command": command, "program": program, "arguments": arguments, "ttlSeconds": self.processTtlSeconds})
        process.start()  # pid-ok: QProcess.start() — pid surfaced via self.runningProcesses tracking

    @Slot()
    def pollProcessWatchdog(self) -> None:
        now = time.time()
        for processKey, process in list(self.runningProcesses.items()):
            state = self.processStates.get(processKey, {})
            expiresAt = float(state.get("expiresAt", now + self.processTtlSeconds))
            if now < expiresAt:
                continue
            self.timeoutProcess(processKey, str(state.get("command", "")))

    def timeoutProcess(self, processKey: str, command: str) -> None:
        process = self.runningProcesses.get(processKey)
        state = self.processStates.get(processKey, {})
        if process is None:
            return
        state["finished"] = "timed_out"
        stdout = str(state.get("stdout", ""))
        stderr = str(state.get("stderr", ""))
        pid = int(state.get("pid") or process.processId() or 0)
        reason = f"timeout after {self.processTtlSeconds}s pid={pid}"
        try:
            self.trace(command, "timed_out", reason)
            writeTrafficLog({"eventType": "toolcall-timeout", "processKey": processKey, "command": command, "pid": pid, "ttlSeconds": self.processTtlSeconds})
            if os.name == "nt" and pid > 0:
                managedSubprocessRun(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=10)
            elif process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2204", error, extra={"handler": "except Exception as error:"})
            self.trace(command, "kill-error", f"{type(error).__name__}: {error}")
        finally:
            self.processDb.markTimedOut(processKey, reason, stdout, stderr)
            self.runningProcesses.pop(processKey, None)
            self.processStates.pop(processKey, None)
            try:
                process.deleteLater()
            except RuntimeError as error:
                recordException("supergrok_bridge/app.py:2212", error, extra={"handler": "except RuntimeError:"})
                pass
            self.debugPane.append("toolcall", f"timed out and killed: {processKey}\n{command}")

    def sendResultToGrok(self, result: dict[str, Any]) -> None:
        text = "ToolCall result:\nCommand: {command}\nExit code: {exitCode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}".format(
            command=result.get("command"), exitCode=result.get("exitCode"), stdout=result.get("stdout") or "", stderr=result.get("stderr") or ""
        )
        if len(text) > 12000:
            text = text[:12000] + "\n[truncated]"
        self.controller.sendPrompt(text)


class SuperGrokBridgeWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config  # noqa: nonconform
        self.config.target = normalizeChatTarget(getattr(config, "target", "") or chatTargetFromUrl(getattr(config, "initialUrl", "")))
        if not str(getattr(self.config, "initialUrl", "") or "").strip():
            self.config.initialUrl = chatProviderHomeUrl(self.config.target)
        self.providerLabel = chatProviderLabel(self.config.target)  # noqa: nonconform
        self.setWindowTitle(loc(f"SuperGrok Bridge - {self.providerLabel}"))
        self.resize(1680, 940)
        self.devToolsDocks: dict[int, QDockWidget] = {}
        clearRunLogs("SuperGrokBridgeWindow.__init__")
        self.requestDatabase = RequestDatabase(REQUEST_DB)  # noqa: nonconform
        self.uiStateDatabase = UIStateDatabase(UI_STATE_DB)  # noqa: nonconform
        self.commandPolicyDatabase = CommandPolicyDatabase(POLICY_DB)  # noqa: redundant  # noqa: nonconform
        self.processDatabase = ProcessDatabase(PROCESS_DB)  # noqa: redundant  # noqa: nonconform
        self.processDatabase.clearForNewRun()
        self.requestInterceptor: WebRequestInterceptor | None = None
        self.grokProfile: QWebEngineProfile | None = None
        self.grokProfileRoot: Path | None = None  # noqa: redundant
        self.grokProfileStoragePath: Path | None = None  # noqa: redundant
        self.grokProfileCachePath: Path | None = None
        self.activeGrokChatDialog: GrokChatDialog | None = None  # noqa: redundant
        self.grokLoadFinishedSeen = False  # noqa: nonconform
        self.grokLoadOk = False  # noqa: nonconform
        self.cliChatQueue: list[tuple[str, Callable[[dict[str, Any]], None], int]] = []
        self.activeCliChatJob: GrokBridgeChatJob | None = None
        self.bridgeCommandServer: BridgeCommandServer | None = None

        self.grokView = self.createGrokWebView()
        self.debugPane = DebugScriptPane(self.showSourceForView, self.openDevToolsForPage, self)  # noqa: nonconform
        self.traceGrokProfileStatus()
        self.connectGrokPageDebugSignals()
        self.chat = ChatPane(self.showSourceForView, self.openDevToolsForPage, self)
        self.controller = GrokPageController(self.grokView.page(), self.chat, self.debugPane, config)  # noqa: nonconform
        self.toolCallManager = ToolCallManager(self.commandPolicyDatabase, self.processDatabase, self.controller, self.debugPane, self.chat, self.config.processTtlSeconds, self)  # noqa: nonconform
        self.chat.setToolCallManager(self.toolCallManager)
        self.debuggerHeartbeatTimer = QTimer(self)  # noqa: nonconform
        self.debuggerHeartbeatTimer.setInterval(DEBUGGER_HEARTBEAT_INTERVAL_MS)
        self.debuggerHeartbeatTimer.timeout.connect(self.emitDebuggerHeartbeatSurface)

        self.messagesButton = QPushButton(loc("Messages"), self)  # noqa: nonconform
        self.grokChatButton = QPushButton(loc("GrokChat"), self)  # noqa: nonconform
        self.sayHiButton = QPushButton(loc("Say Hi"), self)  # noqa: redundant  # noqa: nonconform
        self.injectButton = QPushButton(loc("Inject"), self)  # noqa: redundant  # noqa: nonconform
        self.grokAddress = QLineEdit(self)  # noqa: nonconform
        self.grokAddress.setPlaceholderText(loc(f"Paste a {self.providerLabel} / login URL or type a search..."))
        self.grokAddress.setClearButtonEnabled(True)
        self.grokGoButton = QPushButton(loc("Go"), self)  # noqa: nonconform
        self.requestPane = RequestPane(self.requestDatabase, self)  # noqa: nonconform

        grokHeader = QHBoxLayout()
        grokHeader.addWidget(QLabel(loc(f"Live {self.providerLabel} Website"), self))
        grokHeader.addStretch(1)
        grokHeader.addWidget(self.messagesButton)
        grokHeader.addWidget(self.grokChatButton)
        grokHeader.addWidget(self.sayHiButton)  # noqa: redundant
        grokHeader.addWidget(self.injectButton)  # noqa: redundant

        grokAddressRow = QHBoxLayout()
        grokAddressRow.addWidget(QLabel(loc("URL/Search"), self))
        grokAddressRow.addWidget(self.grokAddress, 1)
        grokAddressRow.addWidget(self.grokGoButton)

        self.grokContainer = QWidget(self)  # noqa: nonconform
        grokLayout = QVBoxLayout(self.grokContainer)
        grokLayout.addLayout(grokHeader)
        grokLayout.addLayout(grokAddressRow)
        grokLayout.addWidget(self.grokView, 1)
        grokLayout.addWidget(self.requestPane)

        self.mainSplitter = QSplitter(Qt.Orientation.Horizontal, self)  # noqa: nonconform
        self.mainSplitter.setObjectName("MainColumnSplitter")
        self.debugPane.setObjectName("DebugColumn")
        self.chat.setObjectName("ChatColumn")  # noqa: redundant
        self.grokContainer.setObjectName("GrokColumn")  # noqa: redundant
        self.mainSplitter.addWidget(self.debugPane)
        self.mainSplitter.addWidget(self.chat)
        self.mainSplitter.addWidget(self.grokContainer)  # noqa: redundant
        self.mainSplitter.setChildrenCollapsible(True)
        self.mainSplitter.setHandleWidth(6)
        self.mainSplitter.setSizes([360, 500, 820])
        self.setCentralWidget(self.mainSplitter)
        self.applyBridgeChrome()
        self.columnActions: dict[str, QAction] = {}
        self.columnWidgets: dict[str, QWidget] = {
            "debug": self.debugPane,
            "chat": self.chat,
            "grok": self.grokContainer,
        }

        self.setStatusBar(QStatusBar(self))
        self.buildMenus()
        self.buildToolbar()
        self.restoreWindowUiState()  # noqa: redundant
        self.connectSignals()  # noqa: redundant
        self.grokView.load(QUrl(config.initialUrl))
        self.emitDebuggerHeartbeatSurface(reason="window-created")
        self.debuggerHeartbeatTimer.start()

        if config.serviceMode:
            self.startBridgeCommandService()

        if config.remoteDebugPort:
            self.statusBar().showMessage(f"Chromium DevTools remote: http://127.0.0.1:{config.remoteDebugPort}")

    def applyBridgeChrome(self) -> None:
        # Scoped stylesheet for the bridge chrome: toolbar / splitter handles /
        # buttons / labels / status bar. Deliberately does NOT target QWebEngineView
        # or its children — Chromium owns its own painting and we don't want to
        # interfere with composition (especially on Windows offscreen-window mode).
        self.setStyleSheet(
            """
            QMainWindow { background: #1f232b; }
            QStatusBar { background: #15181e; color: #c7cdd6; border-top: 1px solid #2d313a; }
            QToolBar { background: #15181e; border-bottom: 1px solid #2d313a; spacing: 2px; padding: 4px; }
            QToolBar QToolButton { color: #e2e8f0; background: transparent; padding: 4px 10px; border-radius: 4px; }
            QToolBar QToolButton:hover { background: #2a2f3a; }
            QToolBar QToolButton:pressed { background: #3a4150; }
            QPushButton { background: #2a2f3a; color: #e2e8f0; border: 1px solid #3d4451; border-radius: 4px; padding: 5px 12px; }
            QPushButton:hover { background: #343a47; border-color: #4d5461; }
            QPushButton:pressed { background: #1f232b; }
            QPushButton:disabled { color: #5c6370; background: #20242c; border-color: #2d313a; }
            QLabel { color: #c7cdd6; }
            QLineEdit { background: #15181e; color: #e2e8f0; border: 1px solid #2d313a; border-radius: 4px; padding: 4px 8px; selection-background-color: #3d4451; }
            QLineEdit:focus { border-color: #82aaff; }
            QMenuBar { background: #15181e; color: #c7cdd6; }
            QMenuBar::item:selected { background: #2a2f3a; }
            QMenu { background: #1f232b; color: #e2e8f0; border: 1px solid #2d313a; }
            QMenu::item:selected { background: #2a2f3a; }
            QSplitter::handle { background: #2d313a; }
            QSplitter::handle:horizontal { width: 6px; margin: 0 1px; border-left: 1px solid #15181e; border-right: 1px solid #15181e; }
            QSplitter::handle:vertical { height: 6px; margin: 1px 0; border-top: 1px solid #15181e; border-bottom: 1px solid #15181e; }
            QSplitter::handle:hover { background: #82aaff; }
            #MainColumnSplitter::handle { background: #3d4451; }
            #MainColumnSplitter::handle:hover { background: #82aaff; }
            QPlainTextEdit { background: #15181e; color: #e2e8f0; border: 1px solid #2d313a; selection-background-color: #3d4451; }
            QListWidget { background: #15181e; color: #e2e8f0; border: 1px solid #2d313a; }
            QListWidget::item:selected { background: #2a2f3a; }
            QDockWidget { color: #c7cdd6; }
            QDockWidget::title { background: #15181e; padding: 4px; }
            """
        )

    def debuggerVarDump(self) -> dict[str, Any]:
        currentUrl = ""
        try:
            currentUrl = self.grokView.url().toString()
        except Exception as error:
            recordException("supergrok_bridge/app.py:debuggerVarDump.url", error, extra={"handler": "except Exception as error:"})
        return {
            "app": "SuperGrok Bridge",
            "pid": int(os.getpid() or 0),
            "url": currentUrl,
            "target": normalizeChatTarget(getattr(self.config, "target", "grok")),
            "debug": bool(self.config.debug),
            "remoteDebugPort": int(self.config.remoteDebugPort or 0),
            "processTtlSeconds": int(self.config.processTtlSeconds or 0),
            "runningToolCalls": len(getattr(self.toolCallManager, "runningProcesses", {}) or {}),
            "surfaces": list(DEBUGGER_SURFACES),
        }

    def debuggerProcessSnapshot(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        try:
            rows = self.processDatabase.allRows()
        except Exception as error:
            recordException("supergrok_bridge/app.py:debuggerProcessSnapshot", error, extra={"handler": "except Exception as error:"})
        return {
            "processDb": str(PROCESS_DB),
            "runningKeys": list(getattr(self.toolCallManager, "runningProcesses", {}).keys()),
            "recentRows": rows[:50],
        }

    @Slot()
    def emitDebuggerHeartbeatSurface(self, reason: str = "timer") -> None:
        emitDebuggerHeartbeat(
            eventKind="heartbeat",
            reason=reason,
            caller="SuperGrokBridgeWindow.emitDebuggerHeartbeatSurface",
            phase="qt-event-loop",
            varDump=self.debuggerVarDump(),
            processSnapshot=self.debuggerProcessSnapshot(),
        )

    def connectGrokPageDebugSignals(self) -> None:
        page = self.grokView.page()
        if isinstance(page, BridgeWebPage):
            page.consoleMessage.connect(self.onGrokConsoleMessage)
            page.alertRequested.connect(self.onGrokAlertRequested)

    def normalizeJavaScriptNetworkPayload(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
        response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        method = str(payload.get("method") or request.get("method") or "").upper() or "REQ"
        url = str(payload.get("url") or response.get("url") or request.get("url") or "")
        layer = str(payload.get("captureLayer") or "javascript-hook")
        normalized = dict(payload)
        normalized.update({
            "viewLabel": f"Live {getattr(self, 'providerLabel', chatProviderLabel(getattr(self.config, 'target', 'grok')))} Website",
            "captureLayer": layer,
            "method": method,
            "url": url,
            "displayUrl": displayUrl(url),
            "firstPartyUrl": str((payload.get("page") or {}).get("url", "")) if isinstance(payload.get("page"), dict) else "",
            "initiator": str((payload.get("page") or {}).get("url", "")) if isinstance(payload.get("page"), dict) else "",
            "navigationType": "JavaScriptNetworkHook",
            "resourceType": "Fetch" if "fetch" in layer else ("Xhr" if "xhr" in layer else "JavaScriptNetwork"),
            "note": f"Captured inside the {getattr(self, 'providerLabel', 'AI chat')} page by JavaScript fetch/XMLHttpRequest hooks. Sensitive header/key names are redacted by default; request/response bodies are captured up to the configured JS limit.",
        })
        return normalized

    def handleJavaScriptNetworkEvent(self, message: str) -> bool:
        if not message.startswith(NETWORK_EVENT_PREFIX):
            return False
        raw = message[len(NETWORK_EVENT_PREFIX):]
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("network event was not a JSON object")
            normalized = self.normalizeJavaScriptNetworkPayload(payload)
            writeTrafficLog({"eventType": "network", "payload": normalized})
            sessionLog({"eventType": "grok-network", "payload": normalized})
            requestId = self.requestDatabase.record(normalized)
            self.requestPane.addRequest(requestId, normalized)
            self.debugPane.append(
                "request",
                f"#{requestId} {normalized.get('captureLayer')} {normalized.get('method')} {normalized.get('displayUrl')}"
            )
            url = str(normalized.get("url", ""))
            target = normalizeChatTarget(getattr(self.config, "target", "grok"))
            parsed = self.toolCallManager.extractAssistantMessage(normalized)
            message = str(parsed.get("message") or "").strip() if isinstance(parsed, dict) else ""
            responseId = str(parsed.get("responseId") or "") if isinstance(parsed, dict) else ""
            isChatStream = isAssistantChatStreamUrl(url, target) or (target == "chatgpt" and bool(message))
            if isChatStream:
                if message:
                    sessionLog({
                        "eventType": "assistant-answer-stream",
                        "url": url,
                        "responseId": responseId,
                        "answerChars": len(message),
                        "answerPreview": message[:500],
                    })
                    self.debugPane.append("grokchat", f"network answer stream chars={len(message)} responseId={responseId} target={target}")
                    job = self.activeCliChatJob
                    if job is not None and hasattr(job, "finishFromNetworkAnswer"):
                        try:
                            if job.finishFromNetworkAnswer(message, responseId=responseId, url=url):
                                return True
                        except Exception as error:
                            recordException("supergrok_bridge/app.py:network-answer-active-job", error, extra={"url": url})
                self.toolCallManager.handleGrokPacket(normalized)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2358", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("error", f"Network event parse/log failed: {type(error).__name__}: {error}\n{raw[:1000]}")
        return True

    def handleGrokChatModalEvent(self, message: str) -> bool:
        if not message.startswith(CHAT_MODAL_EVENT_PREFIX):
            return False
        raw = message[len(CHAT_MODAL_EVENT_PREFIX):]
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("GrokChat event was not a JSON object")
            self.debugPane.append("grokchat", json.dumps(payload, ensure_ascii=False, indent=2))
            if self.activeGrokChatDialog is not None:
                self.activeGrokChatDialog.handleChatEvent(payload)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2373", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("error", f"GrokChat event parse failed: {type(error).__name__}: {error}\n{raw[:1000]}")
        return True

    @Slot(str, str, int, str)
    def onGrokConsoleMessage(self, level: str, message: str, lineNumber: int, sourceID: str) -> None:
        if message and self.handleGrokChatModalEvent(message):
            return
        if message and self.handleJavaScriptNetworkEvent(message):
            return
        if message:
            self.debugPane.append("console", f"{level} {displayUrl(sourceID)}:{lineNumber}: {message}")

    @Slot(str, str)
    def onGrokAlertRequested(self, origin: str, message: str) -> None:
        self.debugPane.append("alert", f"{displayUrl(origin)}\n{message}")
        QMessageBox.information(self, "JavaScript alert from Grok pane", message)

    def installEarlyGrokScripts(self, profile: QWebEngineProfile) -> None:
        """Install bridge/network hooks at DocumentCreation so app fetch/XHR calls are captured early."""
        if not BRIDGE_JS.exists():
            return
        try:
            script = QWebEngineScript()
            script.setName("SuperGrokBridgeEarlyDomAndNetworkHooks")
            script.setSourceCode(tracedReadText(BRIDGE_JS, encoding="utf-8"))
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            profile.scripts().insert(script)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2403", error, extra={"handler": "except Exception as error:"})
            # Keep browser loading even if this PySide build exposes script injection differently.
            if hasattr(self, "debugPane"):
                self.debugPane.append("error", f"Early script injection failed: {type(error).__name__}: {error}")
            else:
                print(f"[WARNING:webengine] Early script injection failed: {type(error).__name__}: {error}")

    def createGrokWebView(self) -> ManagedWebView:
        profileDir = self.config.profileDir or os.environ.get("SUPERGROK_PROFILE_DIR", "")
        targetName = normalizeChatTarget(getattr(self.config, "target", "grok"))
        providerLabel = chatProviderLabel(targetName)
        view = ManagedWebView(f"Live {providerLabel} Website", self.showSourceForView, self.openDevToolsForPage, self)

        # Keep the live AI website on its own persistent profile. Grok and
        # ChatGPT intentionally use separate default profile folders so a normal
        # Grok login cannot pollute ChatGPT cookies, and ChatGPT can be logged in
        # once through the visible bridge then reused by later CLI chats.
        profile = QWebEngineProfile(f"SuperGrokBridge{providerLabel}Profile", self)
        path = Path(profileDir).expanduser().resolve() if profileDir else (DATA / f"{targetName}_profile")
        storagePath = path / "storage"
        cachePath = path / "cache"
        storagePath.mkdir(parents=True, exist_ok=True)
        cachePath.mkdir(parents=True, exist_ok=True)

        # Login persistence matters here: if this profile behaves like an
        # off-the-record/private profile, Grok/Google cookies disappear and the
        # user has to log in every run. Set disk paths, disk cache, and forced
        # persistent cookies before any page is created for this profile.
        profile.setPersistentStoragePath(str(storagePath))
        profile.setCachePath(str(cachePath))
        try:
            profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2433", error, extra={"handler": "except Exception:"})
            # Older PySide/Qt builds may expose enum names slightly differently;
            # keep loading instead of breaking the browser.
            pass
        try:
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2439", error, extra={"handler": "except Exception:"})
            pass
        try:
            profile.setHttpCacheMaximumSize(256 * 1024 * 1024)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2443", error, extra={"handler": "except Exception:"})
            pass

        self.grokProfile = profile
        self.grokProfileRoot = path
        self.grokProfileStoragePath = storagePath  # noqa: redundant
        self.grokProfileCachePath = cachePath  # noqa: redundant
        self.installEarlyGrokScripts(profile)

        page = BridgeWebPage(profile, view)
        view.setPage(page)
        enableWebSettings(view.settings(), localContentCanAccessRemote=True)
        self.requestInterceptor = WebRequestInterceptor("Live Grok Website", view)
        self.requestInterceptor.requestSeen.connect(self.onRequestSeen)
        profile.setUrlRequestInterceptor(self.requestInterceptor)
        return view

    @Slot(dict)
    def onRequestSeen(self, payload: dict[str, Any]) -> None:
        try:
            writeTrafficLog({"eventType": "qt-request-metadata", "payload": payload})
            sessionLog({"eventType": "grok-request-metadata", "payload": payload})
            requestId = self.requestDatabase.record(payload)
            self.requestPane.addRequest(requestId, payload)
            if self.config.debug:
                self.debugPane.append("request", f"#{requestId} {payload.get('method', '')} {payload.get('displayUrl', '')}")
        except Exception as error:
            recordException("supergrok_bridge/app.py:2468", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("error", f"Request log failed: {type(error).__name__}: {error}")

    def sendTrustedChatGptInput(self, message: str, sendId: str, logCallback: Callable[[str, str], None] | None = None) -> None:
        # Focus ChatGPT's composer and send real Qt paste/Enter keystrokes.
        def _log(kind: str, text: str) -> None:
            try:
                if callable(logCallback):
                    logCallback(kind, text)
                else:
                    self.debugPane.append(kind, text)
            except Exception:  # swallow-ok
                pass

        if QTest is None:
            _log("warn", f"{sendId}: PySide6.QtTest.QTest is unavailable; cannot run trusted paste/Enter fallback")
            return
        text = str(message or "")
        if not text.strip():
            _log("warn", f"{sendId}: trusted paste fallback skipped because message is empty")
            return
        focusScript = r"""
(function() {
  function visible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }
  const selectors = ['#prompt-textarea', 'textarea#prompt-textarea', 'textarea', 'div#prompt-textarea[contenteditable="true"]', '[contenteditable="true"][role="textbox"]', '[contenteditable="true"]', '[role="textbox"]'];
  let editor = null;
  let selector = '';
  for (const sel of selectors) {
    try {
      const found = Array.from(document.querySelectorAll(sel)).find(visible);
      if (found) { editor = found; selector = sel; break; }
    } catch (_ignored) {}
  }
  if (!editor) return JSON.stringify({ok:false, error:'composer editor not found', url: location.href, title: document.title});
  try { editor.scrollIntoView({block:'center', inline:'center'}); } catch (_ignored) {}
  try { editor.focus(); } catch (_ignored) {}
  try { editor.click(); } catch (_ignored) {}
  try {
    if (typeof editor.select === 'function') editor.select();
    else {
      const selection = window.getSelection && window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(editor);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  } catch (_ignored) {}
  return JSON.stringify({ok:true, selector: selector, tagName: editor.tagName, id: editor.id || '', valueLength: String(editor.value || editor.innerText || editor.textContent || '').length, url: location.href, title: document.title});
})();
"""
        try:
            self.grokView.setFocus(Qt.FocusReason.MouseFocusReason)
            self.grokView.activateWindow()
            self.raise_()
            self.activateWindow()
        except Exception as error:
            recordException("supergrok_bridge/app.py:sendTrustedChatGptInput.focusWindow", error, extra={"handler": "except Exception as error:"})
        def afterFocus(result: Any) -> None:
            _log("grokchat", f"{sendId}: ChatGPT trusted input focus result: {result}")
            try:
                clipboard = QApplication.clipboard()
                oldText = clipboard.text() if clipboard is not None else ""
                if clipboard is not None:
                    clipboard.setText(text)
                view = self.grokView
                view.setFocus(Qt.FocusReason.MouseFocusReason)
                QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                QTimer.singleShot(90, lambda: QTest.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))
                QTimer.singleShot(520, lambda: QTest.keyClick(view, Qt.Key.Key_Return))
                if clipboard is not None:
                    QTimer.singleShot(1800, lambda: clipboard.setText(oldText))
                _log("grokchat", f"{sendId}: ChatGPT trusted paste/Enter queued chars={len(text)}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:sendTrustedChatGptInput.afterFocus", error, extra={"handler": "except Exception as error:"})
                _log("error", f"{sendId}: ChatGPT trusted paste/Enter failed: {type(error).__name__}: {error}")
        try:
            self.grokView.page().runJavaScript(focusScript, afterFocus)
        except Exception as error:
            recordException("supergrok_bridge/app.py:sendTrustedChatGptInput", error, extra={"handler": "except Exception as error:"})
            _log("error", f"{sendId}: ChatGPT trusted input focus script failed: {type(error).__name__}: {error}")

    def startBridgeCommandService(self) -> None:
        if self.bridgeCommandServer is not None:
            return
        self.bridgeCommandServer = BridgeCommandServer(self, int(self.config.servicePort or BRIDGE_SERVICE_PORT), self)
        if not self.bridgeCommandServer.start():
            self.debugPane.append("error", f"Bridge command service failed to bind port {self.config.servicePort}")
        self._ensureTrayIcon()

    def _ensureTrayIcon(self) -> None:
        """Create a system-tray indicator the user can use to see the bridge is
        live and to close it without hunting for the terminal. Idempotent: if a
        tray was already created on a previous startBridgeCommandService call,
        we re-use it. Silent no-op on platforms without system tray support.
        """
        existing = getattr(self, "_trayIcon", None)
        if existing is not None:
            return
        try:
            from PySide6.QtWidgets import QSystemTrayIcon, QMenu  # depcheck-ok
            from PySide6.QtGui import QIcon, QAction  # depcheck-ok
        except Exception as error:
            recordException("supergrok_bridge/app.py:tray-import", error, extra={"handler": "_ensureTrayIcon import"})
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.debugPane.append("tray", "system tray not available; skipping indicator")
            return
        iconPath = Path(__file__).resolve().parent / "assets" / "supergrok_icon.svg"
        icon = QIcon(str(iconPath)) if iconPath.exists() else self.windowIcon()
        tray = QSystemTrayIcon(icon, self)
        provider = chatProviderLabel(getattr(self.config, "target", "grok"))
        port = int(getattr(self.config, "servicePort", BRIDGE_SERVICE_PORT) or BRIDGE_SERVICE_PORT)
        tray.setToolTip(f"SuperGrok Bridge — {provider} — running on port {port}")
        menu = QMenu()
        showAction = QAction(loc("Show bridge window"), self)
        showAction.triggered.connect(self._trayShowWindow)
        hideAction = QAction(loc("Hide bridge window"), self)
        hideAction.triggered.connect(self._trayHideWindow)
        statusAction = QAction(loc(f"Port: {port} | Target: {provider}"), self)
        statusAction.setEnabled(False)
        quitAction = QAction(loc("Stop bridge server"), self)
        quitAction.triggered.connect(self._trayQuit)
        menu.addAction(statusAction)
        menu.addSeparator()
        menu.addAction(showAction)
        menu.addAction(hideAction)
        menu.addSeparator()
        menu.addAction(quitAction)
        tray.setContextMenu(menu)
        tray.activated.connect(self._onTrayActivated)
        tray.show()
        self._trayIcon = tray
        self._trayMenu = menu
        self.debugPane.append("tray", f"system tray indicator ready (port={port} provider={provider})")

    def _onTrayActivated(self, reason: object) -> None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon  # depcheck-ok
            if reason == QSystemTrayIcon.ActivationReason.Trigger or reason == QSystemTrayIcon.ActivationReason.DoubleClick:
                self._trayShowWindow()
        except Exception as error:
            recordException("supergrok_bridge/app.py:tray-activated", error, extra={"reason": str(reason)})

    def _trayShowWindow(self) -> None:
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()  # noqa: redundant
        except Exception as error:
            recordException("supergrok_bridge/app.py:tray-show", error)

    def _trayHideWindow(self) -> None:
        try:
            self.hide()
        except Exception as error:
            recordException("supergrok_bridge/app.py:tray-hide", error)

    def _trayQuit(self) -> None:
        try:
            tray = getattr(self, "_trayIcon", None)
            if tray is not None:
                tray.hide()
            self.debugPane.append("tray", "bridge stop requested via tray menu")
            from PySide6.QtCore import QCoreApplication  # depcheck-ok
            QCoreApplication.quit()
        except Exception as error:
            recordException("supergrok_bridge/app.py:tray-quit", error)

    def enqueueBridgeChat(self, message: str, callback: Callable[[dict[str, Any]], None], timeoutSeconds: int = 240, attachments: object = None, target: object = "") -> None:
        attachmentList = attachments if isinstance(attachments, list) else []
        targetName = normalizeChatTarget(target or getattr(self.config, "target", "grok"))
        if not self.grokLoadFinishedSeen:
            self.cliChatQueue.append((str(message), callback, int(timeoutSeconds or 240), attachmentList, targetName))
            self.debugPane.append("bridge-service", f"queued chat until browser page load finishes; queue={len(self.cliChatQueue)}")
            return
        self.cliChatQueue.append((str(message), callback, int(timeoutSeconds or 240), attachmentList, targetName))
        self.startNextBridgeChatJob()

    def startNextBridgeChatJob(self) -> None:
        if self.activeCliChatJob is not None or not self.cliChatQueue:
            return
        queued = self.cliChatQueue.pop(0)
        if len(queued) == 5:
            message, callback, timeoutSeconds, attachments, targetName = queued
        elif len(queued) == 4:
            message, callback, timeoutSeconds, attachments = queued
            targetName = normalizeChatTarget(getattr(self.config, "target", "grok"))
        else:
            message, callback, timeoutSeconds = queued
            attachments = []
            targetName = normalizeChatTarget(getattr(self.config, "target", "grok"))
        def finished(response: dict[str, Any]) -> None:
            self.activeCliChatJob = None
            callback(response)
            QTimer.singleShot(0, self.startNextBridgeChatJob)
        self.activeCliChatJob = GrokBridgeChatJob(self.grokView.page(), message, self.debugPane, timeoutSeconds, finished, self, attachments=attachments, target=targetName)
        self.activeCliChatJob.begin()

    def activeBridgeChatProgress(self) -> dict[str, Any]:
        job = self.activeCliChatJob
        if job is None:
            return {"active": False}
        try:
            probe = dict(getattr(job, "lastProbeSummary", {}) or {})
            return {
                "active": True,
                "sendId": str(getattr(job, "sendId", "") or ""),
                "target": str(getattr(job, "target", getattr(self.config, "target", "grok")) or "grok"),
                "stage": str(getattr(job, "stage", "") or ""),
                "reason": str(getattr(job, "lastReason", "") or ""),
                "elapsedSeconds": round(max(0.0, time.monotonic() - float(getattr(job, "startedAt", time.monotonic()) or time.monotonic())), 3),
                "surfaceAttempts": int(getattr(job, "surfaceAttempts", 0) or 0),
                "reloadAttempts": int(getattr(job, "reloadAttempts", 0) or 0),
                "compositorKickAttempts": int(getattr(job, "compositorKickAttempts", 0) or 0),
                "domSendStarted": bool(getattr(job, "domSendStarted", False)),
                "layoutRendered": bool(probe.get("layoutRendered", False)),
                "promptFound": bool(probe.get("promptFound", False)),
                "sendButtonFound": bool(probe.get("sendButtonFound", False)),
                "url": str(probe.get("url") or self.grokView.url().toString()),
            }
        except Exception as error:
            recordException("supergrok_bridge/app.py:activeBridgeChatProgress", error, extra={"handler": "except Exception as error:"})
            return {"active": True, "error": f"{type(error).__name__}: {error}"}

    def kickCompositorForCli(self, reason: str = "") -> None:
        try:
            self.debugPane.append("warn", f"Compositor kick for CLI chat: {reason}")
            warnLog(f"Compositor kick for CLI chat: {reason}")
            self.config.windowMode = "offscreen-window"
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            # Fully transparent windows can starve Chromium composition on some
            # Windows/QtWebEngine builds.  Keep a real opaque surface, lower it,
            # and avoid activation so the CLI stays effectively headless.
            self.setWindowOpacity(1.0)
            self.resize(max(self.width(), 1400), max(self.height(), 900))
            screen = QApplication.primaryScreen()
            geometry = screen.availableGeometry() if screen is not None else None
            left = int(geometry.left()) + 4 if geometry is not None else 4
            top = int(geometry.top()) + 4 if geometry is not None else 4
            self.move(left, top)
            self.show()
            self.lower()
            QTimer.singleShot(250, self.lower)
            QTimer.singleShot(1000, self.lower)
            try:
                self.grokView.setFocus(Qt.FocusReason.OtherFocusReason)
                self.grokView.update()
                self.repaint()
            except Exception:  # swallow-ok
                pass
        except Exception as error:
            recordException("supergrok_bridge/app.py:kickCompositorForCli", error, extra={"reason": reason})

    def revealForHumanRepair(self, reason: str = "") -> None:
        """Show the whole bridge window when headless CLI cannot find Grok surfaces."""
        try:
            self.config.hideWindow = False
            self.config.windowMode = "visible"
        except Exception:  # swallow-ok
            pass
        try:
            self.showAllColumns()
        except Exception:  # swallow-ok
            pass
        try:
            # Undo the transparent/background service surface before showing the
            # bridge for login/captcha/selector repair.  A repair window must be
            # plainly visible and focusable.
            self.setWindowOpacity(1.0)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        except Exception:  # swallow-ok
            pass
        try:
            self.move(120, 120)
            self.resize(max(self.width(), 1200), max(self.height(), 800))
            self.showNormal()
            self.raise_()
            self.activateWindow()  # noqa: redundant
        except Exception as error:
            recordException("supergrok_bridge/app.py:revealForHumanRepair", error, extra={"reason": reason})
        self.debugPane.append("warn", f"Bridge window shown for human repair: {reason}")
        warnLog(f"Bridge window shown for human repair: {reason}")

    def closeEvent(self, event: Any) -> None:
        try:
            self.saveWindowUiState()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2474", error, extra={"handler": "except Exception:"})
            pass
        try:
            self.requestDatabase.close()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2478", error, extra={"handler": "except Exception:"})
            pass
        try:
            self.uiStateDatabase.close()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2482", error, extra={"handler": "except Exception:"})
            pass
        try:
            self.commandPolicyDatabase.close()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2486", error, extra={"handler": "except Exception:"})
            pass
        try:
            self.processDatabase.close()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2490", error, extra={"handler": "except Exception:"})
            pass
        super().closeEvent(event)

    def buildMenus(self) -> None:
        viewMenu = self.menuBar().addMenu("View")
        columnsMenu = viewMenu.addMenu("Columns")
        labels = {
            "debug": "Debug / Scripts Column",
            "chat": "Grok Chat Column",
            "grok": "Live Grok Website Column",
        }
        for key, label in labels.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(lambda checked, columnKey=key: self.setColumnVisible(columnKey, checked, persist=True))
            columnsMenu.addAction(action)
            self.columnActions[key] = action

        columnsMenu.addSeparator()
        showAllAction = QAction(loc("Show All Columns"), self)
        showAllAction.triggered.connect(self.showAllColumns)
        columnsMenu.addAction(showAllAction)

        debugMenu = self.menuBar().addMenu("Debug")
        processReportAction = QAction(loc("Show Process Table"), self)
        processReportAction.triggered.connect(self.showProcessTableDialog)
        debugMenu.addAction(processReportAction)

        exceptionReportAction = QAction(loc("Show Exception Table"), self)
        exceptionReportAction.triggered.connect(self.showExceptionTableDialog)
        debugMenu.addAction(exceptionReportAction)

        debuggerMenuAction = QAction(loc("Show start.py Debugger Menu Help"), self)
        debuggerMenuAction.triggered.connect(self.showStartDebuggerMenuHelp)
        debugMenu.addAction(debuggerMenuAction)

    @Slot()
    def showProcessTableDialog(self) -> None:
        rows = self.processDatabase.allRows()
        source = json.dumps({"processDb": str(PROCESS_DB), "rows": rows}, ensure_ascii=False, indent=2)
        self.openSourceDialog(source, "json")

    @Slot()
    def showExceptionTableDialog(self) -> None:
        rows = getExceptionDatabase().latest()
        source = json.dumps({"exceptionDb": str(EXCEPTION_DB), "rows": rows}, ensure_ascii=False, indent=2)
        self.openSourceDialog(source, "json")

    @Slot()
    def showStartDebuggerMenuHelp(self) -> None:
        source = (
            "start.py debugger entry points\n\n"
            "--debugger-menu        Print the launcher/debugger menu and exit.\n"
            "--debug                Enable launcher/app trace output.\n"
            "--remote-debug-port N  Enable Chromium DevTools target list.\n"
            "--process-ttl N        ToolCall subprocess TTL in seconds.\n"
            "--monkey               Run vendored /vendor/claude reports and exit.\n"
            "Exceptions DB: data/supergrok_bridge_exceptions.sqlite3\n\n"
            "Flatline status: placeholder/drop-in only in this CWV; full debugger package not bundled yet.\n"
        )
        self.openSourceDialog(source, "text")

    def restoreWindowUiState(self) -> None:
        for key in ("debug", "chat", "grok"):
            visible = self.uiStateDatabase.getBool(f"column.{key}.visible", True)
            self.setColumnVisible(key, visible, persist=False)
        sizesRaw = self.uiStateDatabase.get("mainSplitter.sizes", "")
        if sizesRaw:
            try:
                sizes = [int(part) for part in sizesRaw.split(",") if part.strip()]
                if len(sizes) == 3:
                    self.mainSplitter.setSizes(sizes)
            except Exception as error:
                recordException("supergrok_bridge/app.py:2553", error, extra={"handler": "except Exception:"})
                pass

    def saveWindowUiState(self) -> None:
        for key, widget in self.columnWidgets.items():
            self.uiStateDatabase.setBool(f"column.{key}.visible", widget.isVisible())
        try:
            self.uiStateDatabase.set("mainSplitter.sizes", ",".join(str(size) for size in self.mainSplitter.sizes()))
        except Exception as error:
            recordException("supergrok_bridge/app.py:2561", error, extra={"handler": "except Exception:"})
            pass

    @Slot()
    def showAllColumns(self) -> None:
        for key in ("debug", "chat", "grok"):
            action = self.columnActions.get(key)
            if action is not None:
                action.setChecked(True)
            else:
                self.setColumnVisible(key, True, persist=True)

    def setColumnVisible(self, key: str, visible: bool, persist: bool = True) -> None:
        widget = self.columnWidgets.get(key)
        if widget is None:
            return
        widget.setVisible(bool(visible))
        action = self.columnActions.get(key)
        if action is not None and action.isChecked() != bool(visible):
            previous = action.blockSignals(True)
            action.setChecked(bool(visible))
            action.blockSignals(previous)
        if persist:
            self.uiStateDatabase.setBool(f"column.{key}.visible", bool(visible))
            try:
                self.uiStateDatabase.set("mainSplitter.sizes", ",".join(str(size) for size in self.mainSplitter.sizes()))
            except Exception as error:
                recordException("supergrok_bridge/app.py:2587", error, extra={"handler": "except Exception:"})
                pass
        try:
            self.statusBar().showMessage(f"{key.title()} column {'shown' if visible else 'hidden'}", 2500)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2591", error, extra={"handler": "except Exception:"})
            pass

    def buildToolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        providerLabel = self.providerLabel
        liveLabel = f"Live {providerLabel} Website"

        reloadAction = QAction(loc(f"Reload {providerLabel}"), self)
        reloadAction.setShortcut(QKeySequence.StandardKey.Refresh)
        reloadAction.triggered.connect(self.grokView.reload)
        toolbar.addAction(reloadAction)

        backAction = QAction(loc("Back"), self)
        backAction.triggered.connect(self.grokView.back)
        toolbar.addAction(backAction)

        forwardAction = QAction(loc("Forward"), self)
        forwardAction.triggered.connect(self.grokView.forward)
        toolbar.addAction(forwardAction)

        devToolsAction = QAction(loc("DevTools"), self)
        devToolsAction.triggered.connect(lambda: self.openDevToolsForPage(self.grokView.page(), liveLabel, False))
        toolbar.addAction(devToolsAction)

        inspectAction = QAction(loc(f"Inspect {providerLabel}"), self)
        inspectAction.triggered.connect(lambda: self.openDevToolsForPage(self.grokView.page(), liveLabel, True))
        toolbar.addAction(inspectAction)

        viewSourceAction = QAction(loc(f"View {providerLabel} Source"), self)
        viewSourceAction.triggered.connect(lambda: self.showSourceForView(self.grokView))
        toolbar.addAction(viewSourceAction)

        chatSourceAction = QAction(loc("View Chat Source"), self)
        chatSourceAction.triggered.connect(lambda: self.showSourceForView(self.chat.chatView))
        toolbar.addAction(chatSourceAction)

        debugSourceAction = QAction(loc("View Debug Source"), self)
        debugSourceAction.triggered.connect(lambda: self.showSourceForView(self.debugPane.view))
        toolbar.addAction(debugSourceAction)

        remoteAction = QAction(loc("Remote DevTools URL"), self)
        remoteAction.triggered.connect(self.openRemoteDevToolsUrl)
        toolbar.addAction(remoteAction)

        profileAction = QAction(loc("Show Grok Profile"), self)
        profileAction.triggered.connect(self.showGrokProfileInfo)
        toolbar.addAction(profileAction)

        clearProfileAction = QAction(loc("Clear Grok Profile"), self)
        clearProfileAction.triggered.connect(self.clearGrokProfile)
        toolbar.addAction(clearProfileAction)


    def grokProfileInfoText(self) -> str:
        profile = self.grokProfile
        root = self.grokProfileRoot or Path("")
        storage = self.grokProfileStoragePath or Path("")
        cache = self.grokProfileCachePath or Path("")  # noqa: redundant
        pieces = [
            f"root={root}",
            f"storage={storage}",
            f"cache={cache}",
        ]
        if profile is not None:
            try:
                pieces.append(f"offTheRecord={profile.isOffTheRecord()}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:2658", error, extra={"handler": "except Exception as error:"})
                pieces.append(f"offTheRecord=unknown:{type(error).__name__}")
            try:
                pieces.append(f"cookiesPolicy={enumName(profile.persistentCookiesPolicy())}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:2662", error, extra={"handler": "except Exception as error:"})
                pieces.append(f"cookiesPolicy=unknown:{type(error).__name__}")
            try:
                pieces.append(f"httpCacheType={enumName(profile.httpCacheType())}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:2666", error, extra={"handler": "except Exception as error:"})
                pieces.append(f"httpCacheType=unknown:{type(error).__name__}")
            try:
                pieces.append(f"persistentStoragePath={profile.persistentStoragePath()}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:2670", error, extra={"handler": "except Exception:"})
                pass
            try:
                pieces.append(f"cachePath={profile.cachePath()}")
            except Exception as error:
                recordException("supergrok_bridge/app.py:2674", error, extra={"handler": "except Exception:"})
                pass
        return "\n".join(pieces)

    def traceGrokProfileStatus(self) -> None:
        text = self.grokProfileInfoText()
        try:
            self.debugPane.append("profile", text)
        except Exception as error:
            recordException("supergrok_bridge/app.py:2682", error, extra={"handler": "except Exception:"})
            print(f"[INFO:profile] {text}", file=sys.stderr)
        writeTrafficLog({"eventType": "grok-profile", "profile": text})

    @Slot()
    def showGrokProfileInfo(self) -> None:
        QMessageBox.information(self, "Grok Profile", self.grokProfileInfoText())

    @Slot()
    def clearGrokProfile(self) -> None:
        root = self.grokProfileRoot
        if root is None:
            QMessageBox.information(self, "Clear Grok Profile", "No Grok profile path is configured yet.")
            return
        answer = QMessageBox.question(
            self,
            "Clear Grok Profile",
            "This clears the persisted Grok/Google login profile for this app. You will need to log in again. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.grokView.setUrl(QUrl("about:blank"))
        try:
            if self.grokProfile is not None:
                self.grokProfile.cookieStore().deleteAllCookies()
        except Exception as error:
            recordException("supergrok_bridge/app.py:2707", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("profile", f"cookie delete failed: {type(error).__name__}: {error}")
        QTimer.singleShot(500, lambda: self._deleteGrokProfileFiles(root))

    def _deleteGrokProfileFiles(self, root: Path) -> None:
        removed: list[str] = []
        errors: list[str] = []
        for child in (root / "storage", root / "cache"):
            try:
                if child.exists():
                    shutil.rmtree(child)
                    removed.append(str(child))
                child.mkdir(parents=True, exist_ok=True)
            except Exception as error:
                recordException("supergrok_bridge/app.py:2720", error, extra={"handler": "except Exception as error:"})
                errors.append(f"{child}: {type(error).__name__}: {error}")
        self.debugPane.append("profile", "cleared profile paths:\n" + "\n".join(removed or ["nothing removed"]))
        if errors:
            self.debugPane.append("profile", "clear errors:\n" + "\n".join(errors))
            QMessageBox.warning(self, "Clear Grok Profile", "Some profile files could not be removed. Close the app and delete the profile folder manually if login stays stuck.\n\n" + "\n".join(errors[:5]))
        else:
            QMessageBox.information(self, "Clear Grok Profile", "Grok profile cleared. Reload Grok and log in again.")

    def connectSignals(self) -> None:
        self.chat.input.returnPressed.connect(self.sendCurrentPrompt)
        self.chat.sendButton.clicked.connect(self.sendCurrentPrompt)
        self.chat.probeButton.clicked.connect(self.controller.probeDom)
        self.chat.sourceButton.clicked.connect(lambda: self.showSourceForView(self.grokView))
        self.grokView.loadFinished.connect(self.onGrokLoadFinished)
        self.debugPane.scriptsList.itemDoubleClicked.connect(self.runSelectedScript)
        self.messagesButton.clicked.connect(self.controller.dumpMessages)
        self.grokChatButton.clicked.connect(self.openGrokChatDialog)
        self.sayHiButton.clicked.connect(self.controller.sayHi)
        self.injectButton.clicked.connect(self.openInjectDialog)
        self.grokAddress.returnPressed.connect(self.navigateGrokAddress)
        self.grokGoButton.clicked.connect(self.navigateGrokAddress)  # noqa: redundant
        self.grokView.urlChanged.connect(self.onGrokUrlChanged)  # noqa: redundant
        self.mainSplitter.splitterMoved.connect(lambda _pos, _index: self.saveWindowUiState())

    @Slot()
    def sendCurrentPrompt(self) -> None:
        prompt = self.chat.input.text().strip()
        if not prompt:
            return
        self.chat.input.clear()
        self.controller.sendPrompt(prompt)

    @Slot(QListWidgetItem)
    def runSelectedScript(self, item: QListWidgetItem) -> None:
        path = self.debugPane.scriptPathFromItem(item)
        try:
            script = tracedReadText(path, encoding="utf-8")
        except Exception as error:
            recordException("supergrok_bridge/app.py:2758", error, extra={"handler": "except Exception as error:"})
            self.debugPane.append("error", f"Could not read {path}: {type(error).__name__}: {error}")
            return
        self.controller.runRawScript(path.name, script)

    @Slot()
    def navigateGrokAddress(self) -> None:
        url = navigationUrl(self.grokAddress.text())
        if not url.isValid() or url.isEmpty():
            self.statusBar().showMessage(loc("Enter a URL or search text first."), 4000)
            return
        self.debugPane.append("system", f"Navigating Grok pane to: {displayUrl(url)}")
        self.grokView.load(url)

    @Slot(QUrl)
    def onGrokUrlChanged(self, url: QUrl) -> None:
        fullUrl = url.toString()
        if self.grokAddress.text() != fullUrl:
            self.grokAddress.setText(fullUrl)
        self.statusBar().showMessage(f"Grok URL: {displayUrl(url)}", 5000)

    @Slot()
    def openGrokChatDialog(self) -> None:
        dialog = GrokChatDialog(self.grokView.page(), self.requestDatabase, self.debugPane, self)
        self.activeGrokChatDialog = dialog
        try:
            dialog.exec()  # qt-main-thread-ok
        finally:
            if self.activeGrokChatDialog is dialog:
                self.activeGrokChatDialog = None

    @Slot()
    def openInjectDialog(self) -> None:
        defaultCode = self.defaultInjectionCode()
        dialog = InjectionDialog(defaultCode, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:  # qt-main-thread-ok
            self.controller.injectCode(dialog.code())

    def defaultInjectionCode(self) -> str:
        jquerySource = ""
        if JQUERY.exists():
            jquerySource = tracedReadText(JQUERY, encoding="utf-8")
        return f"""// Default injection test.
// This manually injects the local jQuery build/shim into the Grok page, then turns the page blue.
(function() {{
  if (!window.jQuery) {{
{jquerySource}
  }}
  if (window.jQuery) {{
    window.jQuery('html, body').css('background', 'blue');
    console.log('manual inject: jQuery background test ran');
    return 'jQuery injected/tested: background set to blue';
  }}
  document.documentElement.style.background = 'blue';
  if (document.body) document.body.style.background = 'blue';
  console.log('manual inject: fallback background test ran');
  return 'fallback background set to blue';
}})();
"""

    @Slot(bool)
    def onGrokLoadFinished(self, ok: bool) -> None:
        self.grokLoadFinishedSeen = True
        self.grokLoadOk = bool(ok)
        status = "loaded" if ok else "load failed"
        currentUrl = self.grokView.url()
        if self.grokAddress.text() != currentUrl.toString():
            self.grokAddress.setText(currentUrl.toString())
        self.chat.append("system", f"browser {status}: {displayUrl(currentUrl)}")
        self.debugPane.append("system", f"Grok page {status}: {displayUrl(currentUrl)}")
        self.controller.loadBridge(lambda _ignored: self.grokView.page().runJavaScript("window.SuperGrokBridgeDom && window.SuperGrokBridgeDom.installNetworkHooks && JSON.stringify(window.SuperGrokBridgeDom.installNetworkHooks())", lambda result: self.debugPane.append("system", f"network hooks: {result}")))
        QTimer.singleShot(250, self.startNextBridgeChatJob)

    def showSourceForView(self, view: QWebEngineView) -> None:
        script = """(function() {
  try {
    if (document && document.documentElement) {
      return '<!doctype html>\\n' + document.documentElement.outerHTML;
    }
    if (document && document.body) {
      return document.body.outerHTML;
    }
    return '';
  } catch (error) {
    return '<!-- JS source extraction failed: ' + String(error && error.message ? error.message : error) + ' -->';
  }
})();"""
        view.page().runJavaScript(script, lambda source: self._openSourceFromJavaScript(view, source))

    def _openSourceFromJavaScript(self, view: QWebEngineView, source: Any) -> None:
        if isinstance(source, str) and source.strip():
            self.openSourceDialog(source, "markup")
            return
        view.page().toHtml(
            lambda fallback: self.openSourceDialog(
                fallback or "<!-- empty source returned by JavaScript and QWebEnginePage.toHtml() -->",
                "markup",
            )
        )

    def openSourceDialog(self, source: str, language: str = "markup") -> None:
        if not source:
            source = "<!-- empty source returned by source extractor -->"
        dialog = SourceDialog(source, language, self)
        dialog.exec()  # qt-main-thread-ok

    def openRemoteDevToolsUrl(self) -> None:
        if not self.config.remoteDebugPort:
            QMessageBox.information(self, "DevTools", "Remote DevTools is disabled. Start with --remote-debug-port 9222.")
            return
        QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.config.remoteDebugPort}"))

    def openDevToolsForPage(self, page: QWebEnginePage, label: str, inspectElement: bool = False) -> None:
        # Embedded Qt DevTools varies by PySide6/QtWebEngine build and has been flaky
        # on the target Windows runtime. Prefer Chromium's remote-debugging target
        # list, which is the same session/profile and works from any Chromium browser.
        if self.config.remoteDebugPort:
            url = QUrl(f"http://127.0.0.1:{self.config.remoteDebugPort}")
            QDesktopServices.openUrl(url)
            self.statusBar().showMessage(
                f"Opened remote Chromium DevTools target list for {label}: {url.toString()}",
                8000,
            )
            return

        key = id(page)
        dock = self.devToolsDocks.get(key)
        if dock is None:
            dock = QDockWidget(f"Chromium DevTools - {label}", self)
            dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
            devToolsView = QWebEngineView(dock)
            enableWebSettings(devToolsView.settings(), localContentCanAccessRemote=True)
            dock.setWidget(devToolsView)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
            self.devToolsDocks[key] = dock

            devToolsPage = devToolsView.page()
            if hasattr(page, "setDevToolsPage"):
                page.setDevToolsPage(devToolsPage)
            elif hasattr(devToolsPage, "setInspectedPage"):
                devToolsPage.setInspectedPage(page)
            else:
                QMessageBox.information(
                    self,
                    "DevTools",
                    "Embedded DevTools is not exposed by this PySide6 build. Start with --remote-debug-port 9222 and use Remote DevTools URL.",
                )
        dock.show()
        dock.raise_()
        if inspectElement:
            inspectAction = getattr(QWebEnginePage.WebAction, "InspectElement", None)
            if inspectAction is not None:
                page.triggerAction(inspectAction)
            else:
                self.statusBar().showMessage(loc("InspectElement action is not exposed by this PySide6 build; DevTools dock is open."), 6000)


@dataclass
class ApplicationPhase:
    name: str
    callback: Callable[[], Any]


class ApplicationLifecycleController:
    """Small startup lifecycle wrapper so app startup is phase-owned and traceable."""

    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.phases: list[ApplicationPhase] = []
        self.results: dict[str, Any] = {}

    def registerPhase(self, name: str, callback: Callable[[], Any]) -> None:
        self.phases.append(ApplicationPhase(name, callback))

    def trace(self, name: str, outcome: str, started: float, error: Exception | None = None) -> None:
        elapsed = time.time() - started
        payload: dict[str, Any] = {"eventType": "app-lifecycle-phase", "phase": name, "outcome": outcome, "elapsedSeconds": round(elapsed, 3)}
        if error is not None:
            payload["error"] = f"{type(error).__name__}: {error}"
        writeTrafficLog(payload)
        if self.debug or error is not None:
            suffix = f" error={payload.get('error')}" if error is not None else ""
            print(f"[PHASE:app] {name} {outcome} {elapsed:.3f}s{suffix}", file=sys.stderr)

    def run(self) -> int:
        exitCode = 0
        for phase in self.phases:
            started = time.time()
            try:
                result = phase.callback()
                self.results[phase.name] = result
                if isinstance(result, int):
                    exitCode = result
                self.trace(phase.name, "success", started)
            except Exception as error:
                recordException("supergrok_bridge/app.py:2949", error, extra={"handler": "except Exception as error:"})
                self.trace(phase.name, "failed", started, error)
                raise
        return int(exitCode)


def runApplication(initialUrl: str, target: str = "grok", debug: bool = False, profileDir: str = "", remoteDebugPort: int = 9222, processTtlSeconds: int = PROCESS_DEFAULT_TTL_SECONDS, serviceMode: bool = False, servicePort: int = BRIDGE_SERVICE_PORT, hideWindow: bool = False, windowMode: str = "visible", offscreenMode: str = "auto") -> int:
    lifecycle = ApplicationLifecycleController(debug=debug)
    state: dict[str, Any] = {}

    def createApplication() -> None:
        state["app"] = QApplication.instance() or QApplication(sys.argv)

    def createConfig() -> None:
        targetName = normalizeChatTarget(target or chatTargetFromUrl(initialUrl))
        state["config"] = AppConfig(initialUrl=initialUrl or chatProviderHomeUrl(targetName), target=targetName, debug=debug, profileDir=profileDir, remoteDebugPort=remoteDebugPort, processTtlSeconds=max(1, int(processTtlSeconds or PROCESS_DEFAULT_TTL_SECONDS)), serviceMode=bool(serviceMode), servicePort=int(servicePort or BRIDGE_SERVICE_PORT), hideWindow=bool(hideWindow), windowMode=str(windowMode or "visible"), offscreenMode=str(offscreenMode or "auto"))

    def createWindow() -> None:
        state["window"] = SuperGrokBridgeWindow(state["config"])

    def showWindow() -> None:
        window = state["window"]
        config = state["config"]
        mode = str(getattr(config, "windowMode", "visible") or "visible").strip().lower()
        debugLog("offscreen", f"showWindow mode={mode} offscreenMode={getattr(config, 'offscreenMode', 'auto')} qtQpa={os.environ.get('QT_QPA_PLATFORM', '')}")
        if mode == "offscreen-window":
            window.resize(max(window.width(), 1200), max(window.height(), 800))
            if os.name == "nt":
                # Windows QtWebEngine needs a real composited native surface for
                # Chromium to finish hydrating the page.  Moving the window to
                # 32000,32000 or hiding it can leave document.body.innerText
                # empty even though scripts/config loaded.  Keep a real on-screen
                # surface, make it effectively invisible, and push it behind the
                # user's normal windows.  --show-bridge restores opacity/focus.
                try:
                    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                    # Keep the Chromium surface fully composited.  Near-zero
                    # opacity caused Grok to load scripts but never paint the
                    # React composer on some Windows/QtWebEngine builds.
                    window.setWindowOpacity(1.0)
                except Exception as error:
                    recordException("supergrok_bridge/app.py:offscreen-window-opacity", error, extra={"mode": mode})
                try:
                    screen = QApplication.primaryScreen()
                    geometry = screen.availableGeometry() if screen is not None else None
                    left = int(geometry.left()) + 8 if geometry is not None else 8
                    top = int(geometry.top()) + 8 if geometry is not None else 8
                    window.move(left, top)
                except Exception as error:
                    recordException("supergrok_bridge/app.py:offscreen-window-position", error, extra={"mode": mode})
                    window.move(8, 8)
                window.show()
                try:
                    window.lower()
                    QTimer.singleShot(250, window.lower)
                    QTimer.singleShot(1000, window.lower)
                except Exception:  # swallow-ok
                    pass
            else:
                window.move(32000, 32000)
                window.show()
        elif mode == "minimized":
            window.showMinimized()
        else:
            window.show()
            if bool(getattr(config, "hideWindow", False)) or mode == "hidden":
                QTimer.singleShot(0, window.hide)

    def enterEventLoop() -> int:
        return int(state["app"].exec())

    lifecycle.registerPhase("create QApplication", createApplication)
    lifecycle.registerPhase("create AppConfig", createConfig)
    lifecycle.registerPhase("create SuperGrokBridgeWindow", createWindow)  # noqa: redundant
    lifecycle.registerPhase("show main window", showWindow)  # noqa: redundant
    lifecycle.registerPhase("enter Qt event loop", enterEventLoop)
    return lifecycle.run()
