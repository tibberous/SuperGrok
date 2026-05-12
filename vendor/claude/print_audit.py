"""
print_audit.py -- Find print() calls that don't follow [TAG:category] format.

The standard format is:
    print(f"[TAG:category] message")

Examples of compliant prints:
    print(f"[ERROR:db] Query failed: {e}")
    print(f"[WARNING:color] Could not parse '{s}'")
    print(f"[TRACE:startup] Phase 3 complete")
    print(f"[INFO:model] Response received")
    print(f"[DEBUG:auth] Token refreshed")

Anything else is a raw debug print that should either be:
  - Formatted to [TAG:category] standard
  - Removed before shipping

Usage:
    python tools/print_audit.py trio.py
    python tools/print_audit.py trio.py --raw-only     # only show non-standard
    python tools/print_audit.py trio.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


TAG_PATTERN = re.compile(r'^\[([A-Z][A-Z0-9_]*):([a-z][a-z0-9_]*)\]')

KNOWN_TAGS = {
    "ERROR", "WARNING", "WARN", "INFO", "DEBUG",
    "TRACE", "SQL", "DB", "HTTP", "AUTH",
    "HEARTBEAT", "PHASE", "PERF", "I18N",
}


@dataclass
class PrintCall:
    lineno: int
    snippet: str
    conforms: bool
    tag: str
    category: str
    reason: str


def classify(text: str) -> tuple[bool, str, str, str]:
    """Returns (conforms, tag, category, reason)."""
    stripped = text.strip().strip("'\"").strip()

    m = TAG_PATTERN.match(stripped)
    if m:
        return True, m.group(1), m.group(2), ""

    # f-string might start with [TAG:cat]
    if stripped.startswith("[") and ":" in stripped[:30]:
        # e.g. "[ERROR:db] ..."
        m2 = re.match(r'^\[([A-Z][A-Z0-9_]*):([a-z][a-z0-9_]+)\]', stripped)
        if m2:
            return True, m2.group(1), m2.group(2), ""
        return False, "", "", "bracket prefix but non-standard tag format"

    if not stripped:
        return False, "", "", "empty print (spacer/debug)"

    return False, "", "", "raw print — no [TAG:category] prefix"


def audit_file(path: Path) -> list[PrintCall]:
    source = path.read_text(encoding="utf-8", errors="replace")
    lines  = source.splitlines()

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"[ERROR] Syntax error: {e}", file=sys.stderr)
        return []

    results: list[PrintCall] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "print"):
            continue

        lineno  = node.lineno
        snippet = lines[lineno - 1].strip() if lineno <= len(lines) else ""

        # Try to extract the first string argument
        first_str = ""
        if node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                first_str = arg.value
            elif isinstance(arg, ast.JoinedStr):
                # f-string: reconstruct literal prefix
                parts = []
                for v in arg.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                    else:
                        break
                first_str = "".join(parts)
            elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
                # "prefix" + variable — take left side
                if isinstance(arg.left, ast.Constant):
                    first_str = str(arg.left.value)

        conforms, tag, cat, reason = classify(first_str)
        results.append(PrintCall(lineno, snippet, conforms, tag, cat, reason))

    results.sort(key=lambda r: r.lineno)
    return results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--raw-only", action="store_true",
                   help="Only show non-standard prints")
    p.add_argument("--json",     action="store_true")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    results = audit_file(path)

    if args.json:
        out = [vars(r) for r in results]
        if args.raw_only:
            out = [r for r in out if not r["conforms"]]
        print(json.dumps(out, indent=2))
        return

    total   = len(results)
    ok      = sum(1 for r in results if r.conforms)
    bad     = total - ok

    display = [r for r in results if not r.conforms] if args.raw_only else results

    if not display:
        print(f"All {total} print() calls conform to [TAG:category] format.")
        return

    if not args.raw_only:
        print(f"print() audit: {path}  ({ok} ok, {bad} non-standard of {total})")
        print()

    for r in display:
        mark = "OK " if r.conforms else "!!!"
        tag_str = f"[{r.tag}:{r.category}]" if r.conforms else f"({r.reason})"
        print(f"  {mark} L{r.lineno:<6} {tag_str}")
        if not r.conforms:
            print(f"         {r.snippet[:100]}")

    print()
    if bad:
        print(f"  {bad} print(s) need [TAG:category] prefix or removal.")
        print()
        print("  Standard tags:  ERROR  WARNING  INFO  DEBUG  TRACE")
        print("  Format:         print(f\"[ERROR:db] message {var}\")")


if __name__ == "__main__":
    main()
