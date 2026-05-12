#!/usr/bin/env python3
from __future__ import annotations

import datetime
import os
import re
import signal
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
REPORTS = ROOT / "reports"

@dataclass(frozen=True)
class DetectorSpec:
    key: str
    script: str
    report: str
    aliases: tuple[str, ...] = ()
    monkey_style: bool = False
    extra_args: tuple[str, ...] = ()
    description: str = ""

DETECTORS: tuple[DetectorSpec, ...] = (
    DetectorSpec("monkey", "monkeypatchdetect.py", "monkeypatches.txt", ("monkeypatch", "monkey-patch", "patch", "patches"), True, description="Monkey patch mutation detector"),
    DetectorSpec("lifecycle-bypass", "lifecyclebypassdetector.py", "lifecyclebypass.txt", ("lifecycle", "lifecycle_bypass", "bypass"), description="Direct subprocess/thread/runtime bypass detector"),
    DetectorSpec("raw-sql", "rawsqldetector.py", "rawsql.txt", ("rawsql", "raw_sql", "sql", "naked", "naked-sql"), description="Raw database access detector"),
    DetectorSpec("recursion", "recursiondetector.py", "recursion.txt", ("recursive",), description="Direct self-recursion detector"),
    DetectorSpec("swallowed", "swallowedexceptionsdetector.py", "swallowed.txt", ("swallowed-exceptions", "swallow", "exceptions"), description="Swallowed exception detector"),
    DetectorSpec("redundant", "redundant.py", "redundant.txt", ("redundant-code", "duplicate", "redundancy"), description="Repeated source-shape detector"),
    DetectorSpec("file-io", "fileiodetector.py", "fileio.txt", ("fileio", "files", "file_io"), description="Raw file I/O detector"),
    DetectorSpec("process-faults", "processfaultdetector.py", "process_faults.txt", ("process-fault", "processes", "processfaults"), description="Process fault callback detector"),
    DetectorSpec("phase-ownership", "phaseownershipdetector.py", "phase_ownership.txt", ("phase", "phases", "phase_owner"), description="Phase ownership detector"),
    DetectorSpec("phase-hooks", "phasehooksdetector.py", "phase_hooks.txt", ("phasehooks", "phase-hook", "hooks"), description="Phase hook and main() discipline detector"),
    DetectorSpec("nonconform", "nonconformdetector.py", "nonconform.txt", ("nonconformance", "naming-nonconform", "threads", "thread", "thread-safety"), description="Naming, required symbol, and banned Thread detector"),
    DetectorSpec("comport", "nonconformdetector.py", "comport.txt", ("contract", "contracts", "architecture-contract", "architecture-contracts"), extra_args=("--no-prefix-check", "--no-symbol-check"), description="Architecture comport detector focused on banned constructs"),
    DetectorSpec("bad-code", "badcodedetector.py", "badcode.txt", ("badcode", "code"), description="General AST bad-code detector"),
    DetectorSpec("unlocalized", "unlocalizeddetector.py", "unlocalized.txt", ("localization", "localisation", "i18n"), description="Raw UI string detector"),
)

ALIAS_MAP: dict[str, DetectorSpec] = {}
for spec in DETECTORS:
    ALIAS_MAP[spec.key] = spec
    for alias in spec.aliases:
        ALIAS_MAP[alias] = spec


