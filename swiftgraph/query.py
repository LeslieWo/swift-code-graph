"""Graph retrieval: question -> seed nodes -> edge traversal -> relevant subgraph.

This is the part a similarity retriever cannot do. Top-k over text chunks returns
passages that *look* related; walking calls / accesses_table / contains / conforms
returns the actual chain -- who calls what, and which table it ends up writing.

Seeds are located by symbol-name matching (camelCase and snake_case are split
into words). Deliberately not embeddings: the point is to show what structure
buys you, so the retrieval step stays boring and inspectable.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import networkx as nx

# Downstream (out-edges): what it calls, which table it writes, what it contains
DOWN_EDGES = {"calls", "accesses_table", "conforms", "contains"}
# Upstream (in-edges): who calls me, who contains me
UP_EDGES = {"calls", "contains"}


def load_graph(path: Path = Path("graph.json")) -> nx.DiGraph:
    data = json.loads(Path(path).read_text())
    return nx.node_link_graph(data, edges="links", directed=True)


def _words(s: str) -> set[str]:
    """camelCase / snake_case -> lowercase word set. saveUserPhoto -> {save, user, photo}."""
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    return {w for w in re.findall(r"[a-zA-Z]+", s.lower()) if len(w) > 1}


# Prefer concrete entities as seeds; files and modules are too coarse to anchor on
TYPE_BONUS = {"Function": 2, "Table": 3, "Type": 2, "Module": 0, "File": 0, "TypeRef": 0}


def find_seeds(G: nx.DiGraph, question: str, k: int = 5):
    qwords = _words(question)
    qlow = question.lower()
    scored = []
    for nid, d in G.nodes(data=True):
        label = d.get("label", "")
        if not label:
            continue
        score = 0.0
        if len(label) > 2 and re.search(rf"\b{re.escape(label.lower())}\b", qlow):  # whole name appears as a word: strong signal
            score += 6
        score += len(qwords & _words(label)) * 2        # partial word overlap
        if score > 0:
            score += TYPE_BONUS.get(d.get("ntype"), 0)
            scored.append((score, nid))
    scored.sort(reverse=True)
    return [nid for _, nid in scored[:k]]


def retrieve_subgraph(G: nx.DiGraph, seeds, max_hops: int = 3, max_nodes: int = 50) -> nx.DiGraph:
    """Type-aware traversal so a chain can cross from a type into its data endpoint.

    A Type seed has no calls edges of its own, so it first descends into its
    methods (and notes what it conforms to as context). Functions then walk calls
    in both directions plus accesses_table outward, which is what carries a path
    from a view all the way to the table it writes.
    """
    chosen = set(seeds)
    frontier = list(seeds)

    def add(node, into_frontier):
        if node not in chosen and len(chosen) < max_nodes:
            chosen.add(node)
            if into_frontier:
                frontier_next.append(node)

    for _ in range(max_hops):
        frontier_next: list = []
        for n in frontier:
            nt = G.nodes[n].get("ntype")
            if nt == "Type":
                for _, t, d in G.out_edges(n, data=True):
                    et = d.get("etype")
                    if et == "contains":
                        add(t, into_frontier=True)      # descend into methods, keep walking
                    elif et == "conforms":
                        add(t, into_frontier=False)      # parent types are context only
            else:  # Function / Table / etc
                for _, t, d in G.out_edges(n, data=True):
                    if d.get("etype") in ("calls", "accesses_table"):
                        add(t, into_frontier=True)
                for s, _, d in G.in_edges(n, data=True):
                    if d.get("etype") == "calls":
                        add(s, into_frontier=True)       # upstream callers
        frontier = frontier_next
        if not frontier:
            break
    return G.subgraph(chosen).copy()


def retrieve(G: nx.DiGraph, question: str, **kw):
    seeds = find_seeds(G, question, k=kw.pop("k", 5))
    sub = retrieve_subgraph(G, seeds, **kw)
    return seeds, sub


def describe_paths(G: nx.DiGraph, sub: nx.DiGraph, seeds) -> list[str]:
    """Flatten the most informative edges into readable path lines for an agent."""
    lines = []
    for s, t, d in sub.edges(data=True):
        et = d.get("etype")
        sl, tl = sub.nodes[s].get("label"), sub.nodes[t].get("label")
        if et == "calls":
            lines.append(f"{sl} --calls--> {tl}")
        elif et == "accesses_table":
            lines.append(f"{sl} --{d.get('direction','accesses')}--> (table) {tl}")
        elif et == "conforms":
            lines.append(f"{sl} --conforms--> {tl}")
        elif et == "contains" and sub.nodes[s].get("ntype") == "Type":
            lines.append(f"{sl} --contains--> {tl}")
    return lines


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Answer a codebase question by walking the graph.")
    ap.add_argument("question")
    ap.add_argument("-g", "--graph", type=Path, default=Path("graph.json"))
    ap.add_argument("-o", "--out", type=Path, default=Path("path.html"),
                    help="write the retrieved subgraph here (empty string to skip)")
    args = ap.parse_args()

    G = load_graph(args.graph)
    seeds, sub = retrieve(G, args.question)

    print(f"Q: {args.question}\n")
    print("seeds:")
    for s in seeds:
        print(f"  [{G.nodes[s].get('ntype')}] {G.nodes[s].get('label')}  ({s})")
    print(f"\nsubgraph: {sub.number_of_nodes()} nodes / {sub.number_of_edges()} edges")
    print("\nstructured paths (this is what an agent gets as context):")
    for line in describe_paths(G, sub, seeds):
        print(f"  {line}")

    if str(args.out):
        from .viz2d import render
        print(f"\nvisualisation -> {render(sub, seeds, args.out, args.question)}")


if __name__ == "__main__":
    main()
