"""Code graph -> interactive 2D HTML (pyvis / vis-network).

Two entry points:
  render(sub, seeds, out)   a retrieval subgraph; seeds get a red rim, data-flow
                            edges are orange so the storage endpoint stands out.
  render_full(G, out)       whole-graph overview, node size by degree, with a
                            type filter and a search box.

Two pyvis traps this works around:
  1. It copies lib/ next to the **current working directory**, not next to the
     output HTML. Run from the repo root while writing into out/ and every asset
     404s. Fixed by chdir-ing to the output directory before writing.
  2. Too much repulsion makes fit() settle near scale 0.17, and vis-network
     silently stops drawing labels at that zoom -- the page opens as a field of
     unlabelled dots and looks broken. Repulsion has to stay modest, and tighter
     still as the node count grows.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from html import escape, unescape
from pathlib import Path

from pyvis.network import Network

COLORS = {
    "File": "#9CA3AF", "Type": "#60A5FA", "Function": "#34D399",
    "Table": "#FB923C", "Module": "#D1D5DB", "TypeRef": "#C4B5FD",
}


@contextmanager
def _cwd(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


_INJECT_JS = """
                  // Stop the physics engine once the layout settles. pyvis has no
                  // switch for this, and on a 1k+ node graph the simulation pins a
                  // CPU core forever, making pan and zoom feel gluey. Once the
                  // layout is set, continuing to simulate buys nothing.
                  network.once("stabilizationIterationsDone", function () {
                      network.setOptions({physics: {enabled: false}});
                  });

                  // Recenter on the searched node. Stock pyvis only dims the
                  // others, which on a graph this size tells you nothing about
                  // where the highlighted one actually is.
                  var _selectNode = window.selectNode;
                  window.selectNode = function (nodes) {
                      var r = _selectNode(nodes);
                      if (nodes && nodes.length) {
                          network.focus(nodes[0],
                              {scale: 0.9, animation: {duration: 400}});
                      }
                      return r;
                  };

                  return network;"""


# pyvis writes the search dropdown as <option value="ID">ID</option>, and IDs look
# like "func:App/A/B.swift::Type.name#123". A thousand of those is unusable, so we
# rewrite only the display text; the value stays the ID and selection still works.
_RE_OPTION = re.compile(r'<option value="([^"]*)">\1</option>')


def _write(net: Network, out_html, freeze: bool = False,
           option_text: dict[str, str] | None = None) -> str:
    """Write from inside the output directory so pyvis's lib/ lands beside the HTML."""
    out = Path(out_html).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with _cwd(out.parent):
        net.write_html(out.name, notebook=False, open_browser=False)

    html = out.read_text()
    if freeze:
        marker = "\n                  return network;"
        assert marker in html, "pyvis template changed: JS injection point not found"
        html = html.replace(marker, "\n" + _INJECT_JS, 1)
    if option_text:
        # One pass for all of them; per-node str.replace is O(nodes x filesize)
        def sub(m):
            nid = unescape(m.group(1))
            return f'<option value="{m.group(1)}">{escape(option_text.get(nid, nid))}</option>'
        html = _RE_OPTION.sub(sub, html)
    out.write_text(html)
    return str(out)


def render(sub, seeds, out_html, question: str = "") -> str:
    """Render a retrieval subgraph. Seeds get a red rim; accesses_table edges go orange."""
    seeds = set(seeds)
    net = Network(height="100vh", width="100%", directed=True,
                  bgcolor="#0f172a", font_color="#e2e8f0")
    # Only a few dozen nodes here; keep repulsion low or the layout spreads out
    # far enough that labels vanish (see trap 2 in the module docstring).
    net.barnes_hut(spring_length=110, gravity=-3000, central_gravity=0.6)

    for nid, d in sub.nodes(data=True):
        nt = d.get("ntype", "?")
        is_seed = nid in seeds
        net.add_node(
            nid,
            label=d.get("label", nid),
            title=f"{d.get('label', nid)}\n{nt}",
            color={"background": COLORS.get(nt, "#dddddd"),
                   "border": "#ef4444" if is_seed else COLORS.get(nt, "#dddddd")},
            borderWidth=4 if is_seed else 1,
            size=26 if is_seed else 14,
            font={"size": 16, "color": "#e2e8f0"},
            shape="dot",
        )

    for s, t, d in sub.edges(data=True):
        et = d.get("etype", "")
        lab = d.get("direction", et) if et == "accesses_table" else et
        color = ("#FB923C" if et == "accesses_table"
                 else "#34D399" if et == "calls"
                 else "#64748b")
        net.add_edge(s, t, label=lab, title=lab, color=color, arrows="to")

    return _write(net, out_html)


