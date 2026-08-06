"""Code graph -> interactive 3D HTML (3d-force-graph / Three.js WebGL).

How this differs from viz2d.py: the 2D view is for *reading* -- labels, filters,
a retrieval path you can follow. This one is for the *shape* of the whole thing:
orbit it, dive in, see which functions cluster around which tables.

Layout is precomputed in Python and shipped as fixed coordinates. Running the
force simulation in the browser looked fine at first and was the single biggest
time sink here -- see the notes in layout_3d() and below.

Design choices, each one paid for:
  - Node names come from hover and search, never from persistent labels. Those
    need three-spritetext, which wants a global THREE that 3d-force-graph does
    not expose (three is bundled inside it). The resulting `SpriteText is not
    defined` throws inside the nodeThreeObject callback and kills the entire
    render loop: blank canvas, nodes never even get coordinates. Table nodes are
    told apart by size and colour instead.
  - Links have no arrowheads. Thousands of cone geometries cost real frames and
    buy nothing; direction is read by clicking a node and seeing what lights up.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .query import load_graph
from .viz2d import COLORS

TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  body { margin: 0; background: #0f172a; font-family: -apple-system, sans-serif; }
  #graph { width: 100vw; height: 100vh; }
  #panel {
    position: absolute; top: 16px; left: 16px; z-index: 10;
    background: rgba(15,23,42,0.88); border: 1px solid #1e293b;
    border-radius: 10px; padding: 14px 16px; color: #e2e8f0;
    backdrop-filter: blur(8px); max-width: 260px;
  }
  #search {
    width: 100%; box-sizing: border-box; background: #1e293b; color: #e2e8f0;
    border: 1px solid #334155; border-radius: 6px; padding: 8px 10px;
    font-size: 13px; outline: none;
  }
  #search:focus { border-color: #34D399; }
  #hits { margin-top: 6px; max-height: 220px; overflow-y: auto; }
  .hit {
    padding: 6px 8px; font-size: 12px; cursor: pointer; border-radius: 5px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .hit:hover { background: #1e293b; }
  .hit small { color: #64748b; }
  #legend { margin-top: 14px; font-size: 12px; line-height: 1.9; }
  .sw { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 7px; }
  #stats { margin-top: 12px; font-size: 11px; color: #64748b; line-height: 1.6; }
  #tip { color: #64748b; font-size: 11px; margin-top: 10px; line-height: 1.5; }
</style>
</head>
<body>
<div id="panel">
  <input id="search" placeholder="search functions / types / tables" autocomplete="off">
  <div id="hits"></div>
  <div id="legend">__LEGEND__</div>
  <div id="stats">__STATS__</div>
  <div id="tip">Drag to orbit, scroll to zoom. Click a node to fly to it and light up what it touches.</div>
</div>
<div id="graph"></div>

<script src="https://cdn.jsdelivr.net/npm/3d-force-graph@1/dist/3d-force-graph.min.js"></script>
<script>
const DATA = __DATA__;

// Edges carry the meaning on a small graph and become visual mush on a large
// one, so their weight tracks node count rather than being a fixed guess.
const _N = DATA.nodes.length;
const _t = Math.min(1, Math.max(0, (_N - 60) / 1200));      // 0 at tiny, 1 at ~1.3k nodes
const LINK_OPACITY = 0.75 - 0.5 * _t;
const LINK_WIDTH   = 1.6 - 1.3 * _t;
const FOCUS_WIDTH  = LINK_WIDTH * 3;

// refresh() rebuilds every node and link object. On 1k+ nodes that freezes the
// renderer outright. The documented highlight pattern is to re-feed the
// accessors, repainting colours without touching geometry.
const repaint = () => Graph
  .nodeColor(Graph.nodeColor())
  .linkColor(Graph.linkColor())
  .linkWidth(Graph.linkWidth());

// Adjacency: on click, keep the node and everything it links to lit.
const adj = new Map();
DATA.links.forEach(l => {
  if (!adj.has(l.source)) adj.set(l.source, new Set());
  if (!adj.has(l.target)) adj.set(l.target, new Set());
  adj.get(l.source).add(l.target);
  adj.get(l.target).add(l.source);
});

// Point-cloud extent. Used to place the camera directly instead of calling
// zoomToFit(), which depends on render timing -- one frame too early and the
// camera ends up *inside* the cloud, opening onto a wall of spheres.
const _sp = a => Math.max(...a) - Math.min(...a);
const _R = Math.max(_sp(DATA.nodes.map(n => n.x)),
                    _sp(DATA.nodes.map(n => n.y)),
                    _sp(DATA.nodes.map(n => n.z)));

let focused = null;
const dim = c => c + '22';   // 8-digit hex alpha: dim everything unrelated

const Graph = ForceGraph3D()(document.getElementById('graph'))
  .graphData(DATA)
  .backgroundColor('#0f172a')
  .nodeLabel(n => `<div style="background:#0f172a;color:#e2e8f0;padding:6px 9px;
      border:1px solid #334155;border-radius:6px;font-size:12px">
      <b>${n.name}</b><br><span style="color:#94a3b8">${n.ntype}${n.file ? ', ' + n.file : ''}
      </span></div>`)
  .nodeVal(n => n.val)
  .nodeColor(n => (!focused || focused === n.id || adj.get(focused)?.has(n.id))
                  ? n.color : dim(n.color))
  .nodeOpacity(0.9)
  .linkColor(l => {
    const on = !focused || l.source.id === focused || l.target.id === focused;
    return on ? l.color : '#1e293b';
  })
  .linkWidth(l => (focused && (l.source.id === focused || l.target.id === focused))
                  ? FOCUS_WIDTH : LINK_WIDTH)
  .linkOpacity(LINK_OPACITY)
  .onNodeClick(n => {
    focused = focused === n.id ? null : n.id;
    repaint();
    if (focused) {
      const r = 1 + _R / 7 / Math.hypot(n.x, n.y, n.z);   // close in, but keep neighbours in frame
      Graph.cameraPosition({x: n.x * r, y: n.y * r, z: n.z * r}, n, 900);
    }
  })
  // Coordinates come precomputed from Python (nodes carry fx/fy/fz), so no
  // force simulation runs here. See layout_3d() for why.
  .cooldownTicks(0)
  .warmupTicks(0);

Graph.cameraPosition({x: 0, y: 0, z: _R * 1.15});


// ---- search ----
const search = document.getElementById('search'), hits = document.getElementById('hits');
search.addEventListener('input', () => {
  const q = search.value.trim().toLowerCase();
  hits.innerHTML = '';
  if (q.length < 2) return;
  DATA.nodes.filter(n => n.name.toLowerCase().includes(q)).slice(0, 12).forEach(n => {
    const d = document.createElement('div');
    d.className = 'hit';
    d.innerHTML = `<span class="sw" style="background:${n.color}"></span>${n.name}
                   <small>${n.file || n.ntype}</small>`;
    d.onclick = () => {
      focused = n.id; repaint();
      const r = 1 + _R / 7 / Math.hypot(n.x, n.y, n.z);
      Graph.cameraPosition({x: n.x * r, y: n.y * r, z: n.z * r}, n, 900);
    };
    hits.appendChild(d);
  });
});
</script>
</body>
</html>
"""


