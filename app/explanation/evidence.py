"""Evidence Construction (Phase 5, Stage 5B).

Gathers factual, structured evidence about the graph for a future
ExplanationEngine (Stage 5D) to turn into natural-language explanations.
This module never invents relationships, phrasing, or judgments — it
only reads what GraphEngine's public API already tells it, and
repackages that into a stable, explanation-ready shape.

ARCHITECTURAL RULE — enforced by this module's imports:
    GraphEngine (app.graph.engine) remains the sole source of truth.
    This module talks to it ONLY through its public methods (get_node,
    get_dependencies, get_dependents, impact_analysis, detect_cycles,
    shortest_path, connected_components, to_model). It never imports
    networkx and never reaches into app.graph.core.Graph or any
    app.graph.algorithms module directly.

This stage deliberately stops at structured evidence. It does not
generate explanation text, does not pick a fallback wording strategy,
and does not decide how "confident" a piece of prose should sound —
that belongs to Stage 5C (deterministic fallback) and Stage 5D
(ExplanationEngine).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.graph.engine import GraphEngine
from app.graph.exceptions import NodeNotFoundError
from app.models.graph import Edge


class ObservationKind(StrEnum):
    """What a single Observation is evidence of."""

    NODE_INFO = "node_info"
    DEPENDENCY = "dependency"
    DEPENDENT = "dependent"
    IMPACT_DIRECT = "impact_direct"
    IMPACT_TRANSITIVE = "impact_transitive"
    IMPACT_SUMMARY = "impact_summary"
    DIRECT_RELATIONSHIP = "direct_relationship"
    INDIRECT_PATH = "indirect_path"
    NO_RELATIONSHIP = "no_relationship"
    CYCLE = "cycle"
    CONNECTION = "connection"
    GRAPH_SUMMARY = "graph_summary"


class Observation(BaseModel):
    """A single structured, factual observation about the graph.

    `origin` / `confidence` / `basis` are populated ONLY for
    observations derived from a specific Edge, and are copied verbatim
    from Edge.metadata (see app/graph/inference.py) — never re-derived
    or reworded here. Observations with no underlying edge (dependency
    counts, graph summaries, ...) leave them None rather than guessing.
    """

    kind: ObservationKind
    subject_id: str | None = Field(default=None, description="The primary node id this observation concerns.")
    related_id: str | None = Field(default=None, description="A second node id, for relationship-style observations.")
    origin: str | None = Field(default=None, description='Edge.metadata["origin"], copied verbatim: "parsed" | "inferred".')
    confidence: str | None = Field(
        default=None, description='Edge.metadata["confidence"], copied verbatim: "high" | "heuristic".'
    )
    basis: str | None = Field(default=None, description='Edge.metadata["basis"], copied verbatim, when present.')
    detail: dict[str, Any] = Field(default_factory=dict, description="Observation-specific structured payload.")
    weight: float = Field(
        default=1.0,
        description=(
            "Deterministic relevance/certainty score used for ranking, NOT a probability. "
            "Directly-parsed facts score highest; heuristic inference and absence-of-evidence "
            "score lowest. See _WEIGHT_BY_ORIGIN_CONFIDENCE for the exact table."
        ),
    )


class EvidencePackage(BaseModel):
    """A complete, self-contained set of evidence for one explanation
    request — either about a single node, a pair of nodes, or the whole
    graph. `subject_ids` records which node id(s) the package concerns
    (empty for a whole-graph package)."""

    subject_ids: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)

    def ranked(self) -> list[Observation]:
        """Observations sorted most-salient-first (highest weight
        first). Stable sort — observations with equal weight keep their
        original relative order."""
        return sorted(self.observations, key=lambda observation: observation.weight, reverse=True)


# Deterministic weight lookup for edge-derived observations. A directly
# parsed edge is a plain fact (1.0). An inferred edge is only as good as
# the rule that produced it — "high" confidence inference (e.g. a
# Kubernetes label-selector match, which is exactly how Kubernetes itself
# routes traffic) is close to a fact; "heuristic" inference (e.g. image
# string correlation) is a weaker signal and scored accordingly. Anything
# unrecognized falls back to a neutral middle value rather than 1.0, so an
# unexpected origin/confidence combination is never mistaken for a fact.
_WEIGHT_BY_ORIGIN_CONFIDENCE: dict[tuple[str | None, str | None], float] = {
    ("parsed", None): 1.0,
    ("inferred", "high"): 0.8,
    ("inferred", "heuristic"): 0.5,
}
_DEFAULT_EDGE_WEIGHT = 0.6


def _require_known_node(engine: GraphEngine, node_id: str) -> None:
    """Raise NodeNotFoundError for an unknown node id.

    GraphEngine.get_node() returns None (rather than raising) for a
    missing node, since a lookup is expected to fail gracefully. Every
    evidence-gathering entry point in this module raises consistently
    instead, so callers (Stage 5D) get one uniform error contract
    regardless of which evidence function they called.
    """
    if engine.get_node(node_id) is None:
        raise NodeNotFoundError(node_id)


def _edge_observation(kind: ObservationKind, edge: Edge, subject_id: str, related_id: str) -> Observation:
    """Build an Observation from an Edge, copying its provenance
    metadata verbatim rather than re-deriving or rewording it."""
    origin = edge.metadata.get("origin")
    confidence = edge.metadata.get("confidence")
    basis = edge.metadata.get("basis")
    weight = _WEIGHT_BY_ORIGIN_CONFIDENCE.get((origin, confidence), _DEFAULT_EDGE_WEIGHT)
    return Observation(
        kind=kind,
        subject_id=subject_id,
        related_id=related_id,
        origin=origin,
        confidence=confidence,
        basis=basis,
        detail={"edge_type": edge.edge_type, "edge_id": edge.id},
        weight=weight,
    )


def _edges_between(engine: GraphEngine, source_id: str, target_id: str) -> list[Edge]:
    """Every edge directly from `source_id` to `target_id`, in that
    direction. Reads app.models.graph.Edge objects from
    GraphEngine.to_model() — the engine's own public export — rather
    than touching Graph or networkx directly."""
    return [edge for edge in engine.to_model().edges if edge.source == source_id and edge.target == target_id]


# --- 1. Node/component information ------------------------------------------


def gather_node_info(engine: GraphEngine, node_id: str) -> Observation:
    """What `node_id` is: its name, refined node_type, technology, and
    passthrough metadata."""
    node = engine.get_node(node_id)
    if node is None:
        raise NodeNotFoundError(node_id)
    return Observation(
        kind=ObservationKind.NODE_INFO,
        subject_id=node.id,
        detail={
            "name": node.name,
            "node_type": node.node_type,
            "technology": node.technology,
            "metadata": node.metadata,
        },
        weight=1.0,
    )


# --- 2/3. Dependencies and dependents ----------------------------------------


def gather_dependency_evidence(engine: GraphEngine, node_id: str) -> list[Observation]:
    """Everything `node_id` (transitively) depends on. NodeNotFoundError
    propagates unchanged from GraphEngine.get_dependencies()."""
    dependencies = engine.get_dependencies(node_id)
    return [
        Observation(
            kind=ObservationKind.DEPENDENCY,
            subject_id=node_id,
            related_id=dependency.id,
            detail={"name": dependency.name, "node_type": dependency.node_type},
            weight=0.9,
        )
        for dependency in dependencies
    ]


def gather_dependent_evidence(engine: GraphEngine, node_id: str) -> list[Observation]:
    """Everything that (transitively) depends on `node_id`.
    NodeNotFoundError propagates unchanged from GraphEngine.get_dependents()."""
    dependents = engine.get_dependents(node_id)
    return [
        Observation(
            kind=ObservationKind.DEPENDENT,
            subject_id=node_id,
            related_id=dependent.id,
            detail={"name": dependent.name, "node_type": dependent.node_type},
            weight=0.9,
        )
        for dependent in dependents
    ]


# --- 4. Impact analysis (direct + transitive) --------------------------------


def gather_impact_evidence(engine: GraphEngine, node_id: str) -> list[Observation]:
    """What would be affected, directly or transitively, by a change to
    `node_id` — one observation per affected node, plus a summary
    observation carrying the totals GraphEngine already computed
    (impact_by_type). NodeNotFoundError propagates unchanged from
    GraphEngine.impact_analysis()."""
    report = engine.impact_analysis(node_id)

    observations = [
        Observation(
            kind=ObservationKind.IMPACT_DIRECT,
            subject_id=node_id,
            related_id=dependent.id,
            detail={"name": dependent.name, "node_type": dependent.node_type},
            weight=0.9,
        )
        for dependent in report.direct_dependents
    ]
    observations.extend(
        Observation(
            kind=ObservationKind.IMPACT_TRANSITIVE,
            subject_id=node_id,
            related_id=dependent.id,
            detail={"name": dependent.name, "node_type": dependent.node_type},
            weight=0.6,
        )
        for dependent in report.transitive_dependents
    )
    observations.append(
        Observation(
            kind=ObservationKind.IMPACT_SUMMARY,
            subject_id=node_id,
            detail={
                "total_impact_count": report.total_impact_count,
                "impact_by_type": report.impact_by_type,
            },
            weight=0.8,
        )
    )
    return observations


# --- 5/6. Relationship evidence (direct, provenance, indirect, none) --------


def gather_relationship_evidence(engine: GraphEngine, source_id: str, target_id: str) -> list[Observation]:
    """Evidence for the relationship (if any) from `source_id` to
    `target_id`, in that direction:

    - One DIRECT_RELATIONSHIP observation per direct edge, if any exist
      (there can be more than one — e.g. a parsed depends_on plus a
      separately inferred connects_to between the same pair). Each
      carries the edge's origin/confidence/basis verbatim.
    - Otherwise, one INDIRECT_PATH observation if GraphEngine.shortest_path
      finds a path (any edge type) between the two nodes.
    - Otherwise, one NO_RELATIONSHIP observation.

    Raises NodeNotFoundError if either id doesn't exist in the graph.
    """
    _require_known_node(engine, source_id)
    _require_known_node(engine, target_id)

    direct_edges = _edges_between(engine, source_id, target_id)
    if direct_edges:
        return [
            _edge_observation(ObservationKind.DIRECT_RELATIONSHIP, edge, source_id, target_id)
            for edge in direct_edges
        ]

    path = engine.shortest_path(source_id, target_id)
    if path is not None and len(path) > 1:
        return [
            Observation(
                kind=ObservationKind.INDIRECT_PATH,
                subject_id=source_id,
                related_id=target_id,
                detail={"path": [node.id for node in path], "hop_count": len(path) - 1},
                weight=0.7,
            )
        ]

    return [
        Observation(
            kind=ObservationKind.NO_RELATIONSHIP,
            subject_id=source_id,
            related_id=target_id,
            weight=0.3,
        )
    ]


# --- 7. Cycle evidence --------------------------------------------------------


def gather_cycle_evidence(engine: GraphEngine) -> list[Observation]:
    """Every dependency cycle currently in the graph. Empty list if the
    graph is acyclic — GraphEngine.detect_cycles() already scopes this
    to the dependency subgraph (depends_on/uses/contains/mounts)."""
    return [
        Observation(
            kind=ObservationKind.CYCLE,
            detail={"node_ids": [node.id for node in cycle], "length": len(cycle)},
            weight=0.9,
        )
        for cycle in engine.detect_cycles()
    ]


# --- 8. Connections / related nodes ------------------------------------------


def gather_connection_evidence(engine: GraphEngine, node_id: str) -> list[Observation]:
    """Every edge touching `node_id`, in either direction and of any
    edge type (dependency or lateral) — the full neighborhood, not just
    the dependency subgraph traversal.py restricts itself to. Raises
    NodeNotFoundError if `node_id` doesn't exist."""
    _require_known_node(engine, node_id)

    observations = []
    for edge in engine.to_model().edges:
        if edge.source == node_id:
            observations.append(_edge_observation(ObservationKind.CONNECTION, edge, node_id, edge.target))
        elif edge.target == node_id:
            observations.append(_edge_observation(ObservationKind.CONNECTION, edge, node_id, edge.source))
    return observations