def normalize_name(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    while text.startswith("-") or text.startswith("/"):
        text = text[1:]
    return text


def resolve_specs(names: Iterable[str] | None) -> list[DetectorSpec]:
    requested = [normalize_name(name) for name in (names or []) if normalize_name(name)]
    if not requested or any(name in {"all", "certify", "claude", "detectors", "health"} for name in requested):
        return list(DETECTORS)
    selected: list[DetectorSpec] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for name in requested:
        spec = ALIAS_MAP.get(name)
        if spec is None:
            unknown.append(name)
            continue
        if spec.key not in seen:
            selected.append(spec)
            seen.add(spec.key)
    if unknown:
        valid = ", ".join(spec.key for spec in DETECTORS)
        raise SystemExit(f"[ERROR:detectors] Unknown detector(s): {', '.join(unknown)}\nValid: {valid}")
    return selected


def detector_python() -> list[str]:
    override = os.environ.get("DETECTOR_PYTHON", "").strip()
    if override:
        return shlex.split(override)
    return [sys.executable]


def iter_app_python_files(root: Path) -> list[Path]:
    skip = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", "node_modules", "vendor"}
    files: list[Path] = []
    for path in root.rglob("*.py"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in skip for part in rel.parts):
            continue
        files.append(path)
    return sorted(files)


def command_for(spec: DetectorSpec, *, root: Path, output: Path, paths: list[Path] | None = None) -> list[str]:
    script = VENDOR / spec.script
    seeds = [str(path) for path in (paths or [])]
    if not seeds:
        defaults = [root / "start.py", root / "supergrok_bridge"]
        seeds = [str(path) for path in defaults if path.exists()] or [str(root)]
    if spec.monkey_style:
        # Legacy monkeypatchdetect.py accepts one positional root only, not multiple scan seeds.
        return [*detector_python(), str(script), str(root), "--out", str(output)]
    return [*detector_python(), str(script), "--root", str(root), "--output", str(output), *spec.extra_args, *seeds]


def parse_report_metrics(path: Path) -> tuple[int | None, int | None]:
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")
    files = None
    file_matches = re.findall(r"^Files scanned\s*:\s*(\d+)", text, flags=re.M)
    if file_matches:
        files = sum(int(value) for value in file_matches)
    total_match = re.search(r"^Total findings\s*:\s*(\d+)", text, flags=re.M)
    if total_match:
        return int(total_match.group(1)), files
    simple_match = re.search(r"^Findings\s*:\s*(\d+)", text, flags=re.M)
    if simple_match:
        return int(simple_match.group(1)), files
    patterns = [
        r"^Thread/Process findings\s*:\s*(\d+)",
        r"^Phase findings\s*:\s*(\d+)",
        r"^Phase hook findings\s*:\s*(\d+)",
        r"^main\(\) discipline findings\s*:\s*(\d+)",
        r"^Prefix pollution findings\s*:\s*(\d+)",
        r"^Banned construct findings\s*:\s*(\d+)",
        r"^Missing required symbols\s*:\s*(\d+)",
    ]
    values: list[int] = []
    for pattern in patterns:
        values.extend(int(value) for value in re.findall(pattern, text, flags=re.M))
    if values:
        return sum(values), files
    if "No monkey-patches detected" in text or "No nonconformances found" in text or "No phase hook" in text:
        return 0, files
    return None, files


def run_one(spec: DetectorSpec, *, root: Path = ROOT, paths: list[Path] | None = None, timeout: int = 300) -> tuple[int, str, Path]:
    root = root.resolve()
    output_path = root / "logs" / spec.report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = VENDOR / spec.script
    if not script.exists():
        body = f"[ERROR:detectors] Missing detector script: {script}\n"
        output_path.write_text(body, encoding="utf-8")
        return 2, body, output_path
    command = command_for(spec, root=root, output=output_path, paths=paths)
    header = f"[detector:{spec.key}] command: {' '.join(shlex.quote(part) for part in command)}"
    try:
        popen_kwargs: dict[str, object] = {
            "cwd": str(root),
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)  # lifecycle-bypass-ok detector-runner-ok: report-only detector process with timeout/process-tree cleanup
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            code = int(process.returncode or 0)
        except subprocess.TimeoutExpired:
            code = 124
            if os.name == "nt":
                cleanup = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)  # lifecycle-bypass-ok detector-timeout-cleanup-ok
                stderr = str((cleanup.stdout or ""))
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    process.kill()
                stderr = ""
            try:
                stdout_after, stderr_after = process.communicate(timeout=10)
            except Exception:
                stdout_after, stderr_after = "", ""
            stdout = str(stdout_after or "")
            stderr = (str(stderr or "") + "\n" + str(stderr_after or "")).strip()
        body = header + "\n" + (stdout or "")
        if stderr:
            body += "\n[stderr]\n" + stderr
        if code == 124:
            body = header + f"\n[ERROR:detectors] timed out after {timeout}s and the detector process tree was killed\n" + (stdout or "") + ("\n[stderr]\n" + stderr if stderr else "")
            output_path.write_text(body, encoding="utf-8")
    except Exception as error:
        code = 2
        body = header + f"\n[ERROR:detectors] {type(error).__name__}: {error}\n"
        output_path.write_text(body, encoding="utf-8")
    if not output_path.exists():
        output_path.write_text(body or f"{spec.key}: no report generated\n", encoding="utf-8")
    metrics = parse_report_metrics(output_path)
    if metrics != (None, None):
        body += f"\n[detector:{spec.key}] parsed findings={metrics[0]} files_scanned={metrics[1]}\n"
    return code, body, output_path


