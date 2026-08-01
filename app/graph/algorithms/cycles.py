"""Cycle detection and topological sort — both scoped to the dependency
subgraph (see traversal.py), since a depends_on cycle is the class of
cycle that represents a genuine infrastructure bug (Compose/Terraform
literally can't start or apply it) — not just two components that happen
to reference each other laterally via connects_to.
"""

import networkx as nx

from app.graph.algorithms.traversal import dependency_subgraph


def detect_cycles(g: nx.MultiDiGraph) -> list[list[str]]:
    """Every simple cycle in the dependency subgraph, each as an ordered
    list of node ids. Empty list if the graph is acyclic."""
    subgraph = dependency_subgraph(g)
    return list(nx.simple_cycles(subgraph))


def topological_order(g: nx.MultiDiGraph) -> list[str] | None:
    """A valid dependency order (id list), or None if the dependency
    subgraph contains a cycle — topological order is only defined on a DAG."""
    subgraph = dependency_subgraph(g)
    try:
        return list(nx.topological_sort(subgraph))
    except nx.NetworkXUnfeasible:
        return None