# --- 9. Whole-graph observations ---------------------------------------------


def gather_graph_observations(engine: GraphEngine) -> list[Observation]:
    """Graph-level facts useful for an architecture-level explanation:
    size, cyclicity, isolation, and a type/technology breakdown — plus
    every individual cycle (see gather_cycle_evidence). Safe to call on
    an empty graph (0 nodes)."""
    model = engine.to_model()

    observations = [
        Observation(
            kind=ObservationKind.GRAPH_SUMMARY,
            detail={
                "node_count": model.metadata.get("node_count", 0),
                "edge_count": model.metadata.get("edge_count", 0),
                "has_cycles": model.metadata.get("has_cycles", False),
            },
            weight=1.0,
        )
    ]

    components = engine.connected_components()
    isolated_node_ids = [group[0].id for group in components if len(group) == 1]
    observations.append(
        Observation(
            kind=ObservationKind.GRAPH_SUMMARY,
            detail={
                "connected_component_count": len(components),
                "isolated_node_ids": isolated_node_ids,
            },
            weight=0.8,
        )
    )

    node_type_counts: dict[str, int] = {}
    technology_counts: dict[str, int] = {}
    for node in model.nodes:
        node_type_counts[node.node_type] = node_type_counts.get(node.node_type, 0) + 1
        technology_counts[node.technology] = technology_counts.get(node.technology, 0) + 1
    observations.append(
        Observation(
            kind=ObservationKind.GRAPH_SUMMARY,
            detail={"node_type_counts": node_type_counts, "technology_counts": technology_counts},
            weight=0.6,
        )
    )

    observations.extend(gather_cycle_evidence(engine))
    return observations


