"""Swift source tree -> code knowledge graph.

Nodes: File / Type / Function / Table / Module / TypeRef
Edges: contains / imports / calls / conforms / accesses_table

Approach:
  - Structure (types, functions, imports, call sites) comes from the tree-sitter AST.
  - Persistence targets come from regex rules (see DATA_SOURCES). Real apps reach
    storage through several layers at once -- a typed client here, a raw REST call
    there -- and no single AST shape covers them, so this stays a heuristic pass.
  - `calls` is resolved in two passes: collect (caller, callee-simple-name) while
    walking, then match against a global index of function names.

Usage:
    python -m swiftgraph.build_graph <path-to-swift-repo> [-o graph.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

from . import tsswift as ts

# Directories that are never first-party source. Dependency and build output
# would otherwise drown the real structure -- one checkouts/ folder can easily
# outweigh the app itself.
SKIP_DIRS = {"Pods", "Carthage", "DerivedData", "SourcePackages", "checkouts",
             "build", ".build", "vendor", "Generated", "fastlane"}

TYPE_DECLS = {
    "class_declaration": None,        # class/struct/actor share this kind; first keyword disambiguates
    "protocol_declaration": "protocol",
    "enum_declaration": "enum",
    "extension_declaration": "extension",
}
FUNC_DECLS = {"function_declaration", "init_declaration"}

# Each rule maps a storage API to the table/collection/entity it touches.
# `pattern` must expose the name as group 1. Add your own -- this list is only
# what shows up most often in iOS codebases.
DATA_SOURCES: list[tuple[str, re.Pattern]] = [
    ("supabase-client", re.compile(r'\.from\(\s*"([^"]+)"')),
    ("supabase-rest",   re.compile(r'/rest/v1/([A-Za-z_][A-Za-z0-9_]*)')),
    ("firestore",       re.compile(r'\.collection\(\s*"([^"]+)"')),
    ("coredata",        re.compile(r'entity(?:ForName|Name):\s*"([^"]+)"')),
    ("coredata-fetch",  re.compile(r'NSFetchRequest<\s*([A-Za-z_][A-Za-z0-9_]*)')),
    ("realm",           re.compile(r'realm\.objects\(\s*([A-Za-z_][A-Za-z0-9_]*)\.self')),
    # SQL rules require the keyword and the table name inside the *same* string
    # literal ([^"]* never crosses a quote). A bare /\bFROM\s+(\w+)/i looks
    # equivalent and is not: it happily reads English prose, and on a real
    # codebase it turned comments like "copied from Marco" and "read from disk"
    # into tables named Marco and disk.
    ("sql-select",      re.compile(r'"[^"]*\bSELECT\b[^"]*\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)),
    ("sql-insert",      re.compile(r'"[^"]*\bINSERT\s+(?:or\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)),
    # UPDATE must be followed by SET, or every "Update your credentials" string
    # in the UI copy donates a table called `your`.
    ("sql-update",      re.compile(r'"[^"]*\bUPDATE\s+([A-Za-z_][A-Za-z0-9_]*)\s+SET\b', re.I)),
    ("sql-delete",      re.compile(r'"[^"]*\bDELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)', re.I)),
    ("sql-create",      re.compile(r'"[^"]*\bCREATE\s+TABLE\s+(?:if\s+not\s+exists\s+)?([A-Za-z_][A-Za-z0-9_]*)', re.I)),
]

# Words that are never a table, however well they match. Swift codebases build
# SQL with string interpolation -- `CREATE TABLE IF NOT EXISTS \(tableName)` --
# and when the real name is a \(...) the regex backtracks and captures the
# keyword instead. Those tables cannot be recovered statically; the honest
# outcome is to drop them rather than invent a table called `IF`.
SQL_NOISE = {"if", "not", "set", "exists", "into", "from", "table", "select",
             "where", "values", "on", "conflict", "do", "or", "and", "null",
             "temporary", "temp", "unique", "index"}

# Tells a write from a read. Deliberately loose: on a data-flow graph, a false
# "writes" is far less misleading than quietly calling a mutation a read.
RE_WRITE_HINT = re.compile(
    r'\b(insert|upsert|update|delete|save|create|set)\b|"(POST|PATCH|PUT|DELETE)"', re.I)


def type_name(src, n):
    c = ts.child_of_kind(n, "type_identifier") or ts.child_of_kind(n, "user_type")
    return ts.txt(src, c) if c else "<anon>"


def type_subkind(src, n):
    if ts.kind(n) in ("protocol_declaration", "enum_declaration", "extension_declaration"):
        return ts.kind(n).split("_")[0]
    head = ts.txt(src, n).lstrip().split(None, 1)
    first = head[0] if head else ""
    return first if first in ("class", "struct", "actor") else "class"


def func_name(src, n):
    if ts.kind(n) == "init_declaration":
        return "init"
    c = ts.child_of_kind(n, "simple_identifier")
    return ts.txt(src, c) if c else "<anon>"


def callee_name(src, n):
    """First child of a call_expression is the callee: a bare name, or a.b.foo -> foo."""
    ks = ts.kids(n)
    if not ks:
        return None
    callee = ks[0]
    if ts.kind(callee) == "simple_identifier":
        return ts.txt(src, callee)
    if ts.kind(callee) == "navigation_expression":
        suf = ts.child_of_kind(callee, "navigation_suffix")
        if suf:
            sid = ts.child_of_kind(suf, "simple_identifier")
            if sid:
                return ts.txt(src, sid)
    return None


def conforms_list(src, n):
    """Heuristic: in `class X: A, B {`, capitalised identifiers after the colon."""
    head = ts.txt(src, n).split("{", 1)[0]
    if ":" not in head:
        return []
    after = head.split(":", 1)[1].split(" where ")[0]
    return re.findall(r'\b[A-Z][A-Za-z0-9_]*', after)


def find_tables(body: str) -> set[str]:
    return {m for _, pat in DATA_SOURCES for m in pat.findall(body)
            if m.lower() not in SQL_NOISE}


def _parser_versions() -> dict[str, str]:
    """Record what actually parsed this graph.

    The Swift grammar evolves, and a different grammar sees a different number of
    call sites in identical source -- 8,803 vs 8,763 on the same app across two
    environments here. Pinning would force a version on everyone; stamping it
    means a graph that disagrees with yours can at least be explained.
    """
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for pkg in ("tree-sitter", "tree-sitter-language-pack"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            pass
    return out


def build(repo: Path):
    G = nx.DiGraph()
    G.graph["parser"] = _parser_versions()
    files = [f for f in sorted(repo.rglob("*.swift"))
             if "/." not in str(f) and not SKIP_DIRS & set(f.relative_to(repo).parts)]
    func_index = defaultdict(list)     # simple name -> [function node id]
    pending_calls = []                 # (caller_func_id, callee simple name)

    for f in files:
        rel = str(f.relative_to(repo))
        file_id = f"file:{rel}"
        G.add_node(file_id, label=f.name, ntype="File", path=rel)
        src = f.read_bytes()
        root = ts.parse(src)

        def walk(n, cur_type, cur_func):
            k = ts.kind(n)
            nt, nf = cur_type, cur_func

            if k in TYPE_DECLS:
                tname = type_name(src, n)
                tid = f"type:{rel}::{tname}"
                G.add_node(tid, label=tname, ntype="Type",
                           subkind=type_subkind(src, n), path=rel, line=ts.line_of(src, n))
                G.add_edge(file_id, tid, etype="contains")
                for parent in conforms_list(src, n):
                    pid = f"typeref:{parent}"
                    if not G.has_node(pid):
                        G.add_node(pid, label=parent, ntype="TypeRef")
                    G.add_edge(tid, pid, etype="conforms")
                nt = tid

            elif k in FUNC_DECLS:
                fname = func_name(src, n)
                line = ts.line_of(src, n)
                owner = cur_type or file_id
                prefix = (cur_type.split("::")[-1] + ".") if cur_type else ""
                fid = f"func:{rel}::{prefix}{fname}#{line}"
                G.add_node(fid, label=fname, ntype="Function", path=rel, line=line)
                G.add_edge(owner, fid, etype="contains")
                func_index[fname].append(fid)
                nf = fid
                body = ts.txt(src, n)
                for tbl in find_tables(body):
                    tbid = f"table:{tbl}"
                    if not G.has_node(tbid):
                        G.add_node(tbid, label=tbl, ntype="Table")
                    direction = "writes" if RE_WRITE_HINT.search(body) else "reads"
                    G.add_edge(fid, tbid, etype="accesses_table", direction=direction)

            elif k == "import_declaration":
                raw = ts.txt(src, n).replace("import", "", 1).strip()
                mod = raw.split()[0] if raw else ""
                if mod:
                    mid = f"module:{mod}"
                    if not G.has_node(mid):
                        G.add_node(mid, label=mod, ntype="Module")
                    G.add_edge(file_id, mid, etype="imports")

            elif k == "call_expression" and cur_func:
                cn = callee_name(src, n)
                if cn:
                    pending_calls.append((cur_func, cn))

            for c in ts.kids(n):
                walk(c, nt, nf)

        walk(root, None, None)

    # ---- resolve calls against the global function-name index ----
    resolved = unresolved = 0
    for caller, cn in pending_calls:
        targets = func_index.get(cn, [])
        if targets:
            for t in targets[:3]:          # ambiguous simple name: link up to 3 candidates
                if t != caller:
                    G.add_edge(caller, t, etype="calls")
            resolved += 1
        else:
            unresolved += 1

    return G, dict(files=len(files), call_sites=len(pending_calls),
                   calls_resolved=resolved, calls_unresolved=unresolved)


def main():
    ap = argparse.ArgumentParser(description="Build a code knowledge graph from a Swift repo.")
    ap.add_argument("repo", type=Path, help="path to the Swift source tree")
    ap.add_argument("-o", "--out", type=Path, default=Path("graph.json"))
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        ap.error(f"not a directory: {repo}")

    G, stats = build(repo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(nx.node_link_data(G, edges="links"),
                                   ensure_ascii=False, indent=2))

    ntypes = Counter(d["ntype"] for _, d in G.nodes(data=True))
    etypes = Counter(d["etype"] for *_, d in G.edges(data=True))

    print(f"=== {repo.name} -> {args.out} ===")
    print(f"files {stats['files']} | nodes {G.number_of_nodes()} | edges {G.number_of_edges()}")
    print("\nnodes:")
    for t, c in ntypes.most_common():
        print(f"  {c:5d}  {t}")
    print("edges:")
    for t, c in etypes.most_common():
        print(f"  {c:5d}  {t}")
    print(f"\ncalls: {stats['calls_resolved']} resolved / "
          f"{stats['calls_unresolved']} unresolved ({stats['call_sites']} call sites)")
    if G.graph.get("parser"):
        print("parsed by: " + ", ".join(f"{k} {v}" for k, v in G.graph["parser"].items()))

    tables = sorted(d["label"] for _, d in G.nodes(data=True) if d["ntype"] == "Table")
    if tables:
        print(f"\ntables ({len(tables)}): {', '.join(tables)}")
    else:
        print("\nNo tables found. If this app does persist data, add a rule to "
              "DATA_SOURCES in swiftgraph/build_graph.py.")


if __name__ == "__main__":
    main()