# spring_layout emits [-1,1]; scale it up so the cloud has room to fly through.
# Has to track node count: a value tuned on a 1.5k-node app leaves a 30-node
# example as a few specks scattered across an empty void. Cube root keeps the
# density roughly constant, since this is a volume.
SCALE_AT_1500 = 1600


def _scale(n: int) -> float:
    return max(260.0, SCALE_AT_1500 * (n / 1500) ** (1 / 3))


def layout_3d(G):
    """Precompute 3D coordinates.

    Why not let the browser do it: 3d-force-graph runs the simulation client-side
    and caps it with cooldownTime, which is **wall-clock** (15s by default). On a
    busy machine the layout gets cut off mid-flight, so the same file opens as a
    tight ball one run and a spread-out cloud the next -- extents differed by 10x
    in testing. Removing the cap just moves the problem: a few hundred ticks on
    1k+ nodes pins the main thread long enough for the tab to stop responding.
    Precomputing costs ~15s once, and the page then opens fast, looks identical
    every time, and burns no CPU.

    k is the ideal edge length. networkx defaults to 1/sqrt(n), which on a graph
    this size packs everything into a solid blob; an order of magnitude more lets
    the structure open up. The fixed seed is what makes runs reproducible.
    """
    import networkx as nx

    print(f"computing 3D layout ({G.number_of_nodes()} nodes)...", flush=True)
    pos = nx.spring_layout(G.to_undirected(), dim=3, k=8 / (G.number_of_nodes() ** 0.5),
                           iterations=90, seed=42)
    scale = _scale(G.number_of_nodes())
    return {n: [float(c) * scale for c in p] for n, p in pos.items()}


