"""Stage 5C: tests for app/explanation/fallback.py.

Reuses the same hand-crafted, multi-technology graph shape as
tests/test_evidence.py (defined locally here, deliberately not imported,
so this file stays a self-contained unit test of the fallback layer
against known EvidencePackage inputs) so wording can be asserted against
exactly-known parsed/inferred/indirect/absent relationships.
"""

from app.explanation.evidence import (
    build_graph_evidence,
    build_node_evidence,
    build_relationship_evidence,
)
from app.explanation.fallback import (
    explain_graph,
    explain_node,
    explain_relationship,
    generate_fallback_explanation,
)
from app.graph.engine import GraphEngine
from app.models.explanation import Confidence
from app.models.ikm import Component, InfrastructureModel, Relationship


# --- fixtures (mirrors tests/test_evidence.py) -------------------------------


def _main_engine() -> GraphEngine:
    """
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


# --- determinism ---------------------------------------------------------------


def test_node_explanation_is_deterministic() -> None:
    engine = _main_engine()
    package = build_node_evidence(engine, "backend")
    first = explain_node(package)
    second = explain_node(package)
    assert first.explanation == second.explanation
    assert first.confidence == second.confidence


def test_relationship_explanation_is_deterministic() -> None:
    engine = _main_engine()
    package = build_relationship_evidence(engine, "backend", "db")
    first = explain_relationship(package)
    second = explain_relationship(package)
    assert first == second


def test_graph_explanation_is_deterministic() -> None:
    engine = _main_engine()
    package = build_graph_evidence(engine)
    first = explain_graph(package)
    second = explain_graph(package)
    assert first == second


def test_deterministic_across_separately_built_but_identical_graphs() -> None:
    """Same underlying model, independently built twice -> same output."""
    first = explain_node(build_node_evidence(_main_engine(), "backend"))
    second = explain_node(build_node_evidence(_main_engine(), "backend"))
    assert first.explanation == second.explanation
    assert first.confidence == second.confidence


# --- generation_method / provider_name contract ------------------------------


def test_fallback_results_use_template_generation_method() -> None:
    engine = _main_engine()
    result = generate_fallback_explanation(build_node_evidence(engine, "backend"))
    assert result.generation_method == "template"
    assert result.provider_name is None


# --- 1. component/node explanation -------------------------------------------


def test_node_explanation_states_node_info() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "backend"))
    assert "backend is a service component (docker-compose), defined in docker-compose.yml." in result.explanation


def test_node_explanation_omits_citation_when_source_file_absent() -> None:
    """Phase 6A.5: the citation clause only appears when source_file is
    actually present in metadata — no fabricated "defined in ..." text
    for a component that doesn't carry that information."""
    result = explain_node(build_node_evidence(_cyclic_engine(), "c1"))
    assert "c1 is a service component (docker-compose)." in result.explanation
    assert "defined in" not in result.explanation


def test_dispatcher_routes_single_subject_id_to_node_explanation() -> None:
    engine = _main_engine()
    dispatched = generate_fallback_explanation(build_node_evidence(engine, "backend"))
    direct = explain_node(build_node_evidence(engine, "backend"))
    assert dispatched == direct


# --- 2. dependencies -----------------------------------------------------------


def test_dependency_explanation_lists_dependency() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "backend"))
    assert "backend depends on: db." in result.explanation


def test_dependency_explanation_when_no_dependencies() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "db"))
    assert "db has no known dependencies." in result.explanation


# --- 3. dependents ---------------------------------------------------------------


def test_dependent_explanation_lists_dependents() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "db"))
    assert "backend" in result.explanation and "backend2" in result.explanation
    assert "depend on db" in result.explanation


def test_dependent_explanation_when_no_dependents() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "backend2"))
    assert (
        "Nothing currently depends on backend2, so changing it would have no impact on other components."
        in result.explanation
    )


# --- 4. impact (nonzero + zero) -----------------------------------------------
# Phase 6A.5: dependents and impact are consolidated into one sentence (see
# fallback.explain_node's comment) rather than two separate, redundant ones —
# these tests now check the consolidated wording, not a standalone "impact"
# sentence, which no longer exists.


def test_impact_explanation_nonzero() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "db"))
    assert (
        "would be impacted if it changes: backend (directly); backend2 (transitively)." in result.explanation
    )


def test_impact_explanation_zero_impact() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "isolated"))
    assert "would have no impact on other components." in result.explanation


# --- 5. relationship: parsed / inferred / indirect / none -------------------


def test_parsed_relationship_stated_as_fact() -> None:
    result = explain_relationship(build_relationship_evidence(_main_engine(), "backend", "db"))
    assert result.explanation == "backend depends on db."
    assert result.confidence == Confidence.HIGH


