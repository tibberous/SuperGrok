#!/usr/bin/env python3
# ============================================================================
#  SuperGrok Bridge
#  ---------------------------------------------------------------------------
#  A QtWebEngine bridge that hosts Grok, ChatGPT, Gemini, and Claude web
#  sessions in a single persistent-profile Qt window, with a CLI for headless
#  prompts, attachments, and a resident bridge service.
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

import argparse
import base64
import builtins
import hashlib
import mimetypes
import importlib
import importlib.util
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import textwrap
import time
import traceback
from pathlib import Path
from typing import Any

try:
    from exception_log import recordException as _recordException
except Exception:  # swallow-ok: launcher must still print dependency failures.
    _recordException = None

APP_NAME = "SuperGrok Bridge"
APP_VERSION = "1.0.1"
DEFAULT_GROK_URL = "https://grok.com/"
DEFAULT_CHATGPT_URL = "https://chatgpt.com/"
DEFAULT_GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_CLAUDE_URL = "https://claude.ai/new"
DEFAULT_URL = DEFAULT_GROK_URL
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
REPORTS = ROOT / "reports"
VENDOR_CLAUDE = ROOT / "vendor" / "claude"
DEBUGGER_SURFACES = (
    "heartbeat",
    "poll",
    "vardump",
    "accepts-proxy",
    "bridge-service",
    "chat",
)
BRIDGE_SERVICE_HOST = "127.0.0.1"
BRIDGE_SERVICE_PORT = int(os.environ.get("SUPERGROK_BRIDGE_PORT", "8767") or "8767")
OFFSCREEN_MODE_AUTO = "auto"
OFFSCREEN_MODE_HIDDEN = "hidden"
OFFSCREEN_MODE_OFFSCREEN_WINDOW = "offscreen-window"
OFFSCREEN_MODE_MINIMIZED = "minimized"
OFFSCREEN_MODE_QT = "qt"
OFFSCREEN_MODE_XVFB = "xvfb"
OFFSCREEN_MODE_XDUMMY = "xdummy"
OFFSCREEN_MODE_XPRA = "xpra"
WINDOWS_NATIVE_OFFSCREEN_MODES = {OFFSCREEN_MODE_HIDDEN, OFFSCREEN_MODE_OFFSCREEN_WINDOW, OFFSCREEN_MODE_MINIMIZED}
LINUX_CAPTURE_OFFSCREEN_MODES = {OFFSCREEN_MODE_XVFB, OFFSCREEN_MODE_XDUMMY, OFFSCREEN_MODE_XPRA}
KNOWN_STALE_ENTRYPOINTS = (
    "start.py",
    "supergrok_bridge/app.py",
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
ALL_CHAT_TARGET_ALIASES = CHATGPT_TARGET_ALIASES | GROK_TARGET_ALIASES | GEMINI_TARGET_ALIASES | CLAUDE_TARGET_ALIASES
CHATGPT_CLI_FLAG_ALIASES = {
    "--chatgpt", "--chatgtp", "--gpt", "--gtp",
    "-chatgpt", "-chatgtp", "-gpt", "-gtp",
    "/chatgpt", "/chatgtp", "/gpt", "/gtp",
}
GEMINI_CLI_FLAG_ALIASES = {
    "--gemini", "--gem", "--bard",
    "-gemini", "-gem", "-bard",
    "/gemini", "/gem", "/bard",
}
CLAUDE_CLI_FLAG_ALIASES = {
    "--claude", "--anthropic",
    "-claude", "-anthropic",
    "/claude", "/anthropic",
}
CHAT_CLI_FLAG_ALIASES = {"--chat", "-chat", "/chat"} | CHATGPT_CLI_FLAG_ALIASES | GEMINI_CLI_FLAG_ALIASES | CLAUDE_CLI_FLAG_ALIASES


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


def defaultUrlForChatTarget(target: object = "") -> str:
    t = normalizeChatTarget(target)
    if t == "chatgpt":
        return DEFAULT_CHATGPT_URL
    if t == "gemini":
        return DEFAULT_GEMINI_URL
    if t == "claude":
        return DEFAULT_CLAUDE_URL
    return DEFAULT_GROK_URL


def urlLooksLikeTarget(url: object, target: object = "") -> bool:
    text = str(url or "").lower()
    wanted = normalizeChatTarget(target)
    if wanted == "chatgpt":
        return "chatgpt.com" in text or "chat.openai.com" in text
    if wanted == "gemini":
        return "gemini.google.com" in text or "bard.google.com" in text
    if wanted == "claude":
        return "claude.ai" in text
    return "grok.com" in text or "x.ai" in text
DEBUG_LOG = ROOT / "debug.log"
SESSION_LOG = ROOT / "session.log"
TRAFFIC_LOG = DATA / "traffic.log"
BRIDGE_SERVICE_LOG = LOGS / "bridge_service.log"
_ORIGINAL_PRINT = getattr(builtins, "print")


def _appendTextLog(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as handle:  # file-io-ok: launcher debug log.
            handle.write(text)
    except Exception:
        pass


def _safeJson(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    except Exception as error:
        return json.dumps({"jsonError": f"{type(error).__name__}: {error}", "repr": repr(payload)}, ensure_ascii=False)


def _debugJson(kind: str, payload: dict[str, Any]) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    event = dict(payload or {})
    event.setdefault("loggedAt", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    _appendTextLog(DEBUG_LOG, f"[{stamp}] [{str(kind or 'debug').upper()}] {_safeJson(event)}\n")


def _sessionJson(payload: dict[str, Any]) -> None:
    event = dict(payload or {})
    event.setdefault("loggedAt", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z")
    _appendTextLog(SESSION_LOG, _safeJson(event) + "\n")


def resetRunLogs(reason: str = "start") -> None:
    if os.environ.get("SUPERGROK_KEEP_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    for path in (DEBUG_LOG, SESSION_LOG, TRAFFIC_LOG, BRIDGE_SERVICE_LOG):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8", errors="replace")
        except Exception as error:
            _appendTextLog(DEBUG_LOG, f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [WARN] failed to reset {path}: {type(error).__name__}: {error}\n")
    _debugJson("log-reset", {"eventType": "log-reset", "reason": reason, "root": str(ROOT), "pid": os.getpid()})


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
                _appendTextLog(DEBUG_LOG, str(sep).join(str(arg) for arg in args) + str(end))
            except Exception:
                pass
    setattr(builtins, "_supergrok_print_tee_installed", True)
    builtins.print = teePrint


_installPrintTee()


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


def currentSourceSignature() -> dict[str, Any]:
    """Return a stable source fingerprint for code loaded by the resident bridge."""
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
        except Exception as error:
            rel = str(path)
            row = {"path": rel, "error": f"{type(error).__name__}: {error}"}
        rows.append(row)
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return {
        "schema": 1,
        "root": str(ROOT.resolve()),
        "signature": digest.hexdigest(),
        "files": rows,
    }


def sourceSignatureMatches(statusPayload: dict[str, Any]) -> tuple[bool, str]:
    service_signature = statusPayload.get("sourceSignature") if isinstance(statusPayload, dict) else None
    if not isinstance(service_signature, dict):
        return False, "resident bridge did not report a source signature"
    service_digest = str(service_signature.get("signature") or "").strip()
    current_digest = str(currentSourceSignature().get("signature") or "").strip()
    if not service_digest:
        return False, "resident bridge reported a blank source signature"
    if service_digest != current_digest:
        return False, f"resident bridge source signature is stale service={service_digest[:12]} current={current_digest[:12]}"
    service_root = str(service_signature.get("root") or statusPayload.get("root") or "").strip()
    if service_root and Path(service_root).resolve() != ROOT.resolve():
        return False, f"resident bridge root mismatch service={service_root} current={ROOT}"
    return True, "source signature matches current files"


def recordException(context: str, error: BaseException, *, extra: dict[str, Any] | None = None) -> None:
    try:
        if _recordException is not None:
            _recordException(context, error, extra=extra)
            return
    except Exception:  # swallow-ok: fallback recorder cannot recursively fail the launcher.
        pass
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / "launcher_exceptions.log").open("a", encoding="utf-8", errors="replace") as handle:  # file-io-ok: launcher exception fallback log.
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}: {type(error).__name__}: {error}\n")
    except Exception:  # swallow-ok: final fallback cannot do more than avoid recursive launch failure.
        pass


# Backward-compatible alias for older local handoff notes.
recordLauncherException = recordException


def debuggerSurfaceLine() -> str:
    return " ".join(DEBUGGER_SURFACES)


def commandText(command: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def protectedProcessIds() -> set[int]:
    """Return PIDs that this launcher must never taskkill during stale cleanup.

    The important Windows edge case is --chat spawning --serve-bridge: the new
    service process has start.py in its command line, and its parent CLI also has
    start.py in its command line.  Stale cleanup must be allowed to kill older
    resident bridge/debug GUI trees, but it must never kill the current process
    or the parent/ancestor process that is waiting for this service to become
    ready.
    """
    protected = {int(os.getpid() or 0)}
    try:
        parent = int(os.getppid() or 0)
        if parent > 0:
            protected.add(parent)
    except Exception:
        pass
    if os.name != "nt":
        return {pid for pid in protected if pid > 0}
    script = r'''
$ErrorActionPreference = 'SilentlyContinue'
$PidCursor = __PID__
$seen = @{}
$rows = @()
while ($PidCursor -gt 0 -and -not $seen.ContainsKey([string]$PidCursor)) {
  $seen[[string]$PidCursor] = $true
  $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $PidCursor)
  if ($null -eq $proc) { break }
  $rows += [int]$proc.ProcessId
  $PidCursor = [int]$proc.ParentProcessId
}
$rows | ConvertTo-Json -Compress
'''.replace("__PID__", str(os.getpid()))
    try:
        completed = managedSubprocessRun(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=10, debug=False)
        raw = (completed.stdout or "").strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, int):
                protected.add(int(data))
            elif isinstance(data, list):
                for item in data:
                    try:
                        protected.add(int(item))
                    except Exception:
                        pass
    except Exception as error:
        recordException("start.py.protectedProcessIds", error)
    return {pid for pid in protected if pid > 0}


def managedSubprocessRun(command: list[str], *, cwd: Path | None = None, timeout: int = 120, debug: bool = False) -> subprocess.CompletedProcess[str]:
    if debug:
        print(f"[TRACE:start-process] {commandText(command)} timeout={timeout}", file=sys.stderr, flush=True)
    try:
        return subprocess.run(  # lifecycle-bypass-ok phase-ownership-ok: launcher-owned managed subprocess wrapper with timeout.
            command,
            cwd=str(cwd or ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as error:
        recordException("start.py.managedSubprocessRun", error, extra={"command": command, "cwd": str(cwd or ROOT), "timeout": timeout})
        raise


class Tasks:
    """Launcher task utilities shared by startup cleanup and detector workflows."""

    @staticmethod
    def taskkill(pid: int, *, reason: str = "taskkill", timeout: int = 15, debug: bool = False, protected: set[int] | None = None) -> bool:
        pid = int(pid or 0)
        protected_ids = set(protected or protectedProcessIds())
        if pid <= 0:
            print(f"[TRACE:taskkill] skipped invalid pid={pid} reason={reason}", file=sys.stderr, flush=True)
            return False
        if pid in protected_ids:
            print(f"[WARN:taskkill] skipped protected pid={pid} reason={reason} protected={sorted(protected_ids)}", file=sys.stderr, flush=True)
            return False
        if os.name == "nt":
            command = ["taskkill", "/PID", str(pid), "/T", "/F"]
            completed = managedSubprocessRun(command, timeout=timeout, debug=debug)
            ok = int(completed.returncode or 0) == 0
            level = "TRACE" if ok else "WARN"
            print(f"[{level}:taskkill] pid={pid} reason={reason} exit={completed.returncode} ok={ok}", file=sys.stderr, flush=True)
            output = ((completed.stdout or "") + ("\n" if completed.stdout and completed.stderr else "") + (completed.stderr or "")).strip()
            if output:
                for line in output.splitlines():
                    print(f"[{level}:taskkill] {line}", file=sys.stderr, flush=True)
            return ok
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[TRACE:taskkill] pid={pid} reason={reason} signal=SIGTERM ok=True", file=sys.stderr, flush=True)
            return True
        except ProcessLookupError:  # swallow-ok: pid is already gone, so the desired taskkill outcome is satisfied.
            print(f"[TRACE:taskkill] pid={pid} reason={reason} already-exited ok=True", file=sys.stderr, flush=True)
            return True
        except Exception as error:
            recordException("start.py.Tasks.taskkill", error, extra={"pid": pid, "reason": reason})
            print(f"[WARN:taskkill] pid={pid} reason={reason} failed={type(error).__name__}: {error}", file=sys.stderr, flush=True)
            return False


def missingRuntimeImports() -> list[str]:
    required = {
        "PySide6": "PySide6",
        "sqlalchemy": "SQLAlchemy",
    }
    missing: list[str] = []
    for importName, displayName in required.items():
        if importlib.util.find_spec(importName) is None:
            missing.append(displayName)
    return missing


def ensureRuntimeDependencies(debug: bool = False, autoInstall: bool = True) -> None:
    """Install PySide6/SQLAlchemy from requirements.txt before Qt imports when needed."""
    missing = missingRuntimeImports()
    if not missing:
        return
    requirements = ROOT / "requirements.txt"
    if not autoInstall:
        raise RuntimeError("Missing runtime imports: " + ", ".join(missing) + f". Run: {sys.executable} -m pip install -r {requirements}")
    command = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    if sys.platform.startswith("linux"):
        command.append("--break-system-packages")
    print(f"[WARN:start] missing runtime imports {missing}; installing with managed subprocess", file=sys.stderr, flush=True)
    completed = managedSubprocessRun(command, timeout=900, debug=debug)
    if debug or completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, file=sys.stderr, flush=True)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, flush=True)
    if int(completed.returncode or 0) != 0:
        raise RuntimeError(f"dependency install failed with exit code {completed.returncode}: {commandText(command)}")
    stillMissing = missingRuntimeImports()
    if stillMissing:
        raise RuntimeError("Dependencies installed but imports are still missing: " + ", ".join(stillMissing))


def tryLoadFlatlineDebugger(debug: bool = False) -> object | None:
    """Best-effort FlatLine drop-in hook.

    The latest FlatLine detector/debugger bundle is vendored under vendor/claude.
    A full interactive debugger package may also be dropped into ./flatline; if it
    exposes a known factory, this launcher will instantiate it without forcing the
    application layer to import debugger code.
    """
    flatlineRoot = ROOT / "flatline"
    if flatlineRoot.exists() and str(flatlineRoot) not in sys.path:
        sys.path.insert(0, str(flatlineRoot))
    for moduleName in ("flatline_debugger", "debugger", "start"):
        spec = importlib.util.find_spec(moduleName)
        if spec is None:
            if debug:
                print(f"[INFO:start] Flatline module not installed: {moduleName}", file=sys.stderr)
            continue
        try:
            module = importlib.import_module(moduleName)
        except Exception as error:
            recordException("start.py.flatline-import", error, extra={"module": moduleName})
            if debug:
                print(f"[WARN:start] Flatline import failed {moduleName}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        for factoryName in ("createDebugger", "createFlatlineDebugger", "FlatlineDebugger"):
            factory = getattr(module, factoryName, None)
            if factory is None:
                continue
            try:
                debugger = factory() if callable(factory) else factory
                if debug:
                    print(f"[INFO:start] Flatline debugger loaded: {moduleName}.{factoryName}", file=sys.stderr)
                return debugger
            except Exception as error:
                recordException("start.py.flatline-factory", error, extra={"factory": factoryName})
                print(f"[WARN:start] Flatline factory failed {moduleName}.{factoryName}: {type(error).__name__}: {error}", file=sys.stderr)
    if debug:
        print(f"[INFO:start] Flatline drop-in hook checked. Detector bundle: {VENDOR_CLAUDE}", file=sys.stderr)
    return None


def findStaleSuperGrokProcesses(*, debug: bool = False, bridgeOnly: bool = False, includeBridgeServices: bool = True) -> list[dict[str, Any]]:
    """Find stale Windows Python children by command line, never by process name alone.

    Broad matching against start.py was too dangerous: the PowerShell helper used
    for process discovery also contains the same path text, and a normal
    ``start.py --debug`` relaunch can be the parent of the resident bridge.
    Killing that parent with /T also kills the bridge that --chat is waiting on.
    So cleanup is now role-aware: bridge replacement only targets Python
    processes whose command line contains --serve-bridge.
    """
    if os.name != "nt":
        return []
    root_text = str(ROOT.resolve())
    needles = [str((ROOT / item).resolve()).replace("\\", "/") for item in KNOWN_STALE_ENTRYPOINTS]
    script = r'''
$ErrorActionPreference = 'SilentlyContinue'
$CurrentPid = __PID__
$Needles = @(__NEEDLES__)
$BridgeOnly = __BRIDGE_ONLY__
$IncludeBridgeServices = __INCLUDE_BRIDGE__
$matches = @()
Get-CimInstance Win32_Process | ForEach-Object {
  $cmd = [string]$_.CommandLine
  if ([string]::IsNullOrWhiteSpace($cmd)) { return }
  if ([int]$_.ProcessId -eq [int]$CurrentPid) { return }
  $name = ([string]$_.Name).ToLowerInvariant()
  if ($name -notin @('python.exe','pythonw.exe','py.exe','python','pythonw','py')) { return }
  $normalized = $cmd -replace '\\','/'
  $hit = $false
  foreach ($needle in $Needles) {
    if ($normalized.IndexOf([string]$needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $hit = $true; break }
  }
  if (-not $hit) { return }
  $isBridge = $normalized.IndexOf('--serve-bridge', [StringComparison]::OrdinalIgnoreCase) -ge 0 -or $normalized.IndexOf('--bridge-service', [StringComparison]::OrdinalIgnoreCase) -ge 0
  if ($BridgeOnly -and -not $isBridge) { return }
  if ((-not $IncludeBridgeServices) -and $isBridge) { return }
  if ($normalized.IndexOf('Get-CimInstance Win32_Process', [StringComparison]::OrdinalIgnoreCase) -ge 0) { return }
  $matches += [pscustomobject]@{ ProcessId = [int]$_.ProcessId; Name = [string]$_.Name; CommandLine = $cmd; IsBridge = [bool]$isBridge }
}
$matches | ConvertTo-Json -Compress -Depth 3
'''
    needles_literal = ",".join(json.dumps(n) for n in needles)
    script = (script
        .replace("__PID__", str(os.getpid()))
        .replace("__NEEDLES__", needles_literal)
        .replace("__BRIDGE_ONLY__", "$true" if bridgeOnly else "$false")
        .replace("__INCLUDE_BRIDGE__", "$true" if includeBridgeServices else "$false"))
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        completed = managedSubprocessRun(command, timeout=20, debug=debug)
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except Exception as error:
        recordException("start.py.findStaleSuperGrokProcesses", error, extra={"root": root_text})
    return []


def killStaleSuperGrokProcesses(*, debug: bool = False, bridgeOnly: bool = False, includeBridgeServices: bool = True, reason: str = "stale SuperGrok relaunch cleanup") -> int:
    rows = findStaleSuperGrokProcesses(debug=debug, bridgeOnly=bridgeOnly, includeBridgeServices=includeBridgeServices)
    protected = protectedProcessIds()
    killed = 0
    skipped = 0
    for row in rows:
        pid = int(row.get("ProcessId") or 0)
        if pid <= 0:
            skipped += 1
            continue
        if pid in protected:
            skipped += 1
            print(f"[WARN:stale-process] skip protected pid={pid} name={row.get('Name')} command={row.get('CommandLine')}", file=sys.stderr, flush=True)
            continue
        print(f"[TRACE:stale-process] found pid={pid} bridge={row.get('IsBridge')} name={row.get('Name')} command={row.get('CommandLine')}", file=sys.stderr, flush=True)
        if Tasks.taskkill(pid, reason=reason, debug=debug, protected=protected):
            killed += 1
    if rows or debug:
        print(f"[TRACE:stale-process] summary found={len(rows)} killed={killed} skipped={skipped} bridgeOnly={bridgeOnly} includeBridgeServices={includeBridgeServices} protected={sorted(protected)}", file=sys.stderr, flush=True)
    return killed


DETECTOR_HELP = """
Detector CLI:
  python start.py --health
      Run the full vendored Claude/FlatLine detector suite with coverage details.

  python start.py --certify
      Run all /vendor/claude detector routes, print to console, write reports, and exit.

  python start.py --detector-selftest
      Run a temporary dirty-canary route test so detector wiring failures are visible.

  python start.py --claude-detectors raw-sql swallowed file-io
      Run selected detectors only. Valid detector keys:
      monkey, lifecycle-bypass, raw-sql, recursion, swallowed, redundant,
      file-io, process-faults, phase-ownership, phase-hooks, nonconform,
      comport, bad-code, unlocalized

  Individual shortcuts:
      --monkey / --monkeypatch / --monkey-patch
      --lifecycle-bypass
      --raw-sql
      --recursion
      --swallowed / --swallowed-exceptions
      --redundant
      --file-io
      --process-faults
      --phase-ownership
      --phase-hooks
      --nonconform
      --comport
      --threads       (alias for nonconform)
      --bad-code
      --unlocalized
      --manual        (print vendor/claude/DETECTORS_MANUAL.md)

Reports:
  Combined latest: reports/claude_detectors_report_latest.txt
  Per-detector:    logs/monkeypatches.txt, logs/lifecyclebypass.txt, logs/rawsql.txt,
                   logs/recursion.txt, logs/swallowed.txt, logs/redundant.txt,
                   logs/fileio.txt, logs/process_faults.txt, logs/phase_ownership.txt,
                   logs/phase_hooks.txt, logs/nonconform.txt, logs/comport.txt,
                   logs/badcode.txt, logs/unlocalized.txt

Runtime fault evidence:
  Exceptions are persisted to data/supergrok_bridge_exceptions.sqlite3 when SQLAlchemy is available.
"""


def showDebuggerMenu() -> None:
    reportPath = REPORTS / "claude_detectors_report_latest.txt"
    print(f"""[INFO:start] {APP_NAME} start.py debugger menu

Status:
  start.py launcher: active
  FlatLine detector bundle: {VENDOR_CLAUDE}
  Vendored zip: {VENDOR_CLAUDE / 'claude_latest_flatline_debugger_20260502.zip'}
  Uploaded FlatLine reference: {VENDOR_CLAUDE / 'flatline_start_reference_20260505.py'}
  Process DB: {DATA / 'supergrok_bridge_processes.sqlite3'}
  Exception DB: {DATA / 'supergrok_bridge_exceptions.sqlite3'}
  Debugger heartbeat DB: {DATA / 'supergrok_bridge_debugger.sqlite3'}
  Advertised child surfaces: {debuggerSurfaceLine()}
  Traffic log: {DATA / 'traffic.log'}
  Latest Claude detector report: {reportPath}
  Detector manual: {VENDOR_CLAUDE / 'DETECTORS_MANUAL.md'}
  Whitepaper: {VENDOR_CLAUDE / 'triodesktop_threadzero_process_whitepaper_friendly_letter.txt'}
  Whitepaper: {VENDOR_CLAUDE / 'locked_file_process_cleanup_whitepaper.txt'}

Useful commands:
  python start.py --debug
  python start.py --debug --process-ttl 30
  python start.py --remote-debug-port 9222
  python start.py --health
  python start.py --certify
  python start.py --detector-selftest
  python start.py --phase-hooks
  python start.py --nonconform
  python start.py --comport
  python start.py --claude-detectors raw-sql swallowed file-io
  python start.py --serve-bridge --offscreen --debug
  python start.py --chat grok "hello" --offscreen
  python start.py --bridge-status
  python start.py --offscreen --debug

Detector notes:
{textwrap.indent(DETECTOR_HELP.strip(), '  ')}

Runtime notes:
  ToolCall subprocesses are persisted in the processes table.
  The Qt main-event-loop watchdog polls active QProcess objects and kills expired Windows process trees with taskkill /T /F.
  Windows relaunch uses command-line-targeted stale-process cleanup unless --no-stale-process-kill is passed.
""")



def normalizeOffscreenMode(args: argparse.Namespace) -> str:
    """Resolve the requested bridge window/offscreen strategy.

    The latest FlatLine reference supports Linux managed X11 capture engines
    (xvfb, xdummy, xpra).  On Windows the closest reliable equivalent is a real
    native QtWebEngine surface that is moved away/minimized/hidden; Qt's pure
    offscreen QPA plugin is kept as an explicit opt-in because Chromium
    WebEngine login flows can fail when no native window surface exists.
    """
    raw = str(getattr(args, "offscreen_mode", "") or "").strip().lower().replace("_", "-")
    if getattr(args, "xdummy", False):
        raw = OFFSCREEN_MODE_XDUMMY
    elif getattr(args, "xpra", False):
        raw = OFFSCREEN_MODE_XPRA
    elif getattr(args, "xvfb", False):
        raw = OFFSCREEN_MODE_XVFB
    elif getattr(args, "qt_offscreen", False):
        raw = OFFSCREEN_MODE_QT
    if not raw:
        raw = OFFSCREEN_MODE_AUTO
    aliases = {
        "native": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "offscreen-native": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "window": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "offscreenwindow": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "screen-edge": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "far-window": OFFSCREEN_MODE_OFFSCREEN_WINDOW,
        "hide": OFFSCREEN_MODE_HIDDEN,
        "headless": OFFSCREEN_MODE_QT,
        "qpa": OFFSCREEN_MODE_QT,
        "qt-offscreen": OFFSCREEN_MODE_QT,
        "dummy": OFFSCREEN_MODE_XDUMMY,
        "x11dummy": OFFSCREEN_MODE_XDUMMY,
        "xpra-wrapper": OFFSCREEN_MODE_XPRA,
    }
    raw = aliases.get(raw, raw)
    if raw == OFFSCREEN_MODE_AUTO:
        if os.name == "nt":
            return OFFSCREEN_MODE_OFFSCREEN_WINDOW
        if sys.platform.startswith("linux"):
            return OFFSCREEN_MODE_XVFB
        return OFFSCREEN_MODE_HIDDEN
    allowed = {OFFSCREEN_MODE_HIDDEN, OFFSCREEN_MODE_OFFSCREEN_WINDOW, OFFSCREEN_MODE_MINIMIZED, OFFSCREEN_MODE_QT, OFFSCREEN_MODE_XVFB, OFFSCREEN_MODE_XDUMMY, OFFSCREEN_MODE_XPRA}
    if raw not in allowed:
        print(f"[WARN:offscreen] unknown --offscreen-mode={raw!r}; using auto", file=sys.stderr, flush=True)
        return normalizeOffscreenMode(argparse.Namespace(**{**vars(args), "offscreen_mode": OFFSCREEN_MODE_AUTO, "xdummy": False, "xpra": False, "xvfb": False, "qt_offscreen": False}))
    return raw


def bridgeWindowModeForArgs(args: argparse.Namespace) -> str:
    if getattr(args, "show_bridge", False):
        return "visible"
    if not getattr(args, "serve_bridge", False):
        return "visible"
    if not getattr(args, "offscreen", False):
        return "visible"
    mode = normalizeOffscreenMode(args)
    if mode in {OFFSCREEN_MODE_XVFB, OFFSCREEN_MODE_XDUMMY, OFFSCREEN_MODE_XPRA, OFFSCREEN_MODE_QT}:
        return OFFSCREEN_MODE_HIDDEN
    return mode

def configureQtEnvironment(args: argparse.Namespace) -> None:
    if args.remote_debug_port:
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = str(args.remote_debug_port)
        print(f"[INFO:start] Chromium remote debugging enabled: http://127.0.0.1:{args.remote_debug_port}", file=sys.stderr)
    if args.offscreen:
        mode = normalizeOffscreenMode(args)
        os.environ["SUPERGROK_OFFSCREEN_MODE"] = mode
        if mode == OFFSCREEN_MODE_QT:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            print("[WARN:offscreen] using Qt QPA offscreen mode. This is useful for smoke tests, but QtWebEngine/Grok login can be less reliable than native hidden-window mode.", file=sys.stderr, flush=True)
        elif os.name == "nt":
            previous = str(os.environ.get("QT_QPA_PLATFORM") or "").strip().lower()
            if previous == "offscreen":
                os.environ["QT_QPA_PLATFORM"] = "windows"
                print("[WARN:offscreen] overriding QT_QPA_PLATFORM=offscreen -> windows so QtWebEngine gets a real Chromium/native window surface.", file=sys.stderr, flush=True)
            print(f"[INFO:offscreen] Windows bridge mode={mode}; using a real QtWebEngine window surface hidden from the user, not the fragile Qt offscreen platform.", file=sys.stderr, flush=True)
        elif mode in LINUX_CAPTURE_OFFSCREEN_MODES:
            # The full FlatLine reference implements managed Xvfb/Xdummy/Xpra displays.
            # SuperGrok keeps those flags compatible and documents the selected mode;
            # if DISPLAY is already owned by FlatLine, this child will use it.
            os.environ.setdefault("SUPERGROK_LINUX_CAPTURE_ENGINE", mode)
            print(f"[INFO:offscreen] Linux capture mode requested={mode}; use the full FlatLine parent for managed display startup, or run under an existing DISPLAY.", file=sys.stderr, flush=True)
        else:
            print(f"[INFO:offscreen] bridge mode={mode}; Qt platform remains native/default.", file=sys.stderr, flush=True)
    if args.profile_dir:
        profilePath = Path(args.profile_dir).expanduser().resolve()
        profilePath.mkdir(parents=True, exist_ok=True)
        os.environ["SUPERGROK_PROFILE_DIR"] = str(profilePath)

def buildParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=DETECTOR_HELP,
    )
    parser.add_argument("--health", action="store_true", help="Run all vendored Claude/FlatLine detectors with coverage details and exit.")
    parser.add_argument("--certify", action="store_true", help="Run the full /vendor/claude detector suite, print text reports, and exit.")
    parser.add_argument("--detector-selftest", "--detector-self-test", action="store_true", help="Run detector canary route self-test and exit.")
    parser.add_argument("--claude-detectors", "--detectors", nargs="*", default=None, help="Run selected /vendor/claude detectors and exit. Use no values or all for the full suite.")
    parser.add_argument("--detector-timeout", type=int, default=300, help="Seconds before an individual detector route is killed.")
    parser.add_argument("--root", default="", help="Optional detector scan root. Defaults to this repo root.")
    parser.add_argument("--monkey", action="store_true", help="Run the monkey-patch detector and exit.")
    parser.add_argument("--monkeypatch", "--monkey-patch", action="store_true", help="Run only the monkey patch detector and exit.")
    parser.add_argument("--monkey-report", default="", help="Optional combined detector report path.")
    parser.add_argument("--lifecycle-bypass", action="store_true", help="Run only the lifecycle bypass detector and exit.")
    parser.add_argument("--raw-sql", action="store_true", help="Run only the raw SQL detector and exit.")
    parser.add_argument("--recursion", action="store_true", help="Run only the recursion detector and exit.")
    parser.add_argument("--redundant", action="store_true", help="Run only the redundant code detector and exit.")
    parser.add_argument("--file-io", action="store_true", help="Run only the file I/O detector and exit.")
    parser.add_argument("--process-faults", action="store_true", help="Run only the process fault callback detector and exit.")
    parser.add_argument("--phase-ownership", action="store_true", help="Run only the lifecycle phase ownership detector and exit.")
    parser.add_argument("--phase-hooks", action="store_true", help="Run only the phase hooks/main discipline detector and exit.")
    parser.add_argument("--nonconform", action="store_true", help="Run the nonconformance detector and exit.")
    parser.add_argument("--comport", action="store_true", help="Run the architecture-comport detector route and exit.")
    parser.add_argument("--threads", action="store_true", help="Alias for --nonconform; checks banned Thread/threading constructs.")
    parser.add_argument("--bad-code", action="store_true", help="Run only the bad-code detector and exit.")
    parser.add_argument("--unlocalized", action="store_true", help="Run only the unlocalized UI string detector and exit.")
    parser.add_argument("--swallowed", "--swallowed-exceptions", action="store_true", help="Run only the swallowed exceptions detector and exit.")
    parser.add_argument("--manual", "--man", action="store_true", help="Print usage/manual with detector commands and exit.")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL to load in the browser pane. Defaults to Grok, or ChatGPT when --chatgpt/--chatgtp is used.")
    parser.add_argument("--target", choices=["grok", "chatgpt", "gemini", "claude"], default="", help="Browser target for --serve-bridge. Usually inferred from --chat/--chatgpt/--gemini/--claude or --url.")
    parser.add_argument("--chat", nargs="*", default=None, help="Send a command-line chat request. Examples: --chat \"hello\", --chat --debug \"hello\", --chat grok \"hello\", --chat chatgpt \"hello\", or --chat grok deployment \"hello\".")
    parser.add_argument("--chatgpt", "--chatgtp", "--gpt", "--gtp", dest="chatgpt", nargs="*", default=None, help="Send a command-line chat request through ChatGPT at chatgpt.com. With no message, opens the visible ChatGPT bridge so you can log in and persist cookies. Aliases keep --chatgtp, --gpt, and --gtp working.")
    parser.add_argument("--gemini", "--gem", "--bard", dest="gemini", nargs="*", default=None, help="Send a command-line chat request through Gemini (gemini.google.com). With no message, opens the visible Gemini bridge so you can log in.")
    parser.add_argument("--claude", "--anthropic", dest="claude", nargs="*", default=None, help="Send a command-line chat request through Claude (claude.ai). With no message, opens the visible Claude bridge so you can log in.")
    parser.add_argument("--attach", action="append", default=[], help="Attach an input file to a --chat request. May be repeated. Text files inlined; binary/image/PDF sent as base64. Replaces the old --file-as-attachment usage.")
    parser.add_argument("--file", nargs="?", const="<dialog>", default=None, help="Save the chat response to a file. With a path (--file out.txt), writes directly. Bare --file pops a Qt Save-As dialog. Use --attach for input files.")
    parser.add_argument("--serve-bridge", "--bridge-service", action="store_true", help="Run the resident local Grok bridge command service so Grok stays warm/logged in.")
    parser.add_argument("--bridge-status", action="store_true", help="Query the resident Grok bridge service and exit.")
    parser.add_argument("--bridge-port", type=int, default=BRIDGE_SERVICE_PORT, help="Local bridge command service TCP port.")
    parser.add_argument("--chat-timeout", type=int, default=240, help="Seconds to wait for a Grok bridge chat response.")
    parser.add_argument("--no-chat-service-start", action="store_true", help="Do not auto-start --serve-bridge when --chat cannot reach the resident service.")
    parser.add_argument("--force-bridge-restart", action="store_true", help="For --chat, restart the resident bridge before sending so the service reloads current Python files.")
    parser.add_argument("--no-bridge-source-check", action="store_true", help="For --chat, allow using a resident bridge even when its loaded source signature is stale or missing.")
    parser.add_argument("--show-bridge", action="store_true", help="Show the bridge window even in service mode. Useful when logging in again.")
    parser.add_argument("--profile-dir", default="", help="Persistent Qt WebEngine profile directory.")
    parser.add_argument("--remote-debug-port", type=int, default=9222, help="Chromium DevTools remote debugging port. Use 0 to disable.")
    parser.add_argument("--offscreen", "--off-screen", action="store_true", help="Run the resident bridge invisibly. On Windows this defaults to a real native window moved off-screen, not QT_QPA_PLATFORM=offscreen.")
    parser.add_argument("--offscreen-mode", choices=["auto", "hidden", "offscreen-window", "minimized", "qt", "xvfb", "xdummy", "xpra"], default="auto", help="Bridge offscreen strategy. Windows default auto=offscreen-window; qt forces QT_QPA_PLATFORM=offscreen; Linux names mirror FlatLine capture engines.")
    parser.add_argument("--qt-offscreen", action="store_true", help="Force QT_QPA_PLATFORM=offscreen. Mostly for smoke tests; not recommended for Grok login reliability.")
    parser.add_argument("--xvfb", action="store_true", help="Request FlatLine-style Xvfb capture mode when running under Linux/FlatLine.")
    parser.add_argument("--xdummy", "--dummy", action="store_true", help="Request FlatLine-style Xdummy capture mode when running under Linux/FlatLine.")
    parser.add_argument("--xpra", action="store_true", help="Request FlatLine-style Xpra capture mode when running under Linux/FlatLine.")
    parser.add_argument("--process-ttl", type=int, default=30, help="ToolCall subprocess TTL in seconds before watchdog timeout/taskkill.")
    parser.add_argument("--no-deps", action="store_true", help="Do not auto-install missing PySide6/SQLAlchemy dependencies before app launch.")
    parser.add_argument("--no-stale-process-kill", action="store_true", help="Do not taskkill stale SuperGrok children before launching on Windows.")
    parser.add_argument("--stale-process-cleanup", action="store_true", help="Opt in to broad non-bridge stale cleanup before a visible/debug app launch. Bridge replacement remains automatic and role-scoped.")
    parser.add_argument("--debugger-query-surfaces", action="store_true", help="Print FlatLine-compatible child surfaces and exit.")
    parser.add_argument("--debugger-vardump", action="store_true", help="Print a small JSON launcher vardump and exit.")
    parser.add_argument("--debugger-menu", action="store_true", help="Print start.py debugger menu/status and exit.")
    parser.add_argument("--debug", action="store_true", help="Print launcher/app debug traces.")
    parser.add_argument("--ver", "--version", action="store_true", help=f"Print version ({APP_NAME} {APP_VERSION}) and exit.")
    parser.add_argument("--login", action="store_true", help="Open a stripped-down login-only window for the chosen --target (or --grok/--chatgpt/--gemini/--claude).")
    parser.add_argument("--probe-auth", action="store_true", help="Headless: load the chosen --target home URL, report logged-in/out state and minimum no-scroll login window size as JSON, then exit.")
    return parser


def chatFlagPresent(argv: list[str] | None) -> bool:
    return any(str(token or "").strip().lower() in CHAT_CLI_FLAG_ALIASES for token in list(argv or []))


def chatGptFlagPresent(argv: list[str] | None) -> bool:
    return any(str(token or "").strip().lower() in CHATGPT_CLI_FLAG_ALIASES for token in list(argv or []))


def geminiFlagPresent(argv: list[str] | None) -> bool:
    return any(str(token or "").strip().lower() in GEMINI_CLI_FLAG_ALIASES for token in list(argv or []))


def claudeFlagPresent(argv: list[str] | None) -> bool:
    return any(str(token or "").strip().lower() in CLAUDE_CLI_FLAG_ALIASES for token in list(argv or []))


def activeChatArgName(args: argparse.Namespace, argv: list[str] | None = None) -> str:
    # If --chat is present, unknown free text after another option belongs to
    # --chat, even when a provider alias like --gpt/--gtp is also present.
    # normalizeChatModeArgs then forces that --chat request to the right target.
    if getattr(args, "chat", None) is not None:
        return "chat"
    if getattr(args, "chatgpt", None) is not None:
        return "chatgpt"
    if getattr(args, "gemini", None) is not None:
        return "gemini"
    if getattr(args, "claude", None) is not None:
        return "claude"
    return "chat"


def applyChatUnknownTail(args: argparse.Namespace, unknown: list[str], argv: list[str] | None) -> None:
    """Allow provider-style CLI ordering such as: --chat --debug "Hello".

    argparse cannot normally attach a positional message after another option
    when --chat/--chatgpt uses nargs. If a chat flag was present, unknown bare
    tokens are treated as the chat tail instead of causing a usage failure.
    """
    if not chatFlagPresent(argv):
        return
    attr = activeChatArgName(args, argv)
    current = list(getattr(args, attr, None) or [])
    tail = [str(item) for item in list(unknown or []) if str(item or "").strip()]
    if tail:
        current.extend(tail)
    setattr(args, attr, current)
    if tail:
        unknown.clear()
    if bool(getattr(args, "debug", False)):
        try:
            print(f"[TRACE:bridge-client] normalized chat argv attr={attr} chat={current!r} unknown={tail!r}", file=sys.stderr, flush=True)
        except Exception:
            pass


def chatGptBridgeLoginRequested(args: argparse.Namespace) -> bool:
    """True when --chatgpt/--chatgtp/--gpt/--gtp was used without a message.

    That mode is intentionally a visible browser-login bridge, not a CLI chat.
    ChatGPT web auth is cookie/session based, so first use must let the user log
    in through the persistent QtWebEngine profile. If the user also supplied
    --chat, the alias is a target selector and the chat message should be sent.
    """
    if getattr(args, "chat", None) is not None:
        return False
    values = getattr(args, "chatgpt", None)
    if values is None:
        return False
    return not any(str(item or "").strip() for item in list(values or []))


def configureChatGptLoginBridgeArgs(args: argparse.Namespace) -> None:
    args.chat = None
    args.chatgpt = []
    args.chat_target = "chatgpt"
    args.target = "chatgpt"
    args.url = DEFAULT_CHATGPT_URL
    args.serve_bridge = True
    args.show_bridge = True
    args.offscreen = False


def forceChatGptChatParts(parts: list[str] | None) -> list[str]:
    values = [str(item) for item in list(parts or []) if str(item or "").strip()]
    if not values:
        return ["chatgpt"]
    first = values[0].strip().lower().replace("_", "-")
    if first in ALL_CHAT_TARGET_ALIASES:
        return ["chatgpt", *values[1:]]
    return ["chatgpt", *values]


def geminiBridgeLoginRequested(args: argparse.Namespace) -> bool:
    """Like chatGptBridgeLoginRequested but for --gemini with no message."""
    if getattr(args, "chat", None) is not None:
        return False
    values = getattr(args, "gemini", None)
    if values is None:
        return False
    return not any(str(item or "").strip() for item in list(values or []))


def configureGeminiLoginBridgeArgs(args: argparse.Namespace) -> None:
    args.chat = None
    args.gemini = []
    args.chat_target = "gemini"
    args.target = "gemini"
    args.url = DEFAULT_GEMINI_URL
    args.serve_bridge = True
    args.show_bridge = True
    args.offscreen = False


def forceGeminiChatParts(parts: list[str] | None) -> list[str]:
    values = [str(item) for item in list(parts or []) if str(item or "").strip()]
    if not values:
        return ["gemini"]
    first = values[0].strip().lower().replace("_", "-")
    if first in ALL_CHAT_TARGET_ALIASES:
        return ["gemini", *values[1:]]
    return ["gemini", *values]


def claudeBridgeLoginRequested(args: argparse.Namespace) -> bool:
    """Like chatGptBridgeLoginRequested but for --claude with no message."""
    if getattr(args, "chat", None) is not None:
        return False
    values = getattr(args, "claude", None)
    if values is None:
        return False
    return not any(str(item or "").strip() for item in list(values or []))


def configureClaudeLoginBridgeArgs(args: argparse.Namespace) -> None:
    args.chat = None
    args.claude = []
    args.chat_target = "claude"
    args.target = "claude"
    args.url = DEFAULT_CLAUDE_URL
    args.serve_bridge = True
    args.show_bridge = True
    args.offscreen = False


def forceClaudeChatParts(parts: list[str] | None) -> list[str]:
    values = [str(item) for item in list(parts or []) if str(item or "").strip()]
    if not values:
        return ["claude"]
    first = values[0].strip().lower().replace("_", "-")
    if first in ALL_CHAT_TARGET_ALIASES:
        return ["claude", *values[1:]]
    return ["claude", *values]


def _urlEffectivelyDefault(args: argparse.Namespace) -> bool:
    """A url is 'effectively unset' if it's empty or still the bare grok default."""
    value = str(getattr(args, "url", "") or "").strip()
    return not value or value == DEFAULT_GROK_URL


def _inferTargetFromUrl(args: argparse.Namespace) -> str:
    url = getattr(args, "url", "")
    if urlLooksLikeTarget(url, "chatgpt"):
        return "chatgpt"
    if urlLooksLikeTarget(url, "gemini"):
        return "gemini"
    if urlLooksLikeTarget(url, "claude"):
        return "claude"
    return "grok"


def normalizeTargetUrlArgs(args: argparse.Namespace) -> None:
    target = normalizeChatTarget(
        getattr(args, "target", "")
        or getattr(args, "chat_target", "")
        or _inferTargetFromUrl(args)
    )
    if getattr(args, "target", ""):
        args.target = target
    if target == "chatgpt" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_CHATGPT_URL
    elif target == "gemini" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_GEMINI_URL
    elif target == "claude" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_CLAUDE_URL
    elif target == "grok" and not str(getattr(args, "url", "") or "").strip():
        args.url = DEFAULT_GROK_URL


def normalizeChatModeArgs(args: argparse.Namespace) -> None:
    chatgpt_alias_requested = bool(getattr(args, "chatgpt_alias_requested", False) or getattr(args, "chatgpt", None) is not None)
    gemini_alias_requested = bool(getattr(args, "gemini_alias_requested", False) or getattr(args, "gemini", None) is not None)
    claude_alias_requested = bool(getattr(args, "claude_alias_requested", False) or getattr(args, "claude", None) is not None)
    if getattr(args, "chat", None) is not None and chatgpt_alias_requested:
        args.chat = forceChatGptChatParts(getattr(args, "chat", []) or [])
    elif getattr(args, "chat", None) is not None and gemini_alias_requested:
        args.chat = forceGeminiChatParts(getattr(args, "chat", []) or [])
    elif getattr(args, "chat", None) is not None and claude_alias_requested:
        args.chat = forceClaudeChatParts(getattr(args, "chat", []) or [])
    elif getattr(args, "chatgpt", None) is not None:
        args.chat = forceChatGptChatParts(getattr(args, "chatgpt", []) or [])
    elif getattr(args, "gemini", None) is not None:
        args.chat = forceGeminiChatParts(getattr(args, "gemini", []) or [])
    elif getattr(args, "claude", None) is not None:
        args.chat = forceClaudeChatParts(getattr(args, "claude", []) or [])
    if getattr(args, "chat", None) is None:
        return
    parsed = parseChatArgs(getattr(args, "chat", []) or [])
    target = normalizeChatTarget(parsed.get("target"))
    setattr(args, "chat_target", target)
    if target == "chatgpt" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_CHATGPT_URL
    elif target == "gemini" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_GEMINI_URL
    elif target == "claude" and _urlEffectivelyDefault(args):
        args.url = DEFAULT_CLAUDE_URL
    elif target == "grok" and not str(getattr(args, "url", "") or "").strip():
        args.url = DEFAULT_GROK_URL


def detectorNamesFromArgs(args: argparse.Namespace) -> list[str] | None:
    names: list[str] = []
    if args.certify or args.health:
        names.append("all")
    if args.monkey or args.monkeypatch:
        names.append("monkey")
    if args.lifecycle_bypass:
        names.append("lifecycle-bypass")
    if args.raw_sql:
        names.append("raw-sql")
    if args.recursion:
        names.append("recursion")
    if args.redundant:
        names.append("redundant")
    if args.file_io:
        names.append("file-io")
    if args.process_faults:
        names.append("process-faults")
    if args.phase_ownership:
        names.append("phase-ownership")
    if args.phase_hooks:
        names.append("phase-hooks")
    if args.nonconform or args.threads:
        names.append("nonconform")
    if args.comport:
        names.append("comport")
    if args.bad_code:
        names.append("bad-code")
    if args.unlocalized:
        names.append("unlocalized")
    if args.swallowed:
        names.append("swallowed")
    if args.claude_detectors is not None:
        names.extend(args.claude_detectors or ["all"])
    return names or None


def runClaudeDetectors(args: argparse.Namespace, names: list[str] | None = None) -> int:
    from vendor.claude.run_claude_reports import print_manual, run_and_return_code, run_selftest

    if args.detector_selftest:
        return int(run_selftest(timeout=max(3, min(int(args.detector_timeout or 8), 8))))
    if args.manual:
        return int(print_manual())
    reportPath = Path(args.monkey_report).expanduser() if args.monkey_report else None
    scanRoot = Path(args.root).expanduser().resolve() if args.root else ROOT
    code = run_and_return_code(
        reportPath,
        names,
        root=scanRoot,
        echo=True,
        timeout=max(15, int(args.detector_timeout or 300)),
        coverage_fail=bool(args.health),
    )
    report = reportPath or (scanRoot / "reports" / "claude_detectors_report_latest.txt")
    print(f"[INFO:claude] report written: {report}")
    return int(code)


def readWhitepaperRecommendations() -> list[str]:
    recs = []
    for filename in ("triodesktop_threadzero_process_whitepaper_friendly_letter.txt", "locked_file_process_cleanup_whitepaper.txt"):
        path = VENDOR_CLAUDE / filename
        if path.exists():
            recs.append(path.name)
    return recs



_CHAT_CLI_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_CHAT_CLI_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".ps1", ".bat", ".cmd", ".php", ".sql", ".yaml", ".yml", ".ini", ".cfg", ".log", ".toml", ".rst"}


def _chatCliResolveFile(pathValue: object) -> Path:
    raw = str(pathValue or "").strip().strip('"')
    if not raw:
        raise RuntimeError("empty --file path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        cwdPath = (Path.cwd() / path).resolve()
        rootPath = (ROOT / path).resolve()
        if cwdPath.exists():
            path = cwdPath
        elif rootPath.exists():
            path = rootPath
    path = path.resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"--file path not found: {raw}")
    return path


def _chatCliMimeForPath(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    if ext == ".pdf":
        return "application/pdf"
    guessed = mimetypes.guess_type(str(path))[0]
    if guessed:
        return guessed
    if ext in _CHAT_CLI_TEXT_EXTENSIONS:
        return "text/plain"
    return "application/octet-stream"


def buildBridgeAttachmentPayloads(files: object) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if not files:
        return payloads
    items = list(files) if isinstance(files, (list, tuple, set)) else [files]
    maxBytes = max(1, int(os.environ.get("SUPERGROK_ATTACHMENT_MAX_BYTES", "2097152") or "2097152"))
    maxTextChars = max(1000, int(os.environ.get("SUPERGROK_ATTACHMENT_TEXT_CHARS", "120000") or "120000"))
    for item in items:
        path = _chatCliResolveFile(item)
        data = path.read_bytes()
        size = len(data)
        mime = _chatCliMimeForPath(path)
        ext = path.suffix.lower()
        isText = mime.startswith("text/") or ext in _CHAT_CLI_TEXT_EXTENSIONS
        row: dict[str, Any] = {
            "path": str(path),
            "name": path.name,
            "mime": mime,
            "size": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "isImage": ext in _CHAT_CLI_IMAGE_EXTENSIONS,
            "isText": isText,
        }
        if isText:
            text = data.decode("utf-8", errors="replace")
            row["text"] = text[:maxTextChars]
            row["textTruncated"] = len(text) > maxTextChars
        elif size <= maxBytes:
            row["base64"] = base64.b64encode(data).decode("ascii")
            row["base64Truncated"] = False
        else:
            row["base64"] = ""
            row["base64Truncated"] = True
            row["note"] = f"File was {size} bytes, above SUPERGROK_ATTACHMENT_MAX_BYTES={maxBytes}; metadata only."
        payloads.append(row)
    return payloads


def parseChatArgs(parts: list[str] | None) -> dict[str, str]:
    values = [str(part) for part in (parts or [])]
    targets = ALL_CHAT_TARGET_ALIASES
    if not values:
        raise ValueError('usage: --chat "message", --chat grok "message", --chat chatgpt "message", or --chatgpt "message"')
    # Mirror normalizeChatTarget's underscore→hyphen step so `gem_bridge` matches `gem-bridge`.
    first = values[0].strip().lower().replace("_", "-")
    if first in targets:
        target = normalizeChatTarget(first)
        if len(values) < 2:
            raise ValueError('usage: --chat grok "message", --chat chatgpt "message", or --chatgpt "message"')
        if len(values) == 2:
            deployment = ""
            message = values[1]
        else:
            deployment = values[1]
            message = " ".join(values[2:])
    else:
        target = "grok"
        deployment = ""
        message = " ".join(values)
    if not message.strip():
        raise ValueError("chat message is blank")
    return {"target": target, "deployment": deployment, "message": message}


def bridgeRequest(payload: dict[str, Any], *, port: int, timeout: int = 30) -> dict[str, Any]:
    request_id = f"bridge-client-{int(time.time() * 1000)}-{os.getpid()}"
    transport = {"host": BRIDGE_SERVICE_HOST, "port": int(port or BRIDGE_SERVICE_PORT), "timeoutSeconds": int(timeout or 30)}
    _debugJson("bridge-client-request", {
        "eventType": "bridge-client-request",
        "requestId": request_id,
        "direction": "client-to-bridge",
        "transport": transport,
        "request": {"headers": {}, "body": payload, "bodyText": _safeJson(payload)},
    })
    _sessionJson({"eventType": "bridge-client-request", "requestId": request_id, "direction": "client-to-bridge", "transport": transport, "request": payload})
    data = (_safeJson(payload) + "\n").encode("utf-8")
    deadline = time.monotonic() + max(1, int(timeout or 30))
    received = b""
    started = time.monotonic()
    try:
        with socket.create_connection((BRIDGE_SERVICE_HOST, int(port or BRIDGE_SERVICE_PORT)), timeout=min(10, max(1, int(timeout or 30)))) as sock:
            sock.settimeout(1.0)
            sock.sendall(data)
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                received += chunk
                if b"\n" in received:
                    line = received.split(b"\n", 1)[0]
                    decoded = json.loads(line.decode("utf-8", "replace"))
                    if isinstance(decoded, dict):
                        _debugJson("bridge-client-response", {
                            "eventType": "bridge-client-response",
                            "requestId": request_id,
                            "direction": "bridge-to-client",
                            "durationMs": int((time.monotonic() - started) * 1000),
                            "response": {"headers": {}, "body": decoded, "bodyText": _safeJson(decoded)},
                        })
                        _sessionJson({"eventType": "bridge-client-response", "requestId": request_id, "direction": "bridge-to-client", "durationMs": int((time.monotonic() - started) * 1000), "response": decoded})
                        return decoded
                    raise ValueError("bridge response was not a JSON object")
        if received.strip():
            decoded = json.loads(received.decode("utf-8", "replace"))
            if isinstance(decoded, dict):
                _debugJson("bridge-client-response", {
                    "eventType": "bridge-client-response",
                    "requestId": request_id,
                    "direction": "bridge-to-client",
                    "durationMs": int((time.monotonic() - started) * 1000),
                    "response": {"headers": {}, "body": decoded, "bodyText": _safeJson(decoded)},
                })
                _sessionJson({"eventType": "bridge-client-response", "requestId": request_id, "direction": "bridge-to-client", "durationMs": int((time.monotonic() - started) * 1000), "response": decoded})
                return decoded
        raise TimeoutError(f"bridge service did not answer within {timeout}s")
    except Exception as error:
        _debugJson("bridge-client-error", {
            "eventType": "bridge-client-error",
            "requestId": request_id,
            "direction": "client-bridge-error",
            "durationMs": int((time.monotonic() - started) * 1000),
            "transport": transport,
            "request": {"headers": {}, "body": payload, "bodyText": _safeJson(payload)},
            "receivedText": received.decode("utf-8", "replace") if received else "",
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _sessionJson({"eventType": "bridge-client-error", "requestId": request_id, "direction": "client-bridge-error", "durationMs": int((time.monotonic() - started) * 1000), "request": payload, "error": f"{type(error).__name__}: {error}"})
        raise


def tailTextFile(path: Path, *, maxLines: int = 20, maxChars: int = 6000) -> str:
    try:
        if not path.exists():
            return ""
        data = path.read_text(encoding="utf-8", errors="replace")
        if len(data) > maxChars:
            data = data[-maxChars:]
        lines = data.splitlines()[-max(1, int(maxLines or 20)):]
        return "\n".join(lines).strip()
    except Exception as error:
        return f"<could not read {path}: {type(error).__name__}: {error}>"


def waitForBridgeService(*, port: int, timeout: int = 60, debug: bool = False, process: subprocess.Popen[Any] | None = None, logPath: Path | None = None) -> bool:
    deadline = time.monotonic() + max(1, int(timeout or 60))
    started = time.monotonic()
    lastTrace = 0.0
    lastTail = ""
    while time.monotonic() < deadline:
        if process is not None:
            code = process.poll()
            if code is not None:
                if debug:
                    print(f"[TRACE:bridge-client] bridge service process exited before binding pid={getattr(process, 'pid', 0)} exit={code}", file=sys.stderr, flush=True)
                    tail = tailTextFile(logPath or (LOGS / "bridge_service.log"), maxLines=30)
                    if tail:
                        print(f"[TRACE:bridge-client] bridge_service.log tail after early exit:\n{tail}", file=sys.stderr, flush=True)
                return False
        try:
            response = bridgeRequest({"action": "status"}, port=port, timeout=3)
            if response.get("ok"):
                if debug:
                    print(f"[TRACE:bridge-client] service ready pid={response.get('pid')} loaded={response.get('loaded')} loadOk={response.get('loadOk')} url={response.get('url')}", file=sys.stderr, flush=True)
                return True
        except Exception as error:
            now = time.monotonic()
            if debug and (now - lastTrace >= 2.0 or lastTrace <= 0.0):
                elapsed = now - started
                pid = int(getattr(process, "pid", 0) or 0) if process is not None else 0
                alive = process is not None and process.poll() is None
                print(f"[TRACE:bridge-client] waiting for service elapsed={elapsed:.1f}s port={port} pid={pid} alive={alive}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                tail = tailTextFile(logPath or (LOGS / "bridge_service.log"), maxLines=8, maxChars=2500)
                if tail and tail != lastTail:
                    print(f"[TRACE:bridge-client] bridge_service.log tail:\n{tail}", file=sys.stderr, flush=True)
                    lastTail = tail
                lastTrace = now
            time.sleep(0.5)
    if debug:
        pid = int(getattr(process, "pid", 0) or 0) if process is not None else 0
        alive = process is not None and process.poll() is None
        print(f"[TRACE:bridge-client] service wait timed out after {timeout}s port={port} pid={pid} alive={alive}", file=sys.stderr, flush=True)
        tail = tailTextFile(logPath or (LOGS / "bridge_service.log"), maxLines=40)
        if tail:
            print(f"[TRACE:bridge-client] final bridge_service.log tail:\n{tail}", file=sys.stderr, flush=True)
    return False


def _windowsHiddenStartupInfo() -> tuple[int, Any | None]:
    """Return Popen flags/startupinfo that keep helper consoles invisible on Windows."""
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
        creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
            startupinfo.wShowWindow = 0
        except Exception as error:
            recordException("start.py.hidden-startupinfo", error)
            startupinfo = None
    return creationflags, startupinfo


def startBridgeServiceProcess(args: argparse.Namespace) -> subprocess.Popen[Any]:
    # A --chat request should behave like the API-provider --chat route once a
    # resident browser session exists. Grok can normally warm up hidden. ChatGPT
    # cannot reliably be "logged in" from a blind CLI, so first-run ChatGPT
    # starts visible unless the user explicitly requests --offscreen after cookies
    # are already saved in the persistent profile.
    chatTarget = normalizeChatTarget(getattr(args, "chat_target", "") or (parseChatArgs(getattr(args, "chat", []) or []).get("target") if getattr(args, "chat", None) is not None else getattr(args, "target", "") or "grok"))
    force_service_offscreen = bool(getattr(args, "chat", None) is not None and chatTarget != "chatgpt" and not getattr(args, "show_bridge", False))
    force_chatgpt_visible = bool(getattr(args, "chat", None) is not None and chatTarget == "chatgpt" and not getattr(args, "offscreen", False))
    command = [
        sys.executable,
        str(ROOT / "start.py"),
        "--serve-bridge",
        "--bridge-port",
        str(int(args.bridge_port or BRIDGE_SERVICE_PORT)),
        "--remote-debug-port",
        str(int(args.remote_debug_port or 0)),
        "--process-ttl",
        str(int(args.process_ttl or 30)),
    ]
    command.extend(["--target", chatTarget])
    if args.url:
        command.extend(["--url", str(args.url)])
    if args.profile_dir:
        command.extend(["--profile-dir", str(args.profile_dir)])
    if args.offscreen or force_service_offscreen:
        command.append("--offscreen")
        mode = normalizeOffscreenMode(args)
        if force_service_offscreen and mode == OFFSCREEN_MODE_AUTO:
            mode = OFFSCREEN_MODE_OFFSCREEN_WINDOW if os.name == "nt" else OFFSCREEN_MODE_XVFB
        if mode and mode != OFFSCREEN_MODE_AUTO:
            command.extend(["--offscreen-mode", mode])
        if args.qt_offscreen:
            command.append("--qt-offscreen")
        if args.xvfb:
            command.append("--xvfb")
        if args.xdummy:
            command.append("--xdummy")
        if args.xpra:
            command.append("--xpra")
    if args.debug:
        command.append("--debug")
    if args.no_deps:
        command.append("--no-deps")
    if args.no_stale_process_kill:
        command.append("--no-stale-process-kill")
    if args.show_bridge or force_chatgpt_visible:
        command.append("--show-bridge")
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / "bridge_service.log"
    handle = log_path.open("w", encoding="utf-8", errors="replace")  # file-io-ok: service bootstrap log reset per run.
    handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] START {' '.join(command)}\n")
    handle.flush()
    if args.debug:
        print(f"[INFO:bridge-client] starting resident bridge service: {commandText(command)}", file=sys.stderr, flush=True)
    process_kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": (os.name != "nt"),
    }
    creationflags, startupinfo = _windowsHiddenStartupInfo()
    if creationflags:
        process_kwargs["creationflags"] = creationflags
    if startupinfo is not None:
        process_kwargs["startupinfo"] = startupinfo
    process = subprocess.Popen(command, **process_kwargs)  # lifecycle-bypass-ok phase-ownership-ok: launcher-owned resident bridge process.
    try:
        setattr(process, "supergrokBridgeLogPath", str(log_path))
    except Exception:
        pass
    handle.close()
    return process


def waitForBridgeServiceStop(*, port: int, timeout: int = 15, debug: bool = False) -> bool:
    deadline = time.monotonic() + max(1, int(timeout or 15))
    while time.monotonic() < deadline:
        try:
            bridgeRequest({"action": "status"}, port=port, timeout=2)
        except Exception:
            return True
        if debug:
            print("[TRACE:bridge-client] waiting for old bridge service to exit", file=sys.stderr, flush=True)
        time.sleep(0.5)
    return False


def stopRunningBridgeService(args: argparse.Namespace, statusPayload: dict[str, Any] | None = None, *, reason: str = "bridge refresh") -> bool:
    port = int(args.bridge_port or BRIDGE_SERVICE_PORT)
    status = statusPayload
    if status is None:
        try:
            status = bridgeRequest({"action": "status"}, port=port, timeout=5)
        except Exception:
            status = None
    if isinstance(status, dict) and status.get("service") == "SuperGrok Bridge":
        print(f"[INFO:bridge-client] stopping resident bridge pid={status.get('pid')} reason={reason}", file=sys.stderr, flush=True)
    else:
        print(f"[INFO:bridge-client] stopping resident bridge on port {port} reason={reason}", file=sys.stderr, flush=True)
    try:
        bridgeRequest({"action": "shutdown", "reason": reason}, port=port, timeout=5)
    except Exception as error:
        if args.debug:
            print(f"[TRACE:bridge-client] shutdown request returned {type(error).__name__}: {error}", file=sys.stderr, flush=True)
    stopped = waitForBridgeServiceStop(port=port, timeout=15, debug=args.debug)
    if stopped:
        return True
    pid = 0
    if isinstance(status, dict) and status.get("service") == "SuperGrok Bridge":
        try:
            pid = int(status.get("pid") or 0)
        except Exception:
            pid = 0
    if pid > 0 and pid != os.getpid():
        print(f"[WARN:bridge-client] bridge did not exit after shutdown; killing pid={pid}", file=sys.stderr, flush=True)
        return Tasks.taskkill(pid, reason=reason, debug=args.debug)
    return False


def ensureFreshBridgeService(args: argparse.Namespace) -> bool:
    port = int(args.bridge_port or BRIDGE_SERVICE_PORT)
    try:
        status = bridgeRequest({"action": "status"}, port=port, timeout=8)
    except Exception:
        return False
    desiredTarget = normalizeChatTarget(getattr(args, "chat_target", "") or (parseChatArgs(getattr(args, "chat", []) or []).get("target") if getattr(args, "chat", None) is not None else getattr(args, "target", "") or "grok"))
    currentTarget = normalizeChatTarget(status.get("target") or ("chatgpt" if urlLooksLikeTarget(status.get("url"), "chatgpt") else "grok"))
    if args.force_bridge_restart:
        stopRunningBridgeService(args, status, reason="--force-bridge-restart")
        process = startBridgeServiceProcess(args)
        return waitForBridgeService(port=port, timeout=90, debug=args.debug, process=process, logPath=LOGS / "bridge_service.log")
    if desiredTarget and currentTarget != desiredTarget:
        if args.no_chat_service_start:
            raise RuntimeError(f"resident bridge target is {currentTarget!r}, but chat target is {desiredTarget!r} and --no-chat-service-start was used")
        print(f"[WARN:bridge-client] resident bridge target is {currentTarget!r}; restarting for {desiredTarget!r}", file=sys.stderr, flush=True)
        stopRunningBridgeService(args, status, reason=f"target changed to {desiredTarget}")
        process = startBridgeServiceProcess(args)
        return waitForBridgeService(port=port, timeout=90, debug=args.debug, process=process, logPath=LOGS / "bridge_service.log")
    matches, reason = sourceSignatureMatches(status)
    if matches or args.no_bridge_source_check:
        if args.debug:
            print(f"[TRACE:bridge-client] resident bridge accepted: {reason} target={currentTarget}", file=sys.stderr, flush=True)
        return True
    if args.no_chat_service_start:
        raise RuntimeError(f"resident bridge service is stale and --no-chat-service-start was used: {reason}")
    print(f"[WARN:bridge-client] {reason}; restarting resident bridge so Python changes load", file=sys.stderr, flush=True)
    stopRunningBridgeService(args, status, reason="source signature changed")
    process = startBridgeServiceProcess(args)
    return waitForBridgeService(port=port, timeout=90, debug=args.debug, process=process, logPath=LOGS / "bridge_service.log")


def replaceBridgeServiceBeforeServing(args: argparse.Namespace) -> None:
    if args.no_stale_process_kill:
        return
    port = int(args.bridge_port or BRIDGE_SERVICE_PORT)
    try:
        status = bridgeRequest({"action": "status"}, port=port, timeout=5)
    except Exception:
        # No listening service. There still may be a half-started old
        # --serve-bridge process, so kill only that role, never a generic
        # start.py --debug parent or PowerShell helper.
        killStaleSuperGrokProcesses(debug=args.debug, bridgeOnly=True, includeBridgeServices=True, reason="new --serve-bridge stale bridge cleanup")
        return
    if isinstance(status, dict) and status.get("service") == "SuperGrok Bridge":
        stopRunningBridgeService(args, status, reason="new --serve-bridge instance replacing old resident bridge")


def chatDebugTrace(args: argparse.Namespace, message: str, **fields: Any) -> None:
    if not bool(getattr(args, "debug", False)):
        return
    try:
        suffix = ""
        if fields:
            suffix = " " + json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True)
        print(f"[TRACE:bridge-client] {message}{suffix}", file=sys.stderr, flush=True)
    except Exception:
        print(f"[TRACE:bridge-client] {message}", file=sys.stderr, flush=True)

def saveOutputIfRequested(args: argparse.Namespace, answer: str) -> None:
    """Implement --file: write the model response to a path on disk.

    `args.file` is `None` when the flag is absent. With a value (`--file out.txt`)
    we write directly; with the sentinel `<dialog>` from bare `--file` we pop a
    Qt Save-As dialog so the user picks the path interactively. Spawning Qt from
    the CLI process is a small cost (~200ms) but keeps the dialog parent-less
    on Windows where there's no other Qt parent in this process.
    """
    requested = getattr(args, "file", None)
    if requested is None:
        return
    path = str(requested).strip()
    if not path or path == "<dialog>":
        path = _promptForSavePath(args)
        if not path:
            print("[INFO:save-file] save-as cancelled.", file=sys.stderr, flush=True)
            return
    try:
        outPath = Path(path).expanduser().resolve()
        outPath.parent.mkdir(parents=True, exist_ok=True)
        outPath.write_text(answer or "", encoding="utf-8")
        print(f"[INFO:save-file] response saved to {outPath}", file=sys.stderr, flush=True)
    except Exception as error:
        raise RuntimeError(f"failed to write {path!r}: {type(error).__name__}: {error}") from error


def _promptForSavePath(args: argparse.Namespace) -> str:
    """Show a Qt Save-As dialog. Returns empty string on cancel.

    The dialog default name is `{provider}-{timestamp}.txt` so multiple calls
    don't collide. Lives in its own QApplication scope so it doesn't fight the
    bridge service's Qt app (we're in the CLI client process here).
    """
    target = normalizeChatTarget(getattr(args, "chat_target", "") or getattr(args, "target", ""))
    defaultName = f"{target or 'chat'}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    defaultDir = str(Path.home() / "Downloads")
    try:
        from PySide6.QtWidgets import QApplication, QFileDialog   # type: ignore[import-not-found]
    except Exception as error:
        print(f"[ERROR:save-file] Qt not available, falling back to home dir. {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return str(Path.home() / defaultName)
    appExisted = QApplication.instance() is not None
    app = QApplication.instance() or QApplication([])
    try:
        chosen, _selectedFilter = QFileDialog.getSaveFileName(
            None,
            f"Save {chatProviderLabelLocal(target)} response",
            str(Path(defaultDir) / defaultName),
            "Text (*.txt);;Markdown (*.md);;All files (*.*)",
        )
    finally:
        if not appExisted:
            try: app.quit()
            except Exception: pass
    return chosen or ""


def chatProviderLabelLocal(target: str) -> str:
    t = normalizeChatTarget(target)
    if t == "chatgpt": return "ChatGPT"
    if t == "gemini":  return "Gemini"
    if t == "claude":  return "Claude"
    return "Grok"


def runChatCommand(args: argparse.Namespace) -> int:
    try:
        chat = parseChatArgs(args.chat)
    except Exception as error:
        print(f"[ERROR:bridge-client] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2
    jobId = f"cli-{int(time.time() * 1000)}"
    chatTimeout = int(args.chat_timeout or 240)
    payload = {
        "action": "chat",
        "target": normalizeChatTarget(chat["target"]),
        "deployment": chat["deployment"],
        "message": chat["message"],
        "attachments": buildBridgeAttachmentPayloads(getattr(args, "attach", []) or []),
        "timeoutSeconds": chatTimeout,
        "async": True,
        "jobId": jobId,
    }
    args.chat_target = normalizeChatTarget(chat["target"])
    chatDebugTrace(args, "parsed chat command", target=args.chat_target, deployment=chat["deployment"], url=getattr(args, "url", ""), chars=len(chat["message"]), attachments=len(payload.get("attachments") or []), timeoutSeconds=chatTimeout, jobId=jobId)

    def _waitForChatResult(ack: dict[str, Any]) -> dict[str, Any]:
        chatDebugTrace(args, "chat ack received", ack=ack)
        if not (ack.get("accepted") and ack.get("jobId")):
            # Compatibility with older resident services that return the final response directly.
            return ack
        ackJobId = str(ack.get("jobId"))
        deadline = time.monotonic() + chatTimeout + 15
        pollCount = 0
        lastProgressText = ""
        while time.monotonic() < deadline:
            pollCount += 1
            try:
                status = bridgeRequest({"action": "chat-result", "jobId": ackJobId}, port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=10)
            except Exception as error:
                chatDebugTrace(args, "chat-result poll failed", jobId=ackJobId, poll=pollCount, error=f"{type(error).__name__}: {error}")
                time.sleep(1.0)
                continue
            if not status.get("pending"):
                chatDebugTrace(args, "chat-result complete", jobId=ackJobId, poll=pollCount, status=status)
                result = status.get("result")
                return result if isinstance(result, dict) else {"ok": False, "error": f"invalid chat-result payload: {type(result).__name__}", "rawStatus": status}
            progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
            progressText = json.dumps(progress, ensure_ascii=False, default=str, sort_keys=True) if progress else ""
            if args.debug:
                if progressText and progressText != lastProgressText:
                    print(f"[TRACE:bridge-client] waiting for chat job={ackJobId} elapsed={status.get('elapsedSeconds')} progress={progressText}", file=sys.stderr, flush=True)
                    lastProgressText = progressText
                else:
                    print(f"[TRACE:bridge-client] waiting for chat job={ackJobId} elapsed={status.get('elapsedSeconds')} poll={pollCount}", file=sys.stderr, flush=True)
            time.sleep(1.0)
        return {"ok": False, "eventType": "error", "sendId": ackJobId, "error": f"timed out after {chatTimeout}s waiting for chat result", "hint": "Run with --debug to see bridge-client phases, or run: python start.py --bridge-status", "lastProgress": lastProgressText}

    try:
        if not args.no_chat_service_start:
            chatDebugTrace(args, "ensuring resident bridge service", port=int(args.bridge_port or BRIDGE_SERVICE_PORT), forceRestart=bool(args.force_bridge_restart))
            fresh = ensureFreshBridgeService(args)
            chatDebugTrace(args, "ensureFreshBridgeService returned", ok=bool(fresh))
        chatDebugTrace(args, "sending chat request to bridge", port=int(args.bridge_port or BRIDGE_SERVICE_PORT), jobId=jobId)
        response = _waitForChatResult(bridgeRequest(payload, port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=30))
    except Exception as first_error:
        chatDebugTrace(args, "first chat request failed", error=f"{type(first_error).__name__}: {first_error}")
        if args.no_chat_service_start:
            print(f"[ERROR:bridge-client] bridge service unavailable or stale: {type(first_error).__name__}: {first_error}", file=sys.stderr, flush=True)
            return 2
        try:
            chatDebugTrace(args, "starting bridge service after first failure")
            process = startBridgeServiceProcess(args)
            chatDebugTrace(args, "bridge service process started", pid=getattr(process, "pid", 0))
            if not waitForBridgeService(port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=90, debug=args.debug, process=process, logPath=LOGS / "bridge_service.log"):
                print("[ERROR:bridge-client] resident bridge service did not become ready", file=sys.stderr, flush=True)
                return 2
            chatDebugTrace(args, "bridge service became ready; resending chat", port=int(args.bridge_port or BRIDGE_SERVICE_PORT), jobId=jobId)
            response = _waitForChatResult(bridgeRequest(payload, port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=30))
        except Exception as error:
            recordException("start.py.runChatCommand", error, extra={"firstError": f"{type(first_error).__name__}: {first_error}"})
            print(f"[ERROR:bridge-client] chat failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            target_for_hint = normalizeChatTarget(getattr(args, "chat_target", "grok"))
            if target_for_hint == "chatgpt":
                print("[HINT] Run once visibly with: python start.py --gpt", file=sys.stderr, flush=True)
                print("[HINT] Log into ChatGPT in the bridge window, leave it running, then use: python start.py --chatgpt \"message\"", file=sys.stderr, flush=True)
            elif target_for_hint == "gemini":
                print("[HINT] Run once visibly with: python start.py --gemini", file=sys.stderr, flush=True)
                print("[HINT] Log into Google in the bridge window, leave it running, then use: python start.py --gemini \"message\"", file=sys.stderr, flush=True)
            elif target_for_hint == "claude":
                print("[HINT] Run once visibly with: python start.py --claude", file=sys.stderr, flush=True)
                print("[HINT] Log into Anthropic in the bridge window, leave it running, then use: python start.py --claude \"message\"", file=sys.stderr, flush=True)
            else:
                print("[HINT] Run once visibly with: python start.py --serve-bridge --show-bridge --target grok", file=sys.stderr, flush=True)
                print("[HINT] After logging into Grok, use: python start.py --chat grok \"message\" --offscreen", file=sys.stderr, flush=True)
            return 2
    chatDebugTrace(args, "final chat response", response=response)
    if response.get("ok"):
        answer = str(response.get("answer") or "")
        attachments = response.get("attachments") if isinstance(response.get("attachments"), list) else []
        toolCalls = response.get("toolCalls") if isinstance(response.get("toolCalls"), list) else []
        if attachments:
            savedLines = ["", "Received attachments saved:"]
            for item in attachments:
                if isinstance(item, dict):
                    savedLines.append(f"- {item.get('path') or item.get('name')}")
            answer = (answer.rstrip() + "\n" + "\n".join(savedLines)).strip()
        if toolCalls:
            toolLines = ["", "ToolCalls executed:"]
            for item in toolCalls:
                if isinstance(item, dict):
                    toolLines.append(f"\n$ {item.get('command')}\nexit={item.get('returncode')}")
                    stdout = str(item.get("stdout") or "").strip()
                    stderr = str(item.get("stderr") or "").strip()
                    error = str(item.get("error") or "").strip()
                    if stdout:
                        toolLines.append("STDOUT:\n" + stdout)
                    if stderr:
                        toolLines.append("STDERR:\n" + stderr)
                    if error:
                        toolLines.append("ERROR:\n" + error)
            answer = (answer.rstrip() + "\n" + "\n".join(toolLines)).strip()
        print(answer, flush=True)
        try:
            saveOutputIfRequested(args, answer)
        except Exception as error:
            print(f"[WARN:save-file] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 0
    try:
        if response.get("shownForRepair") or "surface" in str(response.get("error", "")).lower() or "login" in str(response.get("hint", "")).lower():
            bridgeRequest({"action": "show", "reason": str(response.get("error") or "chat failed")}, port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=5)
    except Exception:
        pass
    print(json.dumps(response, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
    return 1

def runBridgeStatus(args: argparse.Namespace) -> int:
    try:
        response = bridgeRequest({"action": "status"}, port=int(args.bridge_port or BRIDGE_SERVICE_PORT), timeout=10)
        matches, reason = sourceSignatureMatches(response)
        response["currentSourceSignature"] = currentSourceSignature()
        response["sourceMatchesCurrent"] = bool(matches)
        response["sourceMatchReason"] = reason
        print(json.dumps(response, ensure_ascii=False, indent=2), flush=True)
        return 0 if response.get("ok") and matches else 1
    except Exception as error:
        print(f"[ERROR:bridge-client] bridge service unavailable: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        print(json.dumps({"ok": False, "currentSourceSignature": currentSourceSignature()}, ensure_ascii=False, indent=2), flush=True)
        return 2


class StartLifecycle:
    """Minimal launcher lifecycle wrapper so startup work has one owned surface."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def runApplication(self) -> int:
        args = self.args
        tryLoadFlatlineDebugger(debug=args.debug)
        configureQtEnvironment(args)
        # Do not run broad stale cleanup on ordinary/visible starts. A watcher
        # launching ``start.py --debug`` can be the parent of the resident bridge;
        # killing it with taskkill /T kills the --chat service. Bridge service
        # replacement is handled separately and only targets --serve-bridge.
        if args.stale_process_cleanup and not args.no_stale_process_kill and not args.serve_bridge:
            killStaleSuperGrokProcesses(debug=args.debug, bridgeOnly=False, includeBridgeServices=False, reason="explicit non-bridge stale cleanup")
        ensureRuntimeDependencies(debug=args.debug, autoInstall=not args.no_deps)

        from app import runApplication as runSuperGrokApplication

        return runSuperGrokApplication(
            initialUrl=args.url,
            target=normalizeChatTarget(getattr(args, "target", "") or getattr(args, "chat_target", "") or ("chatgpt" if urlLooksLikeTarget(args.url, "chatgpt") else "grok")),
            debug=args.debug,
            profileDir=args.profile_dir,
            remoteDebugPort=args.remote_debug_port,
            processTtlSeconds=args.process_ttl,
            serviceMode=bool(args.serve_bridge),
            servicePort=int(args.bridge_port or BRIDGE_SERVICE_PORT),
            hideWindow=bool(args.serve_bridge and args.offscreen and not args.show_bridge),
            windowMode=bridgeWindowModeForArgs(args),
            offscreenMode=normalizeOffscreenMode(args),
        )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 1 and argv[0].lower() in {"help", "man", "/?", "/help", "-?"}:
        print(buildParser().format_help())
        return 0
    if len(argv) == 1 and argv[0].lower() in {"--ver", "--version", "-v", "/ver", "/version"}:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    parser = buildParser()
    args, unknown = parser.parse_known_args(argv)
    args.chatgpt_alias_requested = chatGptFlagPresent(argv)
    args.gemini_alias_requested = geminiFlagPresent(argv)
    args.claude_alias_requested = claudeFlagPresent(argv)
    applyChatUnknownTail(args, unknown, argv)
    chatgpt_login_bridge = chatGptBridgeLoginRequested(args)
    gemini_login_bridge = geminiBridgeLoginRequested(args)
    claude_login_bridge = claudeBridgeLoginRequested(args)
    if chatgpt_login_bridge:
        configureChatGptLoginBridgeArgs(args)
    elif gemini_login_bridge:
        configureGeminiLoginBridgeArgs(args)
    elif claude_login_bridge:
        configureClaudeLoginBridgeArgs(args)
    else:
        normalizeChatModeArgs(args)
    normalizeTargetUrlArgs(args)
    resetRunLogs("serve-bridge" if getattr(args, "serve_bridge", False) else ("chat" if getattr(args, "chat", None) is not None else "start"))
    if unknown and not chatFlagPresent(argv):
        parser.error("unrecognized arguments: " + " ".join(str(item) for item in unknown))

    if args.debugger_query_surfaces:
        print(debuggerSurfaceLine(), flush=True)
        return 0

    if args.debugger_vardump:
        payload = {
            "app": APP_NAME,
            "pid": os.getpid(),
            "root": str(ROOT),
            "surfaces": list(DEBUGGER_SURFACES),
            "heartbeatDb": str(DATA / "supergrok_bridge_debugger.sqlite3"),
            "vendorClaude": str(VENDOR_CLAUDE),
            "whitepapersRead": readWhitepaperRecommendations(),
            "bridgeService": {"host": BRIDGE_SERVICE_HOST, "port": int(args.bridge_port or BRIDGE_SERVICE_PORT)},
            "offscreenMode": normalizeOffscreenMode(args),
            "bridgeWindowMode": bridgeWindowModeForArgs(args),
            "qtQpaPlatform": os.environ.get("QT_QPA_PLATFORM", ""),
            "currentSourceSignature": currentSourceSignature(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        return 0

    if args.debugger_menu:
        showDebuggerMenu()
        return 0

    if args.serve_bridge:
        replaceBridgeServiceBeforeServing(args)

    if args.bridge_status:
        return runBridgeStatus(args)

    if args.chat is not None:
        return runChatCommand(args)

    detector_names = detectorNamesFromArgs(args)
    if detector_names is not None or args.detector_selftest or args.manual:
        return runClaudeDetectors(args, detector_names)

    lifecycle = StartLifecycle(args)
    return lifecycle.runApplication()


if __name__ == "__main__":
    raise SystemExit(main())
