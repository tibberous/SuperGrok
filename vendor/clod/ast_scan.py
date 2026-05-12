"""
AST scanner — prints all classes, methods, and functions with
line counts. Flag anything over --threshold lines (default 50).

Usage:
    python ast_scan.py path/to/file.py
    python ast_scan.py path/to/file.py --threshold 30
    python ast_scan.py path/to/file.py --json
"""
import ast, sys, json, argparse
from pathlib import Path


def scan(path, threshold=50):
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, filename=str(path))

    parent_by_id = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_by_id[id(child)] = parent

    class_parent: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent = parent_by_id.get(id(node))
        while parent is not None:
            if isinstance(parent, ast.ClassDef):
                class_parent[id(node)] = parent.name
                break
            parent = parent_by_id.get(id(parent))

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start)
        count = end - start + 1
        kind = "class" if isinstance(node, ast.ClassDef) else "def"
        results.append({
            "kind": kind,
            "name": node.name,
            "parent": class_parent.get(id(node), ""),
            "line": start,
            "lines": count,
            "flag": count > threshold,
        })

    results.sort(key=lambda r: r["line"])
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--threshold", type=int, default=50)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    results = scan(args.file, args.threshold)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    flagged = [r for r in results if r["flag"]]
    print(f"File: {args.file}")
    print(f"Total symbols: {len(results)}  |  "
          f"Over {args.threshold} lines: {len(flagged)}")
    print()

    for r in results:
        marker = "  *** LONG ***" if r["flag"] else ""
        parent = f"{r['parent']}." if r["parent"] else ""
        print(f"  L{r['line']:>5}  {r['lines']:>4} lines  "
              f"{r['kind']} {parent}{r['name']}{marker}")

    if flagged:
        print()
        print(f"--- Flagged (>{args.threshold} lines) ---")
        for r in flagged:
            parent = f"{r['parent']}." if r["parent"] else ""
            print(f"  L{r['line']:>5}  {r['lines']:>4} lines  "
                  f"{r['kind']} {parent}{r['name']}")


if __name__ == "__main__":
    main()
