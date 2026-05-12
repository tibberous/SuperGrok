"""
string_audit.py -- Find hardcoded strings that should be ENUMs,
constants, or localization keys. Flags string dict keys, magic
strings in comparisons, and repeated string literals.

Usage:
    python string_audit.py path/to/file.py
    python string_audit.py file.py --min-repeats 3
    python string_audit.py file.py --dict-keys
    python string_audit.py file.py --json
"""
import ast, sys, json, argparse, collections
from pathlib import Path


def audit(path, min_repeats=2, dict_keys_only=False):
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, filename=str(path))

    string_counts = collections.Counter()
    string_lines  = collections.defaultdict(list)
    dict_key_hits = []

    for node in ast.walk(tree):
        # Collect all string literals
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if len(val) >= 2 and not val.startswith(("#", "http",
                    "//", "/*", " ", "\n")):
                string_counts[val] += 1
                string_lines[val].append(
                    getattr(node, "lineno", "?"))

        # Find string dict keys: d["key"] = ...
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str):
                    dict_key_hits.append({
                        "key":  node.slice.value,
                        "line": getattr(node, "lineno", "?"),
                    })

    results = {
        "dict_string_keys": dict_key_hits,
        "repeated_strings": [],
    }

    for val, count in string_counts.most_common():
        if count >= min_repeats:
            results["repeated_strings"].append({
                "value":   val,
                "count":   count,
                "lines":   string_lines[val][:10],
            })

    return results


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--min-repeats", type=int, default=2)
    p.add_argument("--dict-keys", action="store_true",
                   help="Show only dict string key hits")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    results = audit(args.file, args.min_repeats, args.dict_keys)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    if not args.dict_keys:
        reps = results["repeated_strings"]
        print(f"Repeated strings (>={args.min_repeats}x): {len(reps)}")
        for r in reps[:40]:
            lines = ", ".join(f"L{n}" for n in r["lines"][:5])
            suffix = "..." if len(r["lines"]) > 5 else ""
            print(f"  {r['count']:>3}x  {repr(r['value'])[:60]}"
                  f"  [{lines}{suffix}]")
        print()

    keys = results["dict_string_keys"]
    print(f"String dict keys (should be ENUMs): {len(keys)}")
    for k in keys[:40]:
        print(f"  L{k['line']:>5}  d[{repr(k['key'])}]")


if __name__ == "__main__":
    main()
