"""Does the graph actually track changes to the source?

The failure this guards against is subtle: a graph that grows when you add code
but never shrinks when you remove it still looks correct on every screenshot.
You only notice months later, when it reports a table nothing writes any more.

So each test edits the bundled example, rebuilds, and asserts on the delta --
including the reverse direction, which is the one that rots quietly.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from swiftgraph.build_graph import build

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "DemoApp"

NEW_FUNC = '''
    func archiveExpired(before cutoff: Date) async throws {
        try await client.from("archive").delete().lt("created_at", cutoff)
    }
'''


def summarise(G):
    nodes = {n: d.get("ntype") for n, d in G.nodes(data=True)}
    edges = {(s, t, d.get("etype"), d.get("direction", "")) for s, t, d in G.edges(data=True)}
    return nodes, edges


def labels_of(G, ntype):
    return {d["label"] for _, d in G.nodes(data=True) if d.get("ntype") == ntype}


@pytest.fixture
def app(tmp_path):
    """A throwaway copy of the example app, so a failed test cannot corrupt it."""
    dst = tmp_path / "App"
    shutil.copytree(EXAMPLE, dst)
    return dst


def test_baseline(app):
    G, stats = build(app)
    assert stats["files"] == 5
    # Two storage APIs on purpose: a typed client and a hand-rolled REST call.
    assert labels_of(G, "Table") == {"notes", "tags", "sync_log", "events"}


def test_added_code_appears_and_removal_restores(app):
    """Add -> rebuild -> revert -> rebuild, comparing against the baseline both ways."""
    before = build(app)[0]
    base_nodes, base_edges = summarise(before)

    store = app / "Services" / "NoteStore.swift"
    original = store.read_text()
    idx = original.rstrip().rfind("}")
    store.write_text(original[:idx] + NEW_FUNC + original[idx:])

    after = build(app)[0]
    after_nodes, after_edges = summarise(after)

    # --- the change is visible ---
    assert "archive" in labels_of(after, "Table"), "new table never reached the graph"
    assert "archiveExpired" in labels_of(after, "Function")
    new_edges = after_edges - base_edges
    assert any(et == "accesses_table" and direction == "writes"
               and after_nodes.get(t) == "Table"
               for _, t, et, direction in new_edges), \
        "a delete() call should have produced a writes edge"
    assert set(base_nodes) < set(after_nodes), "adding code must only add nodes"

    # --- and reverting takes it all back out ---
    store.write_text(original)
    reverted_nodes, reverted_edges = summarise(build(app)[0])

    assert reverted_nodes == base_nodes, "nodes did not return to baseline after revert"
    assert reverted_edges == base_edges, "edges did not return to baseline after revert"


def test_rebuild_is_deterministic(app):
    """Same input, same graph. Node ids embed line numbers, so drift shows up here."""
    a_nodes, a_edges = summarise(build(app)[0])
    b_nodes, b_edges = summarise(build(app)[0])
    assert a_nodes == b_nodes
    assert a_edges == b_edges


def test_moving_a_function_moves_its_node(app):
    """Node ids carry file and line, so relocating code should relocate the node.

    Guards the opposite failure from the one above: a graph that quietly keeps
    stale locations still resolves, but every 'where is this' answer is wrong.
    """
    store = app / "Services" / "NoteStore.swift"
    text = store.read_text()
    store.write_text("// padding\n// padding\n// padding\n" + text)

    G = build(app)[0]
    moved = [n for n, d in G.nodes(data=True)
             if d.get("label") == "saveNote" and d.get("ntype") == "Function"]
    assert moved, "saveNote disappeared after shifting the file"
    assert G.nodes[moved[0]]["line"] > 3, "line number did not follow the code"
