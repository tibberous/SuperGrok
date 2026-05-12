#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility wrapper.

The active detector suite moved from /vendor/clod to /vendor/claude. This file
keeps the old import path working for any stale scripts/autoload commands while
avoiding a second monkey-patch detector implementation.
"""

import argparse
from pathlib import Path

from vendor.claude.run_claude_reports import run_reports as run_claude_reports


def run_reports(target: Path | None = None, report_path: Path | None = None) -> Path:
    # target is accepted for backward compatibility; Claude detectors scan the project root.
    return run_claude_reports(report_path, None, echo=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compatibility wrapper for /vendor/claude detector reports.")
    parser.add_argument("target", nargs="?", default="", help="Ignored compatibility argument.")
    parser.add_argument("--out", default="", help="Combined report text output path.")
    args = parser.parse_args(argv)
    report = run_reports(Path(args.target) if args.target else None, Path(args.out) if args.out else None)
    print(f"[INFO:claude] report written: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
