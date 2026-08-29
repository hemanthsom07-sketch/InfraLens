"""Phase 7D: tests for app/services/graph_diagnostics_service.py.

Two real GraphEngine fixtures (dependency edge types are depends_on /
uses / contains / mounts — see app/graph/algorithms/traversal.py):

_acyclic_engine():
    a --depends_on--> b --depends_on--> c --connects_to--> d       iso (isolated)

    - dependency subgraph (cycles/topological_order): only a->b->c is a
      dependency chain; the c->d edge is connects_to, NOT a dependency
      edge, so it's excluded there.
    - full graph (connected_components/shortest_path): a-b-c-d are all
      weakly connected (connects_to still counts as an edge for this),
      "iso" is its own isolated singleton group.
    This fixture exists specifically to exercise the scoping difference
    documented in graph_diagnostics_service.py: shortest_path can hop
    across the connects_to edge (c -> d) that dependency-only traversal
    would never use.

_cyclic_engine():
    a --depends_on--> b --depends_on--> c --depends_on--> a   (a 3-cycle)
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, graph_diagnostics_service
from tests.conftest import make_analysis_result


def _acyclic_engine() -> GraphEngine:
    components = [
        Component(id="a", name="a", type="service", technology="test", metadata={}),
        Component(id="b", name="b", type="service", technology="test", metadata={}),
        Component(id="c", name="c", type="service", technology="test", metadata={}),
        Component(id="d", name="d", type="service", technology="test", metadata={}),
        Component(id="iso", name="iso", type="service", technology="test", metadata={}),
    ]
    relationships = [
        Relationship(source="a", target="b", relationship_type="depends_on"),
        Relationship(source="b", target="c", relationship_type="depends_on"),
        Relationship(source="c", target="d", relationship_type="connects_to"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _cyclic_engine() -> GraphEngine:
    components = [
        Component(id="a", name="a", type="service", technology="test", metadata={}),
        Component(id="b", name="b", type="service", technology="test", metadata={}),
        Component(id="c", name="c", type="service", technology="test", metadata={}),
    ]
    relationships = [
        Relationship(source="a", target="b", relationship_type="depends_on"),
        Relationship(source="b", target="c", relationship_type="depends_on"),
        Relationship(source="c", target="a", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _patch_to(monkeypatch: pytest.MonkeyPatch, graph: GraphEngine) -> None:
    monkeypatch.setattr(
        analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: make_analysis_result(graph)
    )


# --- diagnostics: cycle detection ----------------------------------------------


def test_cycle_detection_on_acyclic_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    assert result.cycles == []


def test_cycle_detection_on_cyclic_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _cyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    assert len(result.cycles) == 1
    assert {n.id for n in result.cycles[0]} == {"a", "b", "c"}


def test_cycle_detection_matches_graph_engine_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _cyclic_engine()
    _patch_to(monkeypatch, engine)
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    assert result.cycles == engine.detect_cycles()


# --- diagnostics: topological order ---------------------------------------------


def test_topological_order_on_acyclic_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    assert result.topological_order is not None
    order_ids = [n.id for n in result.topological_order]
    # a, b, c must respect the dependency chain a -> b -> c; d and iso
    # have no ordering constraints but must still appear.
    assert order_ids.index("a") < order_ids.index("b") < order_ids.index("c")
    assert set(order_ids) == {"a", "b", "c", "d", "iso"}


def test_topological_order_is_none_for_cyclic_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _cyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    assert result.topological_order is None


# --- diagnostics: connected components -------------------------------------------


def test_connected_components_multiple_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    groups = [{n.id for n in group} for group in result.connected_components]
    assert {"a", "b", "c", "d"} in groups
    assert {"iso"} in groups
    assert len(groups) == 2


def test_connected_components_uses_full_graph_not_dependency_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """"d" is only reachable via a connects_to edge (not a dependency
    edge) -- it must still be grouped with a/b/c in connected_components,
    confirming this uses the full graph, not the dependency subgraph."""
    _patch_to(monkeypatch, _acyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    groups = [{n.id for n in group} for group in result.connected_components]
    d_group = next(g for g in groups if "d" in g)
    assert d_group == {"a", "b", "c", "d"}


def test_connected_components_isolated_node_is_its_own_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    result = graph_diagnostics_service.get_graph_diagnostics(repo_url="https://github.com/example/repo")
    groups = [{n.id for n in group} for group in result.connected_components]
    assert {"iso"} in groups


# --- diagnostics: analysis resolution --------------------------------------------


def test_diagnostics_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_acyclic_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    graph_diagnostics_service.get_graph_diagnostics(analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_diagnostics_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        graph_diagnostics_service.get_graph_diagnostics(analysis_id="ghost-id")


# --- shortest path: connected / disconnected -------------------------------------


def test_shortest_path_between_connected_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    path = graph_diagnostics_service.get_shortest_path("a", "c", repo_url="https://github.com/example/repo")
    assert [n.id for n in path] == ["a", "b", "c"]


def test_shortest_path_crosses_non_dependency_edge_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """a -> b -> c are dependency edges, c -> d is connects_to -- the
    shortest path from a to d must still traverse it (full graph, any
    edge type), even though get_dependencies("a") would never include d."""
    engine = _acyclic_engine()
    _patch_to(monkeypatch, engine)
    path = graph_diagnostics_service.get_shortest_path("a", "d", repo_url="https://github.com/example/repo")
    assert [n.id for n in path] == ["a", "b", "c", "d"]
    assert "d" not in {n.id for n in engine.get_dependencies("a")}


def test_shortest_path_between_disconnected_nodes_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_to(monkeypatch, _acyclic_engine())
    path = graph_diagnostics_service.get_shortest_path("a", "iso", repo_url="https://github.com/example/repo")
    assert path is None


def test_shortest_path_matches_graph_engine_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _acyclic_engine()
    _patch_to(monkeypatch, engine)
    result = graph_diagnostics_service.get_shortest_path("a", "c", repo_url="https://github.com/example/repo")
    assert result == engine.shortest_path("a", "c")


# --- shortest path: unknown nodes / unknown analysis_id ---------------------------


def test_shortest_path_unknown_source_raises_node_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.graph.exceptions import NodeNotFoundError

    _patch_to(monkeypatch, _acyclic_engine())
    with pytest.raises(NodeNotFoundError):
        graph_diagnostics_service.get_shortest_path(
            "does-not-exist", "c", repo_url="https://github.com/example/repo"
        )


def test_shortest_path_unknown_target_raises_node_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.graph.exceptions import NodeNotFoundError

    _patch_to(monkeypatch, _acyclic_engine())
    with pytest.raises(NodeNotFoundError):
        graph_diagnostics_service.get_shortest_path(
            "a", "does-not-exist", repo_url="https://github.com/example/repo"
        )


def test_shortest_path_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_acyclic_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    graph_diagnostics_service.get_shortest_path("a", "c", analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_shortest_path_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        graph_diagnostics_service.get_shortest_path("a", "c", analysis_id="ghost-id")