def render_3d(G, out_html, min_degree: int = 0, title: str = "Swift Code Graph") -> str:
    if min_degree > 0:
        G = G.subgraph([n for n in G.nodes if G.degree(n) >= min_degree]).copy()

    degs = dict(G.degree())
    dmax = max(degs.values()) if degs else 1
    pos = layout_3d(G)
    # Node volume scales with the cloud, or small graphs render as dust
    base_val = max(8.0, 10 * _scale(G.number_of_nodes()) / SCALE_AT_1500)

    nodes = []
    for nid, d in G.nodes(data=True):
        nt = d.get("ntype", "?")
        deg = degs.get(nid, 0)
        x, y, z = pos[nid]
        nodes.append({
            "id": nid,
            "name": d.get("label", nid),
            "ntype": nt,
            "file": Path(d["path"]).name if d.get("path") else "",
            # Enlarge tables: storage endpoints should read at a glance in 3D too
            "val": base_val * (3.0 if nt == "Table" else 0.1 + 1.2 * (deg / dmax)),
            "color": COLORS.get(nt, "#dddddd"),
            # fx/fy/fz pin the node: 3d-force-graph uses them as-is and solves nothing
            "x": x, "y": y, "z": z, "fx": x, "fy": y, "fz": z,
        })

    links = [{"source": s, "target": t,
              "color": ("#FB923C" if d.get("etype") == "accesses_table"
                        else "#34D399" if d.get("etype") == "calls"
                        else "#475569")}
             for s, t, d in G.edges(data=True)]

    counts = {}
    for n in nodes:
        counts[n["ntype"]] = counts.get(n["ntype"], 0) + 1
    legend = "".join(
        f'<div><span class="sw" style="background:{COLORS.get(t, "#ddd")}"></span>'
        f'{t} <span style="color:#64748b">{c}</span></div>'
        for t, c in sorted(counts.items(), key=lambda kv: -kv[1])
    )

    out = Path(out_html).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    # Placeholder substitution, not %-formatting: the template's CSS (50%) and
    # JS braces both collide with format specifiers.
    out.write_text(
        TEMPLATE
        .replace("__TITLE__", title)
        .replace("__LEGEND__", legend)
        .replace("__STATS__", f"{len(nodes)} nodes / {len(links)} edges")
        .replace("__DATA__", json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False))
    )
    print(f"3D graph -> {out}  ({len(nodes)} nodes / {len(links)} edges)")
    return str(out)


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Render a code graph as interactive 3D HTML.")
    ap.add_argument("graph", type=Path, nargs="?", default=Path("graph.json"))
    ap.add_argument("-o", "--out", type=Path, default=Path("graph_3d.html"))
    ap.add_argument("--min-degree", type=int, default=0,
                    help="drop nodes with degree below this, to surface the backbone")
    ap.add_argument("--title", default="Swift Code Graph")
    args = ap.parse_args()
    render_3d(load_graph(args.graph), args.out,
              min_degree=args.min_degree, title=args.title)


if __name__ == "__main__":
    main()
