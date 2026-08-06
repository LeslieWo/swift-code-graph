"""tree-sitter-swift compatibility shim plus small text helpers.

Node members in this binding are mostly methods (kind / child_count /
start_byte ...) where other bindings expose properties. _v() accepts either,
so callers never have to guess whether a trailing () belongs there.
"""
from tree_sitter_language_pack import get_parser

_PARSER = get_parser("swift")


def _v(x):
    return x() if callable(x) else x


def kind(n):
    # Node exposes this as .kind on some builds and .type on others.
    k = getattr(n, "kind", None)
    return _v(k) if k is not None else _v(n.type)



def sb(n):      return _v(n.start_byte)
def eb(n):      return _v(n.end_byte)
def ccount(n):  return _v(n.child_count)
def kids(n):    return [n.child(i) for i in range(ccount(n))]


def txt(src: bytes, n) -> str:
    return src[sb(n):eb(n)].decode("utf-8", "replace")


def line_of(src: bytes, n) -> int:
    return src[: sb(n)].count(b"\n") + 1


def child_of_kind(n, k):
    for c in kids(n):
        if kind(c) == k:
            return c
    return None


def parse(src: bytes):
    """Return the root node.

    tree-sitter changed what parse() accepts across versions -- older builds want
    str, newer ones insist on bytes -- so try bytes first and fall back. Either
    way the offsets that come back are utf-8 *byte* offsets, which is why every
    helper in this module slices bytes rather than the decoded string.
    """
    try:
        tree = _PARSER.parse(src)
    except TypeError:
        tree = _PARSER.parse(src.decode("utf-8"))
    return _v(tree.root_node)
