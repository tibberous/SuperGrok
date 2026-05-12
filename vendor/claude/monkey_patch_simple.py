#!/usr/bin/env python3
"""
Monkey patch detector — AST + grep analysis for runtime rebinding smells.

Usage:
    python tools/monkey_patch.py trio.py --pretty
    python tools/monkey_patch.py trio.py > patches.json

Output groups by confidence: HIGH → MEDIUM → LOW.
Review all HIGH and MEDIUM manually. LOW is noisy — skim it.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

GREP_PATTERNS = [
    r"\bsetattr\(",
    r"\bdelattr\(",
    r"\bglobals\(\)\s*\[",
    r"\b[A-Za-z_][A-Za-z0-9_]*\.__dict__\s*\[",
    r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*",
    r"\b(prev|legacy|patch|final|shim|override|alias|hook)\b",
]

LOW_RISK_TARGETS = {"self", "cls", "app", "record", "debugger", "owner", "result"}
CALLABLEISH_NAMES = {
    "run", "exec", "execute", "execute_sql", "save", "load", "refresh", "apply", "build",
    "send", "open", "close", "read", "write", "patch", "hook", "start", "stop", "connect",
}
HOOK_ATTRS = {"excepthook", "f_trace", "displayhook", "breakpointhook", "path_hooks", "meta_path"}
PATCHY_NAME_TOKENS = ("legacy", "patch", "shim", "prev", "final", "override", "alias", "hook")


@dataclass
class Cluster:
    file: str
    function: str
    lineno: int
    category: str
    confidence: str
    target: str
    detail: str
    snippet: str


class Analyzer(ast.NodeVisitor):
    def __init__(self, path: Path, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.class_defs: dict[str, int] = {}
        self.class_methods: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.top_funcs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.scope: list[str] = []
        self.const_stack: list[dict[str, str]] = [{}]
        self.clusters: list[Cluster] = []
        self.grep_hits = self.run_grep_patterns()

    def run_grep_patterns(self) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for pat in GREP_PATTERNS:
            try:
                proc = subprocess.run(
                    ["grep", "-nE", pat, str(self.path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except FileNotFoundError:
                continue
            for line in proc.stdout.splitlines():
                if not line.strip() or ":" not in line:
                    continue
                lineno_str, text = line.split(":", 1)
                try:
                    lineno = int(lineno_str)
                except ValueError:
                    continue
                hits.append({"lineno": lineno, "pattern": pat, "text": text.strip()})
        dedup = {(h["lineno"], h["text"]): h for h in hits}
        return sorted(dedup.values(), key=lambda x: (x["lineno"], x["text"]))

    def current_function(self) -> str:
        return "/".join(self.scope) if self.scope else "<module>"

    def line_text(self, lineno: int) -> str:
        return self.lines[lineno - 1].rstrip() if 1 <= lineno <= len(self.lines) else ""

    def add(self, lineno: int, category: str, confidence: str, target: str, detail: str) -> None:
        self.clusters.append(
            Cluster(
                str(self.path),
                self.current_function(),
                lineno,
                category,
                confidence,
                target,
                detail,
                self.line_text(lineno),
            )
        )

    def current_consts(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for scope_consts in self.const_stack:
            merged.update(scope_consts)
        return merged

    def eval_simple_string(self, expr: ast.AST | None) -> str | None:
        if expr is None:
            return None
        consts = self.current_consts()
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        if isinstance(expr, ast.Name):
            return consts.get(expr.id)
        if isinstance(expr, ast.JoinedStr):
            parts: list[str] = []
            for value in expr.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    inner = self.eval_simple_string(value.value)
                    if inner is None:
                        return None
                    parts.append(inner)
                else:
                    return None
            return "".join(parts)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left = self.eval_simple_string(expr.left)
            right = self.eval_simple_string(expr.right)
            if left is not None and right is not None:
                return left + right
        return None

    def store_simple_string_targets(self, targets: list[ast.expr], value: str) -> None:
        for target in targets:
            if isinstance(target, ast.Name):
                self.const_stack[-1][target.id] = value
            elif isinstance(target, (ast.Tuple, ast.List)):
                continue

    def is_callableish_expr(self, expr: ast.AST) -> bool:
        if isinstance(expr, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        if isinstance(expr, ast.Name):
            return True
        if isinstance(expr, ast.Attribute):
            return True
        if isinstance(expr, ast.Call):
            return False
        return False

    def classify_attr_assignment(self, base_name: str | None, attr_name: str, value: ast.AST, lineno: int, target_text: str) -> None:
        value_text = ast.unparse(value) if hasattr(ast, "unparse") else type(value).__name__
        if base_name in self.class_defs:
            detail = f"assigns to attribute on known class {base_name}"
            if self.is_callableish_expr(value):
                detail += " using a callable-ish value"
            self.add(lineno, "class_attribute_assignment", "high", target_text, detail)
            return
        if base_name in ("sys", "threading") and attr_name in HOOK_ATTRS:
            self.add(lineno, "hook_assignment", "high", target_text, f"assigns runtime hook {base_name}.{attr_name}")
            return
        if base_name == "frame" and attr_name in HOOK_ATTRS:
            self.add(lineno, "hook_assignment", "medium", target_text, f"assigns frame tracing hook {base_name}.{attr_name}")
            return
        if base_name and base_name not in LOW_RISK_TARGETS:
            confidence = "medium" if attr_name in CALLABLEISH_NAMES or value_text.isidentifier() or self.is_callableish_expr(value) else "low"
            self.add(lineno, "attribute_assignment", confidence, target_text, f"assigns to attribute on non-self object {base_name}")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_defs[node.name] = node.lineno
        self.scope.append(node.name)
        self.const_stack.append({})
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.class_methods[child.name].append({
                    "class": node.name,
                    "lineno": child.lineno,
                    "end_lineno": getattr(child, "end_lineno", child.lineno),
                })
        self.generic_visit(node)
        self.const_stack.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not self.scope:
            self.top_funcs[node.name].append({
                "lineno": node.lineno,
                "end_lineno": getattr(node, "end_lineno", node.lineno),
            })
            lower = node.name.lower()
            if any(tok in lower for tok in PATCHY_NAME_TOKENS):
                self.add(node.lineno, "legacy_or_patch_named_function", "low", node.name, "top-level function name contains patch/shim/legacy/final token")
            real_body = [
                stmt for stmt in node.body
                if not isinstance(stmt, ast.Expr) or not isinstance(getattr(stmt, "value", None), ast.Constant)
            ]
            if len(real_body) == 1 and isinstance(real_body[0], ast.Return) and isinstance(real_body[0].value, ast.Call):
                callee = ast.unparse(real_body[0].value.func) if hasattr(ast, "unparse") else type(real_body[0].value.func).__name__
                self.add(node.lineno, "thin_wrapper", "low", node.name, f"single-return wrapper forwarding to {callee}")
        self.scope.append(node.name)
        self.const_stack.append({})
        self.generic_visit(node)
        self.const_stack.pop()
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        simple = self.eval_simple_string(node.value)
        if simple is not None:
            self.store_simple_string_targets(node.targets, simple)
        for target in node.targets:
            if isinstance(target, ast.Attribute):
                target_text = ast.unparse(target) if hasattr(ast, "unparse") else self.line_text(node.lineno)
                base_name = target.value.id if isinstance(target.value, ast.Name) else None
                self.classify_attr_assignment(base_name, target.attr, node.value, node.lineno, target_text)
            elif isinstance(target, ast.Name) and isinstance(node.value, ast.Attribute):
                base_name = node.value.value.id if isinstance(node.value.value, ast.Name) else None
                if base_name in self.class_defs:
                    self.add(node.lineno, "captured_class_attribute", "medium", target.id, f"captures attribute from known class {base_name}")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            simple = self.eval_simple_string(node.value)
            if simple is not None:
                self.const_stack[-1][node.target.id] = simple
        if isinstance(node.target, ast.Attribute) and node.value is not None:
            target_text = ast.unparse(node.target) if hasattr(ast, "unparse") else self.line_text(node.lineno)
            base_name = node.target.value.id if isinstance(node.target.value, ast.Name) else None
            self.classify_attr_assignment(base_name, node.target.attr, node.value, node.lineno, target_text)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"} and len(node.args) >= 2:
            op = node.func.id
            target_text = ast.unparse(node.args[0]) if hasattr(ast, "unparse") else "target"
            attr_name = self.eval_simple_string(node.args[1])
            attr_text = attr_name if attr_name is not None else (ast.unparse(node.args[1]) if hasattr(ast, "unparse") else "attr")
            base_name = node.args[0].id if isinstance(node.args[0], ast.Name) else None
            confidence = "low"
            detail = f"{op} on {target_text}.{attr_text}"
            if base_name in self.class_defs:
                confidence = "high"
                detail = f"{op} on known class {base_name}"
            elif base_name not in LOW_RISK_TARGETS:
                if attr_name in HOOK_ATTRS:
                    confidence = "high"
                    detail = f"{op} installs hook on {base_name}.{attr_name}"
                elif attr_name in CALLABLEISH_NAMES:
                    confidence = "high"
                    detail = f"{op} on non-self callable-like attribute {base_name}.{attr_name}"
                elif base_name:
                    confidence = "medium"
                    detail = f"{op} on non-self object {base_name}"
            self.add(node.lineno, op, confidence, f"{target_text}.{attr_text}", detail)
        elif isinstance(node.func, ast.Name) and node.func.id == "globals" and not node.args and not node.keywords:
            self.add(node.lineno, "globals_access", "low", "globals()", "raw globals() access near startup/runtime patch logic")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "globals":
            self.add(node.lineno, "globals_subscript", "medium", ast.unparse(node) if hasattr(ast, "unparse") else "globals()[...]", "subscript against globals() may support dynamic rebinding")
        elif isinstance(node.value, ast.Attribute) and node.value.attr == "__dict__":
            self.add(node.lineno, "dunder_dict_subscript", "medium", ast.unparse(node) if hasattr(ast, "unparse") else "obj.__dict__[...]", "direct __dict__ subscript may support dynamic rebinding")
        self.generic_visit(node)

    def finalize(self) -> None:
        for name, methods in sorted(self.class_methods.items()):
            if name in self.top_funcs:
                for top in self.top_funcs[name]:
                    self.add(top["lineno"], "shadow_name", "medium", name, f"top-level function duplicates class method name used by {len(methods)} class method(s)")
        self.clusters.sort(key=lambda c: (c.lineno, c.category, c.target))


def analyze_file(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source, filename=str(path))
    analyzer = Analyzer(path, source)
    analyzer.visit(tree)
    analyzer.finalize()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cluster in analyzer.clusters:
        grouped[cluster.confidence].append(asdict(cluster))
    return {
        "file": str(path),
        "grep_hit_count": len(analyzer.grep_hits),
        "grep_hits": analyzer.grep_hits,
        "cluster_count": len(analyzer.clusters),
        "clusters": [asdict(cluster) for cluster in analyzer.clusters],
        "by_confidence": {k: v for k, v in grouped.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Over-detect monkey patches and rebinding smells for manual review.")
    parser.add_argument("files", nargs="+", help="Python files to inspect")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable summary")
    args = parser.parse_args()

    results = [analyze_file(Path(file_path)) for file_path in args.files]
    if args.pretty:
        for result in results:
            print(f"FILE: {result['file']}")
            print(f"  grep hits: {result['grep_hit_count']}")
            print(f"  clusters:  {result['cluster_count']}")
            for level in ("high", "medium", "low"):
                rows = result.get("by_confidence", {}).get(level, [])
                if not rows:
                    continue
                print(f"  {level.upper()}: {len(rows)}")
                for row in rows:
                    print(f"    L{row['lineno']:>5} {row['category']:<28} {row['target']} :: {row['detail']}")
            print()
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
