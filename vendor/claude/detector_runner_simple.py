#!/usr/bin/env python3
from __future__ import annotations

from classes._exception_recording import recordException, installExceptionRecorderBuiltin
import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def _run(cmd: list[str], cwd: Path) -> int:
    result = subprocess.run(cmd, cwd=str(cwd), text=True, stdin=subprocess.DEVNULL)  # lifecycle-bypass-ok detector-runner-ok: report-only detector runner subprocess
    return int(result.returncode or 0)


def _script(base: Path, name: str) -> str:
    return str(base / "vendor" / "claude" / name)


def _normal_targets(base: Path, targets: Iterable[str]) -> list[str]:
    items = [str(item) for item in targets if str(item).strip()]
    return items or ["."]


def _run_standard_detector(base: Path, script_name: str, output_name: str, targets: list[str], extra: list[str] | None = None) -> int:
    cmd = [sys.executable, "-S", _script(base, script_name), "--root", str(base), "--output", str(base / output_name)]
    if extra:
        cmd.extend(extra)
    cmd.extend(_normal_targets(base, targets))
    return _run(cmd, base)


def _run_monkey_detector(base: Path, output_name: str) -> int:
    # One canonical monkey-patch detector route. Import it in-process instead
    # of launching it as a script because this detector is pure AST/report work
    # and some shell hosts can leave the standalone script waiting after the
    # report is written. The launcher still exits immediately after this route.
    script = Path(_script(base, "monkeypatchdetect.py"))
    spec = importlib.util.spec_from_file_location("cutiepy_monkeypatchdetect", script)
    if spec is None or spec.loader is None:
        print(f"[detector:monkey] unable to load {script}", file=sys.stderr)
        return 2
    module = importlib.util.module_from_spec(spec)
    sys.modules["cutiepy_monkeypatchdetect"] = module  # nopatch: detector import registration
    try:
        spec.loader.exec_module(module)
        return int(module.run(str(base), str(base / output_name)) or 0)
    finally:
        sys.modules.pop("cutiepy_monkeypatchdetect", None)


DETECTOR_SPECS: dict[str, tuple[str, str]] = {
    "bypass": ("lifecyclebypassdetector.py", "lifecycle_bypass_report.txt"),
    "lifecycle": ("lifecyclebypassdetector.py", "lifecycle_bypass_report.txt"),
    "lifecycle-bypass": ("lifecyclebypassdetector.py", "lifecycle_bypass_report.txt"),
    "rawsql": ("rawsqldetector.py", "rawsql.txt"),
    "raw-sql": ("rawsqldetector.py", "rawsql.txt"),
    "recursion": ("recursiondetector.py", "recursion.txt"),
    "swallowed": ("swallowedexceptionsdetector.py", "swallowed.txt"),
    "swallowed-exceptions": ("swallowedexceptionsdetector.py", "swallowed.txt"),
    "redundant": ("redundant.py", "redundant.txt"),
    "redundant-code": ("redundant.py", "redundant.txt"),
    "redundant-detector": ("redundant.py", "redundant.txt"),
    "redundancy": ("redundant.py", "redundant.txt"),
}

EXISTING_CERTIFICATION_SPECS: dict[str, tuple[str, str]] = {
    "fileio": ("fileiodetector.py", "fileio.txt"),
    "file-io": ("fileiodetector.py", "fileio.txt"),
    "processfault": ("processfaultdetector.py", "process_faults.txt"),
    "process-fault": ("processfaultdetector.py", "process_faults.txt"),
    "process-faults": ("processfaultdetector.py", "process_faults.txt"),
    "phaseownership": ("phaseownershipdetector.py", "phase_ownership.txt"),
    "phase-ownership": ("phaseownershipdetector.py", "phase_ownership.txt"),
}

ALL_DETECTORS = (
    "bypass",
    "rawsql",
    "recursion",
    "swallowed",
    "redundant",
    "monkey",
    "fileio",
    "processfault",
    "phaseownership",
)


def run_detector_by_name(base: Path, detector: str, targets: list[str]) -> int:
    detector = str(detector or "").strip().lower().replace("_", "-")
    if detector in {"monkey", "monkeypatch", "monkey-patch", "monkey-patch-detector", "monkey-scan", "monkey-patch-scan"}:
        return _run_monkey_detector(base, "monkeypatches.txt")
    if detector in DETECTOR_SPECS:
        script_name, output_name = DETECTOR_SPECS[detector]
        return _run_standard_detector(base, script_name, output_name, targets)
    if detector in EXISTING_CERTIFICATION_SPECS:
        script_name, output_name = EXISTING_CERTIFICATION_SPECS[detector]
        return _run_standard_detector(base, script_name, output_name, targets)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--detector", default="bypass")
    parser.add_argument("--targets", nargs="*", default=["."])
    args = parser.parse_args(argv)
    base = Path(args.base_dir).resolve()
    detector = str(args.detector or "").strip().lower().replace("_", "-")
    targets = _normal_targets(base, args.targets)

    if detector in {"certify", "certification", "all", "all-detectors", "claude-detectors"}:
        exit_codes = [run_detector_by_name(base, name, targets) for name in ALL_DETECTORS]
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1 if any(exit_codes) else 0)

    routed = run_detector_by_name(base, detector, targets)
    if routed != 2:
        return routed

    print(f"Unknown detector: {args.detector}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
