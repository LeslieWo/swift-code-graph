# swift-code-graph

Turn a Swift codebase into a knowledge graph you can query, filter, and fly through.

It parses your `.swift` files with tree-sitter, resolves the call graph, and — this
is the part other tools skip — **recovers the tables your code actually touches**, so
a path like this becomes something you can see and traverse:

```
NoteEditorView.save --calls--> NoteStore.saveNote --writes--> (table) notes
```

## Why this exists

If you write Swift, the usual code-graph tools are not available to you.
[Understand](https://scitools.com/supported-languages) covers Ada, Fortran, and
Objective-C but not Swift. [FalkorDB's code-graph](https://docs.falkordb.com/genai-tools/code-graph.html)
supports Python, Java, and C#. Sourcetrail is archived. The gap is real and it is
where iOS developers happen to live.

The second thing: most code graphs stop at symbols. They will tell you `A` calls
`B`, but not that `B` is the function that writes your `users` table. For an app,
the question you usually have is *"what touches this data?"* — so tables,
collections, and entities are first-class nodes here, with `reads` / `writes`
edges pointing at them.

## Install

```bash
git clone https://github.com/<you>/swift-code-graph
cd swift-code-graph
uv pip install -e .          # or: pip install -e .
```

## Use

```bash
# 1. build the graph
swiftgraph-build ~/code/MyApp -o graph.json

# 2. look at it
swiftgraph-3d  graph.json -o graph_3d.html     # orbit the whole thing
swiftgraph-2d  graph.json -o graph_2d.html     # labels, filters, search
swiftgraph-2d  graph.json --min-degree 3       # drop the long tail, keep the backbone

# 3. ask it something
swiftgraph-query "what does the editor screen write to the database?"
```

Try it on the bundled example first:

```bash
swiftgraph-build examples/DemoApp -o graph.json && swiftgraph-3d graph.json
```

**2D or 3D?** The 3D view is for the shape of the thing — orbit it, dive in, see
which functions cluster around which tables. The 2D view is for reading: labels,
per-type filters, and a retrieval path you can follow step by step. Both read the
same `graph.json`.

In both views labels appear as you zoom in, the way Obsidian's graph behaves.
Use the search box to jump to a symbol; everything that is not a neighbour dims out.

## What ends up in the graph

| Node | From |
|---|---|
| `File` | every non-vendored `.swift` file |
| `Type` | class, struct, actor, enum, protocol, extension |
| `Function` | funcs and initialisers, attributed to their owning type |
| `Table` | tables / collections / entities, via `DATA_SOURCES` rules |
| `Module` | `import` targets |
| `TypeRef` | external supertypes and protocols (`Codable`, `ObservableObject`, …) |

| Edge | Meaning |
|---|---|
| `contains` | file → type → member |
| `calls` | resolved call site |
| `accesses_table` | carries `direction: reads \| writes` |
| `conforms` | inheritance and protocol conformance |
| `imports` | file → module |

### Teaching it about your storage layer

Table detection is a list of regex rules in `swiftgraph/build_graph.py`. Supabase
(typed client and raw REST), Firestore, Core Data, Realm, and raw SQL ship by
default. Adding yours is one line:

```python
DATA_SOURCES = [
    ...,
    ("my-orm", re.compile(r'MyORM\.table\(\s*"([^"]+)"')),
]
```

Group 1 is the table name. If a run reports no tables, this list is what to edit.

Why regex and not the AST: real apps reach storage through several layers at once
— a typed client in one file, a hand-rolled `URLSession` call in another. There is
no single AST shape that covers them, and a graph that silently missed half the
data layer would be worse than one that admits it is heuristic.

## Known limitations

Stated plainly, because a graph you can't calibrate is a graph you shouldn't trust:

- **`calls` resolution is by simple name.** A call to `save()` links to every
  in-repo `save` (up to 3 candidates). Most unresolved call sites are stdlib and
  SDK calls that correctly have no node. SwiftUI's `body` is the worst offender
  for false links, since every view declares one.
- **No property-level data flow.** `reads`/`writes` between functions and
  properties are not extracted yet.
- **`conforms` is a first-line heuristic**, not a full inheritance-clause parse.
- **Table direction is a guess.** Presence of `insert|upsert|update|delete|save|
  create|set` in the function body marks the edge `writes`. Deliberately loose:
  on a data-flow graph, a false "writes" misleads less than a mutation quietly
  labelled a read.
- Test fixtures and stub files land in the graph unless their directory is in
  `SKIP_DIRS`.

## Notes for anyone building something similar

Most of the work here was not the parser. It was making 1.5k nodes render without
lying to you or melting a laptop:

- **pyvis copies `lib/` next to the current working directory**, not next to the
  output HTML. Run from the repo root while writing into a subdirectory and every
  asset 404s.
- **vis-network silently stops drawing labels below a certain zoom.** Too much
  repulsion makes `fit()` settle around scale 0.17, and the page opens as a field
  of unlabelled dots that looks like broken text rendering. It isn't.
- **Passing `group=` to pyvis overrides your `color=`** with vis-network's own
  palette. Colours have to be declared under `options.groups`.
- **Neither engine stops on its own.** vis-network pins a CPU core forever;
  3d-force-graph's `refresh()` rebuilds every geometry and freezes the tab.
- **3d-force-graph's `cooldownTime` is wall-clock (15s).** On a busy machine the
  layout is cut off mid-flight, so the same file opens as a tight ball one run and
  a spread cloud the next — extents differed by 10x in testing. Removing the cap
  just moves the problem: a few hundred ticks on 1.5k nodes pins the main thread
  until the tab stops responding. Hence layout is precomputed in Python and shipped
  as fixed `fx/fy/fz`. Costs ~15s once; the page then opens fast and looks identical
  every time.
- **`three-spritetext` needs a global `THREE`** that 3d-force-graph doesn't expose.
  The `SpriteText is not defined` throws inside a node callback and kills the whole
  render loop — blank canvas, nodes without coordinates, no obvious cause.
- **`zoomToFit()` depends on render timing.** One frame early and the camera ends
  up *inside* the point cloud.

## Stack

Python 3.11+, tree-sitter-swift (via `tree-sitter-language-pack`), networkx,
pyvis for 2D, 3d-force-graph for 3D. No build step, no server, no database — the
output is a single self-contained HTML file.

## License

MIT
