"""
recursion_finder.py -- Find direct and indirect recursion cycles.

Detects:
  - Direct:   X -> X
  - Indirect: X -> Y -> X,  X -> Y -> Z -> N -> X, etc.

Usage:
    python recursion_finder.py path/to/file.py
    python recursion_finder.py file.py --json
    python recursion_finder.py file.py --max-depth 10
"""
import ast, sys, json, argparse
from pathlib import Path
from collections import defaultdict


def build_call_graph(path):
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src, filename=str(path))

    # Map: function_name -> set of function names it calls
    calls = defaultdict(set)
    defined = {}  # name -> lineno

    class GraphBuilder(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_FunctionDef(self, node):
            defined[node.name] = node.lineno
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if not self.scope:
                self.generic_visit(node)
                return
            caller = self.scope[-1]
            if isinstance(node.func, ast.Name):
                calls[caller].add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                # Only treat self.method()/cls.method() as project-level calls.
                # Calls like super().closeEvent(...), file.close(), page.setUrl(),
                # and connection.close() share method names with our functions but
                # are not recursion in this file.
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in {"self", "cls"}:
                    calls[caller].add(node.func.attr)
            self.generic_visit(node)

    GraphBuilder().visit(tree)
    return defined, calls


def find_cycles(calls, defined, max_depth):
    """Find cycles with Tarjan SCC instead of brute-force path explosion."""
    graph = {name: {callee for callee in calls.get(name, set()) if callee in defined} for name in defined}
    cycles = []

    for name, callees in graph.items():
        if name in callees:
            cycles.append([name, name])

    index = 0
    stack = []
    on_stack = set()
    indices = {}
    lowlinks = {}

    def strongconnect(node):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for callee in graph.get(node, set()):
            if callee not in indices:
                strongconnect(callee)
                lowlinks[node] = min(lowlinks[node], lowlinks[callee])
            elif callee in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[callee])

        if lowlinks[node] == indices[node]:
            component = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            if len(component) > 1:
                ordered = sorted(component)
                cycles.append(ordered + [ordered[0]])

    for node in sorted(graph):
        if node not in indices:
            strongconnect(node)

    seen = set()
    unique = []
    for cycle in cycles:
        chain = cycle[:-1]
        rotations = [tuple(chain[i:] + chain[:i]) for i in range(len(chain))]
        key = min(rotations)
        if key not in seen:
            seen.add(key)
            unique.append(cycle)
    return sorted(unique, key=lambda c: (len(c), c[0]))


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.add_argument("--max-depth", type=int, default=8,
                   help="Max chain length to search (default 8)")
    args = p.parse_args()

    defined, calls = build_call_graph(args.file)
    cycles = find_cycles(calls, defined, args.max_depth)

    results = [
        {
            "chain": cycle,
            "length": len(cycle) - 1,
            "start_line": defined.get(cycle[0]),
            "type": "direct" if len(cycle) == 2 else "indirect",
        }
        for cycle in cycles
    ]

    if args.json:
        print(json.dumps(results, indent=2))
        return

    if not results:
        print(f"No recursion found in {args.file}")
        return

    print(f"Recursion cycles in {args.file}: {len(results)}")
    print()
    for r in results:
        arrow = " -> ".join(r["chain"])
        tag = f"[{r['type']}]"
        print(f"  {tag:<10} L{r['start_line']:<5} {arrow}")
    print()


if __name__ == "__main__":
    main()
