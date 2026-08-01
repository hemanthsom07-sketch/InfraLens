"""Pydantic wire-format models for the graph — what actually gets
serialized to JSON in API responses.

Kept deliberately separate from the internal, NetworkX-backed working
representation in app/graph/ (see app/graph/core.py), which is indexed
for traversal rather than optimized for serialization. GraphEngine.to_model()
is the bridge between the two — see architecture doc §2.1.
"""

from typing import Any

from pydantic import BaseModel, Field


class Node(BaseModel):
    """A single graph node — one per IKM Component, mapped 1:1."""

    id: str = Field(..., description="Same id as the source Component — already globally unique.")
    name: str
    node_type: str = Field(
        ..., description="Component.type, refined where a type-refinement rule applies (see app/graph/refinement.py)."
    )
    technology: str = Field(..., description="Source technology, e.g. 'docker', 'terraform', 'kubernetes'.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Passed through from Component.metadata unchanged.")


class Edge(BaseModel):
    """A directed edge between two node ids."""

    id: str = Field(..., description="Deterministically derived, e.g. 'source--edge_type-->target'.")
    source: str = Field(..., description="Source Node.id.")
    target: str = Field(..., description="Target Node.id.")
    edge_type: str = Field(..., description="e.g. 'depends_on', 'connects_to', 'uses', 'contains', 'mounts'.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            'Always includes "origin": "parsed" | "inferred". Inferred edges additionally '
            'carry "confidence" ("high" | "heuristic") and "basis" (why it was inferred).'
        ),
    )


class GraphModel(BaseModel):
    """The full graph, ready for an API response or frontend consumption."""

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Precomputed graph-level facts, e.g. node_count, edge_count, has_cycles.",
    )


class ImpactReport(BaseModel):
    """Structured result of GraphEngine.impact_analysis(node_id) — what
    would be affected, directly or transitively, by a change to one node."""

    target: Node
    direct_dependents: list[Node] = Field(default_factory=list, description="1 hop away — depend on target directly.")
    transitive_dependents: list[Node] = Field(default_factory=list, description="2+ hops away.")
    total_impact_count: int = Field(..., description="len(direct_dependents) + len(transitive_dependents).")
    impact_by_type: dict[str, int] = Field(default_factory=dict, description='e.g. {"service": 3, "database": 1}.')
