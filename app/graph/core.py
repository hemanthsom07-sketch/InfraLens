"""Graph: the internal, indexed working representation.

Wraps networkx.MultiDiGraph — a MultiDiGraph specifically (not a plain
DiGraph) because two components can have more than one edge between
them (e.g. a directly-parsed depends_on edge plus a separately-inferred
connects_to edge for the same pair), and a plain DiGraph would silently
collapse the second one into the first.

This is one of exactly five files in the whole project allowed to import
networkx (this one + the four modules under algorithms/). Nothing in
builder.py, engine.py, inference.py, refinement.py, graph_service.py, or
anywhere outside app/graph/ ever does.
"""

import networkx as nx

from app.models.graph import Edge, Node


class Graph:
    """A directed, indexed graph of Node/Edge objects. Construction only
    happens through GraphBuilder; querying happens through GraphEngine —
    this class itself is a thin, dumb container plus lookup helpers."""

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()

    def add_node(self, node: Node) -> None:
        """Idempotent: adding a node whose id already exists overwrites
        its data (last-write-wins) rather than creating a duplicate —
        this is networkx's native add_node() behavior, and it's exactly
        what's needed since Component.id is already globally unique by
        construction (Phase 3), so a repeat id here would only ever be a
        genuine update, never an accidental collision."""
        self._g.add_node(node.id, obj=node)

    def add_edge(self, edge: Edge) -> None:
        """Skips (rather than crashes on) an edge whose source or target
        isn't a known node — defends against a dangling relationship
        reference from a parser bug, consistent with how the rest of the
        project treats malformed input as "contributes nothing", not a
        fatal error. GraphBuilder also checks this before calling in, but
        checking here too means Graph is safe to use directly (e.g. in a
        test) without relying on the caller to have validated first."""
        if edge.source not in self._g or edge.target not in self._g:
            return
        self._g.add_edge(edge.source, edge.target, key=edge.id, obj=edge)

    def get_node(self, node_id: str) -> Node | None:
        if node_id not in self._g:
            return None
        return self._g.nodes[node_id]["obj"]

    def has_node(self, node_id: str) -> bool:
        return node_id in self._g

    @property
    def nodes(self) -> list[Node]:
        return [data["obj"] for _, data in self._g.nodes(data=True)]

    @property
    def edges(self) -> list[Edge]:
        return [data["obj"] for _, _, data in self._g.edges(data=True)]

    @property
    def networkx_graph(self) -> nx.MultiDiGraph:
        """Internal escape hatch for app/graph/algorithms/* only. Nothing
        outside this package should ever call this."""
        return self._g
