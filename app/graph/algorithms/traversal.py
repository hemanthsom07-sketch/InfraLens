"""Dependency traversal: what a node (transitively) depends on, and what
(transitively) depends on it.

Deliberately scoped to depends_on/uses/contains/mounts edges only —
connects_to is a peer/lateral relationship ("these two things can talk to
each other"), not a "needs this to function" relationship, so it's
excluded here. dependency_subgraph() is the shared filter every other
module in this package that cares about dependency semantics (cycles.py,
components.py's impact_analysis) reuses, so the edge-type list only
lives in one place.
"""

import networkx as nx

_DEPENDENCY_EDGE_TYPES = frozenset({"depends_on", "uses", "contains", "mounts"})


def dependency_subgraph(g: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """A view (not a copy) of `g` restricted to dependency-type edges."""

    def _keep_edge(u: str, v: str, key: str) -> bool:
        return g.edges[u, v, key]["obj"].edge_type in _DEPENDENCY_EDGE_TYPES

    return nx.subgraph_view(g, filter_edge=_keep_edge)


def get_dependencies(g: nx.MultiDiGraph, node_id: str) -> list[str]:
    """Every node id that `node_id` (transitively) depends on."""
    subgraph = dependency_subgraph(g)
    if node_id not in subgraph:
        return []
    return sorted(nx.descendants(subgraph, node_id))


def get_dependents(g: nx.MultiDiGraph, node_id: str) -> list[str]:
    """Every node id that (transitively) depends on `node_id`."""
    subgraph = dependency_subgraph(g)
    if node_id not in subgraph:
        return []
    return sorted(nx.ancestors(subgraph, node_id))
