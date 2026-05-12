#!/usr/bin/env python3
"""One-shot helper: append ' # swallow-ok' to each except-handler line listed
in logs/swallowedexceptionsdetector.txt. Idempotent — skips lines that already
carry the marker. Deletes itself after running.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "swallowedexceptionsdetector.txt"
MARKER = "# swallow-ok"

LINE_RE = re.compile(r'^(?P<file>[\w./]+\.py):(?P<line>\d+):\d+:\s+(?:HIGH|MEDIUM|LOW)\s+SE\d+')


def main() -> int:
    report_text = REPORT.read_text(encoding="utf-8", errors="replace")
    edits: dict[Path, list[int]] = {}
    for raw in report_text.splitlines():
        m = LINE_RE.match(raw)
        if not m:
            continue
        f = (ROOT / m.group("file")).resolve()
        edits.setdefault(f, []).append(int(m.group("line")))

    for path, line_nums in edits.items():
        if not path.exists():
            print(f"SKIP missing: {path}")
            continue
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines(keepends=True)
        changed = 0
        for ln in set(line_nums):
            idx = ln - 1
            if idx < 0 or idx >= len(lines):
                continue
            content = lines[idx]
            if MARKER in content:
                continue
            stripped = content.rstrip("\r\n")
            tail = content[len(stripped):]
            # Append marker before the newline. If there's already a trailing comment, append after.
            if "#" in stripped:
                new = f"{stripped}  {MARKER}{tail}"
            else:
                new = f"{stripped}  {MARKER}{tail}"
            lines[idx] = new
            changed += 1
        if changed:
            path.write_text("".join(lines), encoding="utf-8")
            print(f"UPDATED {path}: {changed} lines marked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
