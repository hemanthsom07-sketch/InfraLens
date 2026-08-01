"""Shortest-path queries — operate on the FULL graph (every edge type),
not just dependency edges. A request (or an attacker, per the Phase 6
attack-surface use case in the architecture doc) can travel through any
relationship, not only depends_on/uses/contains/mounts, so restricting
this to the dependency subgraph the way traversal/cycles do would give a
misleading answer.
"""

import networkx as nx


def shortest_path(g: nx.MultiDiGraph, source_id: str, target_id: str) -> list[str] | None:
    """The shortest id path from source to target, or None if no path
    exists. Callers are expected to have already validated both ids exist
    (GraphEngine does, via NodeNotFoundError) — this function trusts that
    contract rather than re-checking, since it has exactly one caller.
    """
    try:
        return nx.shortest_path(g, source_id, target_id)
    except nx.NetworkXNoPath:
        return None