# --- Top-level EvidencePackage builders ---------------------------------------
# These are the entry points Stage 5D's ExplanationEngine is expected to
# call — one per ExplanationRequest shape defined in app/models/explanation.py.


def build_node_evidence(engine: GraphEngine, node_id: str) -> EvidencePackage:
    """Full evidence package for explaining a single node: what it is,
    what it depends on, what depends on it, its impact, and its
    connections. Raises NodeNotFoundError if `node_id` doesn't exist."""
    observations = [gather_node_info(engine, node_id)]
    observations.extend(gather_dependency_evidence(engine, node_id))
    observations.extend(gather_dependent_evidence(engine, node_id))
    observations.extend(gather_impact_evidence(engine, node_id))
    observations.extend(gather_connection_evidence(engine, node_id))
    return EvidencePackage(subject_ids=[node_id], observations=observations)


def build_relationship_evidence(engine: GraphEngine, source_id: str, target_id: str) -> EvidencePackage:
    """Full evidence package for explaining the relationship between two
    specific nodes. Raises NodeNotFoundError if either id doesn't exist."""
    observations = gather_relationship_evidence(engine, source_id, target_id)
    return EvidencePackage(subject_ids=[source_id, target_id], observations=observations)


def build_graph_evidence(engine: GraphEngine) -> EvidencePackage:
    """Full evidence package for a whole-graph, architecture-level
    explanation. Safe to call on an empty graph."""
    return EvidencePackage(subject_ids=[], observations=gather_graph_observations(engine))