def test_inferred_high_confidence_relationship_is_hedged() -> None:
    result = explain_relationship(build_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy"))
    assert "infers" in result.explanation
    assert "may connect to" in result.explanation
    assert "label selector match" in result.explanation
    assert "high confidence" in result.explanation
    assert result.confidence == Confidence.MIXED


def test_inferred_heuristic_relationship_is_hedged() -> None:
    result = explain_relationship(build_relationship_evidence(_main_engine(), "backend", "k8s-deploy"))
    assert "infers" in result.explanation
    assert "image reference match (myapp/backend)" in result.explanation
    assert "heuristic confidence" in result.explanation
    assert result.confidence == Confidence.LOW


def test_inferred_relationships_are_never_phrased_as_unqualified_facts() -> None:
    """The critical provenance rule: no inferred relationship may read as
    a bare 'X depends_on Y' style statement."""
    high = explain_relationship(build_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy"))
    heuristic = explain_relationship(build_relationship_evidence(_main_engine(), "backend", "k8s-deploy"))

    for result in (high, heuristic):
        assert "InfraLens infers" in result.explanation
        assert "not a directly declared relationship" in result.explanation
        # Must not read like the unqualified parsed-fact sentence shape "X connects to Y."
        assert "k8s-svc connects to k8s-deploy." not in result.explanation
        assert "backend connects to k8s-deploy." not in result.explanation


def test_indirect_path_explanation() -> None:
    result = explain_relationship(build_relationship_evidence(_main_engine(), "backend2", "db"))
    assert "no direct relationship" in result.explanation
    assert "indirect path" in result.explanation
    assert "backend2 -> backend -> db" in result.explanation
    assert result.confidence == Confidence.MIXED


def test_no_relationship_explanation() -> None:
    result = explain_relationship(build_relationship_evidence(_main_engine(), "backend", "isolated"))
    assert result.explanation == "No relationship — direct or indirect — was found between backend and isolated."
    assert result.confidence == Confidence.HIGH


def test_dispatcher_routes_two_subject_ids_to_relationship_explanation() -> None:
    engine = _main_engine()
    dispatched = generate_fallback_explanation(build_relationship_evidence(engine, "backend", "db"))
    direct = explain_relationship(build_relationship_evidence(engine, "backend", "db"))
    assert dispatched == direct


# --- 6. architecture/connections ---------------------------------------------


def test_node_explanation_includes_lateral_connection_wording() -> None:
    result = explain_node(build_node_evidence(_main_engine(), "k8s-deploy"))
    # k8s-deploy has two lateral (connects_to) connections: from k8s-svc (high)
    # and from backend (heuristic) — both must appear, both hedged.
    assert result.explanation.count("InfraLens infers") == 2
    assert result.confidence == Confidence.LOW  # heuristic connection present -> lowest band


def test_node_explanation_excludes_dependency_edges_from_connections_section() -> None:
    """Dependency-type edges are already covered by the dependency/dependent
    sentences; the connections section should not restate them."""
    result = explain_node(build_node_evidence(_main_engine(), "backend"))
    assert "InfraLens infers that backend may depends on" not in result.explanation


# --- 7. whole-graph observations -----------------------------------------------


def test_graph_explanation_states_counts_and_acyclic() -> None:
    result = explain_graph(build_graph_evidence(_main_engine()))
    assert "6 component(s)" in result.explanation
    assert "It has no dependency cycles." in result.explanation
    assert result.confidence == Confidence.HIGH


def test_graph_explanation_states_isolated_component() -> None:
    result = explain_graph(build_graph_evidence(_main_engine()))
    assert "isolated from the rest of the graph: isolated" in result.explanation


def test_graph_explanation_states_technology_breakdown() -> None:
    result = explain_graph(build_graph_evidence(_main_engine()))
    assert "docker-compose" in result.explanation
    assert "kubernetes" in result.explanation


def test_dispatcher_routes_zero_subject_ids_to_graph_explanation() -> None:
    engine = _main_engine()
    dispatched = generate_fallback_explanation(build_graph_evidence(engine))
    direct = explain_graph(build_graph_evidence(engine))
    assert dispatched == direct


# --- 8. cycles -------------------------------------------------------------------


def test_graph_explanation_states_cycle() -> None:
    result = explain_graph(build_graph_evidence(_cyclic_engine()))
    assert "It contains at least one dependency cycle." in result.explanation
    assert "A dependency cycle was detected involving:" in result.explanation
    assert "c1" in result.explanation and "c2" in result.explanation


# --- empty graph behavior ------------------------------------------------------


def test_empty_graph_explanation() -> None:
    result = explain_graph(build_graph_evidence(_empty_engine()))
    assert result.explanation == "No infrastructure components were found to explain."
    assert result.confidence == Confidence.LOW


def test_empty_graph_explanation_via_dispatcher() -> None:
    result = generate_fallback_explanation(build_graph_evidence(_empty_engine()))
    assert result.confidence == Confidence.LOW
