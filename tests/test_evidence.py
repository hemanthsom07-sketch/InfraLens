"""Stage 5B: tests for app/explanation/evidence.py.

Builds a small, hand-crafted InfrastructureModel (rather than writing
files to disk and parsing them) so evidence gathering is tested against
a graph whose shape — parsed edges, both confidence levels of inferred
edges, a transitive dependency chain, a cycle, and an isolated node —
is fully known up front.

Two of the three Phase 4 inference rules are deliberately triggered here
(Kubernetes Service->Deployment via label selector = confidence "high";
cross-technology image correlation = confidence "heuristic") specifically
so Stage 5B's provenance-preservation behavior can be checked against
both confidence levels, not just parsed edges.
"""

import pytest

from app.explanation.evidence import (
    ObservationKind,
    build_graph_evidence,
    build_node_evidence,
    build_relationship_evidence,
    gather_connection_evidence,
    gather_cycle_evidence,
    gather_dependency_evidence,
    gather_dependent_evidence,
    gather_graph_observations,
    gather_impact_evidence,
    gather_node_info,
    gather_relationship_evidence,
)
from app.graph.engine import GraphEngine
from app.graph.exceptions import NodeNotFoundError
from app.models.ikm import Component, InfrastructureModel, Relationship


# --- fixtures ----------------------------------------------------------------


