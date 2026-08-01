"""Connected components (full graph, undirected — isolation detection)
and impact analysis (dependency subgraph only — "what depends on this").
"""

import networkx as nx

from app.graph.algorithms.traversal import dependency_subgraph


def connected_components(g: nx.MultiDiGraph) -> list[list[str]]:
    """Weakly connected components over the FULL graph, every edge type.
    "Isolated" here means "unrelated to anything else at all" — a node
    joined to the rest of the graph only by a connects_to edge is still
    meaningfully connected, so this deliberately doesn't use the
    dependency-only subgraph the way cycle detection does."""
    return [sorted(group) for group in nx.weakly_connected_components(g)]


def impact_analysis(g: nx.MultiDiGraph, node_id: str) -> tuple[list[str], list[str]]:
    """(direct_dependent_ids, transitive_dependent_ids) for `node_id`,
    computed over the dependency subgraph — impact analysis is exactly
    "who depends on this", the same semantics as get_dependents(), just
    split into 1-hop vs. further-out for a richer report."""
    subgraph = dependency_subgraph(g)
    if node_id not in subgraph:
        return [], []
    all_dependents = nx.ancestors(subgraph, node_id)
    direct = set(subgraph.predecessors(node_id)) & all_dependents
    transitive = all_dependents - direct
    return sorted(direct), sorted(transitive)
