"""
dead_code.py -- Find functions defined but never called in a file.

A function is "dead" if its name never appears as a call anywhere
in the file. False positives: event handlers, __dunder__ methods,
and anything called dynamically (getattr, signals). Review manually.

Usage:
    python tools/dead_code.py trio.py
    python tools/dead_code.py trio.py --threshold 5   # only if defined 5+ lines long
    python tools/dead_code.py trio.py --json
    python tools/dead_code.py trio.py --include-private   # include _prefixed names
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


# Names that are always "called" implicitly — never flag these
ALWAYS_LIVE = {
    "__init__", "__del__", "__repr__", "__str__", "__len__",
    "__getitem__", "__setitem__", "__delitem__", "__contains__",
    "__iter__", "__next__", "__enter__", "__exit__",
    "__eq__", "__lt__", "__gt__", "__le__", "__ge__", "__ne__",
    "__hash__", "__bool__", "__call__", "__add__", "__radd__",
    "__mul__", "__rmul__", "__sub__", "__truediv__", "__floordiv__",
    "__mod__", "__pow__", "__and__", "__or__", "__xor__",
    "__lshift__", "__rshift__", "__neg__", "__pos__", "__abs__",
    "__invert__", "__index__", "__int__", "__float__", "__complex__",
    "__bytes__", "__format__", "__sizeof__", "__class_getitem__",
    "main", "run", "start", "setup", "teardown",
    # Qt slots / signals commonly connected by name
    "closeEvent", "resizeEvent", "paintEvent", "mousePressEvent",
    "mouseReleaseEvent", "mouseMoveEvent", "keyPressEvent",
    "keyReleaseEvent", "wheelEvent", "dragEnterEvent", "dropEvent",
    "showEvent", "hideEvent", "changeEvent", "focusInEvent",
    "focusOutEvent", "contextMenuEvent", "timerEvent", "eventFilter",
}


def collect_definitions(tree: ast.AST) -> dict[str, dict]:
    """Map name -> {lineno, end_lineno, is_method, class_name}"""
    defs = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.class_stack = []

        def visit_ClassDef(self, node):
            self.class_stack.append(node.name)
            self.generic_visit(node)
            self.class_stack.pop()

        def visit_FunctionDef(self, node):
            defs[node.name] = {
                "lineno":     node.lineno,
                "end_lineno": getattr(node, "end_lineno", node.lineno),
                "is_method":  bool(self.class_stack),
                "class_name": self.class_stack[-1] if self.class_stack else None,
            }
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return defs


def collect_call_names(source: str) -> set[str]:
    """
    All names that appear as direct calls or in dynamic-call patterns.
    Uses both AST (clean calls) and regex (getattr, signals, connect).
    """
    names: set[str] = set()

    # AST calls
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
    except SyntaxError:
        pass

    # Regex: getattr(obj, "name"), connect("name"), .connect(slot)
    # Also catches strings that look like method references
    for m in re.finditer(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']', source):
        names.add(m.group(1))

    return names


def line_count(info: dict) -> int:
    return info["end_lineno"] - info["lineno"] + 1


def find_dead(path: Path, min_lines: int, include_private: bool) -> list[dict]:
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"[ERROR] Syntax error in {path}: {e}", file=sys.stderr)
        return []

    defs  = collect_definitions(tree)
    calls = collect_call_names(source)

    dead = []
    for name, info in sorted(defs.items(), key=lambda x: x[1]["lineno"]):
        if name in ALWAYS_LIVE:
            continue
        if not include_private and name.startswith("_"):
            continue
        if line_count(info) < min_lines:
            continue
        if name not in calls:
            dead.append({
                "name":       name,
                "lineno":     info["lineno"],
                "end_lineno": info["end_lineno"],
                "lines":      line_count(info),
                "is_method":  info["is_method"],
                "class_name": info["class_name"],
            })

    return dead


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--threshold",       type=int, default=1,
                   help="Only report functions with at least N lines (default 1)")
    p.add_argument("--include-private", action="store_true",
                   help="Include _prefixed names (often internal helpers)")
    p.add_argument("--json",            action="store_true")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    dead = find_dead(path, args.threshold, args.include_private)

    if args.json:
        print(json.dumps(dead, indent=2))
        return

    if not dead:
        print(f"No dead functions found in {path}")
        return

    print(f"Potentially dead functions in {path}: {len(dead)}")
    print()
    for d in dead:
        loc = f"L{d['lineno']}"
        tag = f"{d['class_name']}." if d["class_name"] else ""
        print(f"  {loc:<8} {d['lines']:>4} lines  {tag}{d['name']}")
    print()
    print("NOTE: Review manually. Event handlers, Qt slots, and anything")
    print("called via getattr/signals may show as dead but aren't.")


if __name__ == "__main__":
    main()