def _main_engine() -> GraphEngine:
    """A small multi-technology graph:

    backend2 --depends_on--> backend --depends_on--> db
    k8s-svc  --connects_to--> k8s-deploy   (inferred, confidence=high)
    backend  --connects_to--> k8s-deploy   (inferred, confidence=heuristic)
    isolated                                (no edges at all)
    """
    components = [
        Component(
            id="backend",
            name="backend",
            type="service",
            technology="docker-compose",
            metadata={"source_file": "docker-compose.yml", "image": "myapp/backend:1.0"},
        ),
        Component(
            id="db",
            name="db",
            type="database",
            technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="backend2",
            name="backend2",
            type="service",
            technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="k8s-deploy",
            name="backend-deployment",
            type="kubernetes_resource",
            technology="kubernetes",
            metadata={"kind": "Deployment", "pod_labels": {"app": "backend"}, "images": ["myapp/backend:1.0"]},
        ),
        Component(
            id="k8s-svc",
            name="backend-service",
            type="kubernetes_resource",
            technology="kubernetes",
            metadata={"kind": "Service", "selector": {"app": "backend"}},
        ),
        Component(id="isolated", name="isolated", type="service", technology="docker-compose", metadata={}),
    ]
    relationships = [
        Relationship(source="backend", target="db", relationship_type="depends_on"),
        Relationship(source="backend2", target="backend", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _cyclic_engine() -> GraphEngine:
    """c1 depends_on c2, c2 depends_on c1 — a genuine dependency cycle."""
    components = [
        Component(id="c1", name="c1", type="service", technology="docker-compose", metadata={}),
        Component(id="c2", name="c2", type="service", technology="docker-compose", metadata={}),
    ]
    relationships = [
        Relationship(source="c1", target="c2", relationship_type="depends_on"),
        Relationship(source="c2", target="c1", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _empty_engine() -> GraphEngine:
    return GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)


# --- 1. Node/component information -------------------------------------------


def test_gather_node_info() -> None:
    observation = gather_node_info(_main_engine(), "backend")
    assert observation.kind == ObservationKind.NODE_INFO
    assert observation.subject_id == "backend"
    assert observation.detail["name"] == "backend"
    assert observation.detail["technology"] == "docker-compose"


# --- 2. Dependencies -----------------------------------------------------------


def test_dependency_evidence() -> None:
    observations = gather_dependency_evidence(_main_engine(), "backend")
    assert {o.related_id for o in observations} == {"db"}
    assert all(o.kind == ObservationKind.DEPENDENCY for o in observations)


def test_dependency_evidence_is_transitive() -> None:
    observations = gather_dependency_evidence(_main_engine(), "backend2")
    assert {o.related_id for o in observations} == {"backend", "db"}


# --- 3. Dependents ---------------------------------------------------------------


def test_dependent_evidence_is_transitive() -> None:
    observations = gather_dependent_evidence(_main_engine(), "db")
    assert {o.related_id for o in observations} == {"backend", "backend2"}
    assert all(o.kind == ObservationKind.DEPENDENT for o in observations)


# --- 4. Impact analysis: direct + transitive --------------------------------


def test_impact_evidence_direct_and_transitive() -> None:
    observations = gather_impact_evidence(_main_engine(), "db")

    direct = {o.related_id for o in observations if o.kind == ObservationKind.IMPACT_DIRECT}
    transitive = {o.related_id for o in observations if o.kind == ObservationKind.IMPACT_TRANSITIVE}
    summary = [o for o in observations if o.kind == ObservationKind.IMPACT_SUMMARY]

    assert direct == {"backend"}
    assert transitive == {"backend2"}
    assert len(summary) == 1
    assert summary[0].detail["total_impact_count"] == 2


# --- 5/6. Relationship evidence: direct, provenance, indirect, none --------


def test_direct_relationship_parsed_origin_preserved() -> None:
    observations = gather_relationship_evidence(_main_engine(), "backend", "db")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind == ObservationKind.DIRECT_RELATIONSHIP
    assert observation.origin == "parsed"
    assert observation.confidence is None
    assert observation.basis is None


def test_direct_relationship_high_confidence_inferred_provenance_preserved() -> None:
    observations = gather_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind == ObservationKind.DIRECT_RELATIONSHIP
    assert observation.origin == "inferred"
    assert observation.confidence == "high"
    assert observation.basis == "label selector match"


def test_direct_relationship_heuristic_inferred_provenance_preserved() -> None:
    observations = gather_relationship_evidence(_main_engine(), "backend", "k8s-deploy")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind == ObservationKind.DIRECT_RELATIONSHIP
    assert observation.origin == "inferred"
    assert observation.confidence == "heuristic"
    assert observation.basis == "image reference match (myapp/backend)"


def test_inferred_edge_weight_is_lower_than_parsed_edge_weight() -> None:
    parsed = gather_relationship_evidence(_main_engine(), "backend", "db")[0]
    inferred_high = gather_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy")[0]
    inferred_heuristic = gather_relationship_evidence(_main_engine(), "backend", "k8s-deploy")[0]

    assert parsed.weight > inferred_high.weight > inferred_heuristic.weight


def test_indirect_path_evidence() -> None:
    observations = gather_relationship_evidence(_main_engine(), "backend2", "db")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind == ObservationKind.INDIRECT_PATH
    assert observation.detail["path"] == ["backend2", "backend", "db"]
    assert observation.detail["hop_count"] == 2


def test_no_relationship_evidence() -> None:
    observations = gather_relationship_evidence(_main_engine(), "backend", "isolated")
    assert len(observations) == 1
    assert observations[0].kind == ObservationKind.NO_RELATIONSHIP


# --- 7. Cycle evidence ---------------------------------------------------------


def test_cycle_evidence_detects_cycle() -> None:
    observations = gather_cycle_evidence(_cyclic_engine())
    assert len(observations) == 1
    assert observations[0].kind == ObservationKind.CYCLE
    assert set(observations[0].detail["node_ids"]) == {"c1", "c2"}


def test_cycle_evidence_empty_for_acyclic_graph() -> None:
    assert gather_cycle_evidence(_main_engine()) == []


# --- 8. Connections / related nodes ------------------------------------------


def test_connection_evidence_covers_both_directions() -> None:
    observations = gather_connection_evidence(_main_engine(), "k8s-deploy")
    related_ids = {o.related_id for o in observations}
    assert related_ids == {"k8s-svc", "backend"}
    assert all(o.kind == ObservationKind.CONNECTION for o in observations)


def test_connection_evidence_empty_for_isolated_node() -> None:
    assert gather_connection_evidence(_main_engine(), "isolated") == []


# --- 9. Whole-graph observations ---------------------------------------------


def test_graph_observations_summary() -> None:
    observations = gather_graph_observations(_main_engine())
    summaries = [o for o in observations if o.kind == ObservationKind.GRAPH_SUMMARY]
    assert len(summaries) == 3

    size_summary = summaries[0]
    assert size_summary.detail["node_count"] == 6
    assert size_summary.detail["has_cycles"] is False

    isolation_summary = summaries[1]
    assert isolation_summary.detail["isolated_node_ids"] == ["isolated"]
    assert isolation_summary.detail["connected_component_count"] == 2

    breakdown_summary = summaries[2]
    assert breakdown_summary.detail["technology_counts"] == {"docker-compose": 4, "kubernetes": 2}


def test_graph_observations_include_cycles() -> None:
    observations = gather_graph_observations(_cyclic_engine())
    cycle_observations = [o for o in observations if o.kind == ObservationKind.CYCLE]
    assert len(cycle_observations) == 1


# --- EvidencePackage / ranking -------------------------------------------------


def test_build_node_evidence_package_subject_ids() -> None:
    package = build_node_evidence(_main_engine(), "backend")
    assert package.subject_ids == ["backend"]
    assert len(package.observations) > 0


def test_build_relationship_evidence_package_subject_ids() -> None:
    package = build_relationship_evidence(_main_engine(), "backend", "db")
    assert package.subject_ids == ["backend", "db"]


def test_build_graph_evidence_package_has_no_subject_ids() -> None:
    package = build_graph_evidence(_main_engine())
    assert package.subject_ids == []


def test_evidence_package_ranked_is_non_increasing() -> None:
    package = build_node_evidence(_main_engine(), "backend")
    ranked = package.ranked()
    weights = [observation.weight for observation in ranked]
    assert weights == sorted(weights, reverse=True)


# --- 10. Empty graph / empty model handling ----------------------------------


def test_empty_graph_evidence_has_zero_counts() -> None:
    package = build_graph_evidence(_empty_engine())
    size_summary = package.observations[0]
    assert size_summary.detail["node_count"] == 0
    assert size_summary.detail["edge_count"] == 0
    assert size_summary.detail["has_cycles"] is False

    isolation_summary = package.observations[1]
    assert isolation_summary.detail["isolated_node_ids"] == []
    assert isolation_summary.detail["connected_component_count"] == 0


def test_empty_graph_has_no_cycle_observations() -> None:
    assert gather_cycle_evidence(_empty_engine()) == []


# --- 11. Unknown node handling ------------------------------------------------


def test_gather_node_info_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_node_info(_main_engine(), "does-not-exist")


def test_dependency_evidence_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_dependency_evidence(_main_engine(), "does-not-exist")


def test_dependent_evidence_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_dependent_evidence(_main_engine(), "does-not-exist")


def test_impact_evidence_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_impact_evidence(_main_engine(), "does-not-exist")


def test_relationship_evidence_raises_for_unknown_source() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_relationship_evidence(_main_engine(), "does-not-exist", "db")


def test_relationship_evidence_raises_for_unknown_target() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_relationship_evidence(_main_engine(), "backend", "does-not-exist")


def test_connection_evidence_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        gather_connection_evidence(_main_engine(), "does-not-exist")


def test_build_node_evidence_raises_for_unknown_node() -> None:
    with pytest.raises(NodeNotFoundError):
        build_node_evidence(_main_engine(), "does-not-exist")


def test_build_node_evidence_raises_on_empty_graph() -> None:
    with pytest.raises(NodeNotFoundError):
        build_node_evidence(_empty_engine(), "anything")