def render_full(G, out_html, min_degree: int = 0) -> str:
    """Whole-graph overview. Higher degree means a bigger node, so hubs are obvious.

    min_degree > 0 drops low-degree nodes to let the backbone surface.
    """
    if min_degree > 0:
        keep = [n for n in G.nodes if G.degree(n) >= min_degree]
        G = G.subgraph(keep).copy()

    # The select/filter menus eat ~250px; without subtracting it the canvas
    # overflows the viewport and the bottom of the graph is cut off.
    net = Network(height="calc(100vh - 250px)", width="100%", directed=True,
                  bgcolor="#0f172a", font_color="#e2e8f0",
                  select_menu=True, filter_menu=True)

    net.set_options(json.dumps({
        # group drives filter_menu, but it also makes vis-network override
        # node.color with its own palette -- so colours must be declared here.
        "groups": {t: {"color": {"background": c, "border": c}}
                   for t, c in COLORS.items()},
        # 1k+ nodes: forceAtlas2 with tightened repulsion.
        "physics": {
            "solver": "forceAtlas2Based",
            "forceAtlas2Based": {
                "gravitationalConstant": -45, "centralGravity": 0.015,
                "springLength": 55, "springConstant": 0.07,
                "damping": 0.5, "avoidOverlap": 0.1,
            },
            "stabilization": {"enabled": True, "iterations": 300, "fit": True},
            "minVelocity": 0.9,
            "timestep": 0.4,
        },
        "edges": {
            "color": {"inherit": False},
            "smooth": False,
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.4}},
            "width": 0.4,
        },
        "interaction": {"hideEdgesOnDrag": True, "tooltipDelay": 120},
        "nodes": {"font": {"size": 14, "color": "#e2e8f0"}},
    }))

    degs = dict(G.degree())
    dmax = max(degs.values()) if degs else 1
    option_text = {}

    for nid, d in G.nodes(data=True):
        nt = d.get("ntype", "?")
        deg = degs.get(nid, 0)
        label = d.get("label", nid)
        where = Path(d["path"]).name if d.get("path") else ""
        # Always enlarge Table nodes: the storage endpoints are the whole point.
        size = 34 if nt == "Table" else 8 + 26 * (deg / dmax) ** 0.5
        c = COLORS.get(nt, "#dddddd")
        net.add_node(
            nid,
            label=label,
            title="\n".join(x for x in (label, f"{nt}, degree {deg}", where) if x),
            color={"background": c, "border": c},
            size=size,
            shape="dot",
            group=nt,          # filter_menu filters on this field
        )
        option_text[nid] = f"{label}   ({nt}{', ' + where if where else ''})"

    for s, t, d in G.edges(data=True):
        et = d.get("etype", "")
        color = ("#FB923C" if et == "accesses_table"
                 else "#34D399" if et == "calls"
                 else "#475569")
        net.add_edge(s, t, color=color, title=et, arrows="to")

    out = _write(net, out_html, freeze=True, option_text=option_text)
    print(f"2D graph -> {out}  ({G.number_of_nodes()} nodes / {G.number_of_edges()} edges)")
    return out


def main():
    import argparse
    from .query import load_graph

    ap = argparse.ArgumentParser(description="Render a code graph as interactive 2D HTML.")
    ap.add_argument("graph", type=Path, nargs="?", default=Path("graph.json"))
    ap.add_argument("-o", "--out", type=Path, default=Path("graph_2d.html"))
    ap.add_argument("--min-degree", type=int, default=0,
                    help="drop nodes with degree below this, to surface the backbone")
    args = ap.parse_args()
    render_full(load_graph(args.graph), args.out, min_degree=args.min_degree)


if __name__ == "__main__":
    main()