def run_reports(
    report_path: Path | None = None,
    names: Iterable[str] | None = None,
    *,
    root: Path = ROOT,
    paths: Iterable[str | Path] | None = None,
    echo: bool = True,
    timeout: int = 300,
    coverage_fail: bool = False,
) -> Path:
    root = Path(root).resolve()
    selected = resolve_specs(names)
    report = report_path or (root / "reports" / "claude_detectors_report_latest.txt")
    report.parent.mkdir(parents=True, exist_ok=True)
    scan_paths = [Path(p).resolve() for p in (paths or [])]
    app_files = iter_app_python_files(root)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    lines: list[str] = [
        "CLAUDE / FLATLINE DETECTORS COMBINED REPORT",
        "===========================================",
        "",
        f"Generated at: {started}",
        f"Root: {root}",
        f"Non-vendor Python files visible: {len(app_files)}",
        f"Selected detectors: {', '.join(spec.key for spec in selected)}",
        "",
    ]
    exit_codes: list[tuple[str, int, Path, int | None, int | None]] = []
    for spec in selected:
        code, body, out = run_one(spec, root=root, paths=scan_paths or None, timeout=timeout)
        findings, files_scanned = parse_report_metrics(out)
        exit_codes.append((spec.key, code, out, findings, files_scanned))
        relout = out.relative_to(root) if out.is_relative_to(root) else out
        header = f"[{spec.key}] exit={code} report={relout} findings={findings} files_scanned={files_scanned}"
        if echo:
            print("\n" + header)
            print("-" * len(header))
            print(body.rstrip() if body.strip() else "<no console output>")
        lines.append(header)
        lines.append("-" * len(header))
        lines.append(body.rstrip() if body.strip() else "<no console output>")
        lines.append("")
    if coverage_fail and len(app_files) <= 1:
        lines.append("[COVERAGE] FAILED: detector scan saw one or fewer non-vendor Python files.")
    lines.append("SUMMARY")
    lines.append("=======")
    for key, code, out, findings, files_scanned in exit_codes:
        status = "CLEAN" if code == 0 else "FINDINGS/ERROR"
        relout = out.relative_to(root) if out.is_relative_to(root) else out
        lines.append(f"{key}: exit={code} {status} findings={findings} files_scanned={files_scanned} report={relout}")
    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if echo:
        print(f"\n[INFO:detectors] combined report written: {report}")
    return report


def run_and_return_code(
    report_path: Path | None = None,
    names: Iterable[str] | None = None,
    *,
    root: Path = ROOT,
    paths: Iterable[str | Path] | None = None,
    echo: bool = True,
    timeout: int = 300,
    coverage_fail: bool = False,
) -> int:
    selected = resolve_specs(names)
    report = run_reports(report_path, [spec.key for spec in selected], root=root, paths=paths, echo=echo, timeout=timeout, coverage_fail=coverage_fail)
    text = report.read_text(encoding="utf-8", errors="replace")
    failed = bool(re.search(r": exit=(?!0)\d+", text))
    if coverage_fail and "[COVERAGE] FAILED" in text:
        failed = True
    return 1 if failed else 0


def run_selftest(timeout: int = 120) -> int:
    import tempfile
    temp = Path(tempfile.mkdtemp(prefix="supergrok_claude_canary_"))
    canary = temp / "canary.py"
    canary.write_text("""
import os, sqlite3, subprocess, threading
from pathlib import Path

def recursive():
    return recursive()

def swallowed():
    try:
        raise RuntimeError('boom')
    except Exception:
        pass

def raw_sql():
    conn = sqlite3.connect(':memory:')
    conn.execute('select 1')

def raw_file():
    open('x.txt', 'w').write('hi')
    Path('x.txt').read_text()

def bypass():
    subprocess.run(['echo', 'hi'])
    threading.Thread(target=lambda: None).start()

def monkey():
    os.path.exists = lambda p: True

def bad_code(flag=True):
    flag = flag
    if flag == flag:
        return True
    return not not flag
""".lstrip(), encoding="utf-8")
    print(f"Detector canary root: {temp}")
    failures = 0
    per_route_timeout = max(3, min(int(timeout or 8), 8))
    for spec in resolve_specs(["all"]):
        code, body, out = run_one(spec, root=temp, timeout=per_route_timeout)
        findings, files_scanned = parse_report_metrics(out)
        print(f"{spec.key}: exit={code} findings={findings} files_scanned={files_scanned} report={out}")
        if code == 124 or not out.exists():
            failures += 1
    print(f"Detector canary route failures: {failures}")
    print(f"Detector canary logs kept at: {temp / 'logs'}")
    return 1 if failures else 0


def print_manual() -> int:
    manual = VENDOR / "DETECTORS_MANUAL.md"
    if not manual.exists():
        print(f"missing manual: {manual}", file=sys.stderr)
        return 2
    print(manual.read_text(encoding="utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and normalize_name(args[0]) in {"selftest", "detector-selftest"}:
        raise SystemExit(run_selftest())
    if args and normalize_name(args[0]) in {"manual", "man"}:
        raise SystemExit(print_manual())
    raise SystemExit(run_and_return_code(names=args or ["all"]))
