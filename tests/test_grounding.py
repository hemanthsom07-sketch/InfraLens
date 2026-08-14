"""Stage 5F: tests for app/explanation/grounding.py.

Reuses the same hand-crafted graph shape as tests/test_evidence.py and
tests/test_explanation_fallback.py (defined locally, not imported).
"""

from app.explanation.evidence import build_graph_evidence, build_node_evidence, build_relationship_evidence
from app.explanation.grounding import build_grounding_context
from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship


def _main_engine() -> GraphEngine:
    """
    backend2 --depends_on--> backend --depends_on--> db
    k8s-svc  --connects_to--> k8s-deploy   (inferred, confidence=high)
    backend  --connects_to--> k8s-deploy   (inferred, confidence=heuristic)
    isolated                                (no edges at all)
    """
    components = [
        Component(
            id="backend", name="backend", type="service", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml", "image": "myapp/backend:1.0"},
        ),
        Component(
            id="db", name="db", type="database", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="backend2", name="backend2", type="service", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="k8s-deploy", name="backend-deployment", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Deployment", "pod_labels": {"app": "backend"}, "images": ["myapp/backend:1.0"]},
        ),
        Component(
            id="k8s-svc", name="backend-service", type="kubernetes_resource", technology="kubernetes",
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


# --- bucketing correctness ---------------------------------------------------


def test_parsed_relationship_becomes_a_fact() -> None:
    package = build_relationship_evidence(_main_engine(), "backend", "db")
    context = build_grounding_context(package)
    assert context.facts == ["backend depends_on db"]
    assert context.high_confidence_inferences == []
    assert context.heuristic_inferences == []


def test_high_confidence_inferred_relationship_is_bucketed_correctly() -> None:
    package = build_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy")
    context = build_grounding_context(package)
    assert context.facts == []
    assert len(context.high_confidence_inferences) == 1
    assert "k8s-svc connects_to k8s-deploy" in context.high_confidence_inferences[0]
    assert "basis: label selector match" in context.high_confidence_inferences[0]
    assert context.heuristic_inferences == []


def test_heuristic_inferred_relationship_is_bucketed_correctly() -> None:
    package = build_relationship_evidence(_main_engine(), "backend", "k8s-deploy")
    context = build_grounding_context(package)
    assert context.facts == []
    assert context.high_confidence_inferences == []
    assert len(context.heuristic_inferences) == 1
    assert "backend connects_to k8s-deploy" in context.heuristic_inferences[0]
    assert "basis: image reference match (myapp/backend)" in context.heuristic_inferences[0]


def test_node_connections_are_bucketed_by_confidence() -> None:
    """k8s-deploy has two lateral connections: one high, one heuristic —
    both must land in the correct, separate buckets."""
    package = build_node_evidence(_main_engine(), "k8s-deploy")
    context = build_grounding_context(package)
    assert len(context.high_confidence_inferences) == 1
    assert len(context.heuristic_inferences) == 1
    assert context.facts == []


def test_dependency_and_impact_observations_land_in_other_observations() -> None:
    package = build_node_evidence(_main_engine(), "backend")
    context = build_grounding_context(package)
    assert any("dependency" in line for line in context.other_observations)
    assert any("impact_summary" in line for line in context.other_observations)
    assert any("node_info" in line for line in context.other_observations)


def test_no_relationship_observation_lands_in_other_observations() -> None:
    package = build_relationship_evidence(_main_engine(), "backend", "isolated")
    context = build_grounding_context(package)
    assert context.facts == []
    assert context.high_confidence_inferences == []
    assert context.heuristic_inferences == []
    assert any("no_relationship" in line for line in context.other_observations)


def test_indirect_path_observation_lands_in_other_observations() -> None:
    package = build_relationship_evidence(_main_engine(), "backend2", "db")
    context = build_grounding_context(package)
    assert any("indirect_path" in line for line in context.other_observations)


def test_cycle_observations_land_in_other_observations() -> None:
    package = build_graph_evidence(_cyclic_engine())
    context = build_grounding_context(package)
    assert any("cycle" in line for line in context.other_observations)


# --- nothing invented: every line traces back to a real observation --------


def test_grounding_context_line_count_matches_observation_count() -> None:
    package = build_node_evidence(_main_engine(), "k8s-deploy")
    context = build_grounding_context(package)
    total_lines = (
        len(context.facts)
        + len(context.high_confidence_inferences)
        + len(context.heuristic_inferences)
        + len(context.other_observations)
    )
    assert total_lines == len(package.observations)


def test_subject_ids_are_preserved_verbatim() -> None:
    package = build_relationship_evidence(_main_engine(), "backend", "db")
    context = build_grounding_context(package)
    assert context.subject_ids == ["backend", "db"]


def test_graph_level_package_has_empty_subject_ids() -> None:
    package = build_graph_evidence(_main_engine())
    context = build_grounding_context(package)
    assert context.subject_ids == []


# --- determinism ---------------------------------------------------------------


def test_grounding_context_is_deterministic() -> None:
    engine = _main_engine()
    package = build_node_evidence(engine, "backend")
    first = build_grounding_context(package)
    second = build_grounding_context(package)
    assert first == second


def test_empty_graph_grounding_context_has_no_facts_or_inferences() -> None:
    empty_engine = GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)
    package = build_graph_evidence(empty_engine)
    context = build_grounding_context(package)
    assert context.facts == []
    assert context.high_confidence_inferences == []
    assert context.heuristic_inferences == []
