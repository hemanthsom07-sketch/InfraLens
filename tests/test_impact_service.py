"""Phase 7B: tests for app/services/impact_service.py.

Fixture graph (dependency direction: source depends_on target ->
target's dependents include source):

    backend -> db        (backend depends on db)
    worker  -> db         (worker depends on db)
    frontend -> backend  (frontend depends on backend)
    cache                (isolated: no dependencies, no dependents)

So:
- impact_analysis("db"):      direct=[backend, worker], transitive=[frontend]
- impact_analysis("backend"): direct=[frontend],         transitive=[]
- impact_analysis("worker"):  direct=[],                 transitive=[]
- impact_analysis("cache"):   direct=[],                 transitive=[]  (no-impact node)

This one fixture exercises: single-node faithfulness, an empty/no-impact
node, and — for node_ids=["db", "backend"] — a genuine overlap where
"frontend" is transitive for db's own report but direct for backend's,
which must be reclassified as direct in the combined result, and
"backend" (itself one of the requested targets) legitimately appears as
a direct dependent of "db" (see get_multi_node_impact()'s docstring for
why targets are deliberately not filtered out).
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, impact_service
from tests.conftest import make_analysis_result


def _fixture_engine() -> GraphEngine:
    components = [
        Component(id="db", name="db", type="database", technology="test", metadata={}),
        Component(id="backend", name="backend", type="service", technology="test", metadata={}),
        Component(id="worker", name="worker", type="worker", technology="test", metadata={}),
        Component(id="frontend", name="frontend", type="service", technology="test", metadata={}),
        Component(id="cache", name="cache", type="service", technology="test", metadata={}),
    ]
    relationships = [
        Relationship(source="backend", target="db", relationship_type="depends_on"),
        Relationship(source="worker", target="db", relationship_type="depends_on"),
        Relationship(source="frontend", target="backend", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> GraphEngine:
    graph = _fixture_engine()

    def fake_resolve(*, analysis_id, repo_url):
        return make_analysis_result(graph)

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    return graph


# --- single-node: faithfulness (no reimplementation, no reshaping) -------------


def test_single_node_matches_graph_engine_impact_analysis_exactly(patched_engine: GraphEngine) -> None:
    result = impact_service.get_single_node_impact("db", repo_url="https://github.com/example/repo")
    expected = patched_engine.impact_analysis("db")
    assert result == expected


def test_single_node_result_content(patched_engine: GraphEngine) -> None:
    result = impact_service.get_single_node_impact("db", repo_url="https://github.com/example/repo")
    assert {n.id for n in result.direct_dependents} == {"backend", "worker"}
    assert {n.id for n in result.transitive_dependents} == {"frontend"}
    assert result.total_impact_count == 3
    assert result.target.id == "db"


def test_single_node_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_fixture_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    impact_service.get_single_node_impact("db", analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


# --- single-node: empty/no-impact node -----------------------------------------


def test_single_node_with_no_dependents(patched_engine: GraphEngine) -> None:
    result = impact_service.get_single_node_impact("cache", repo_url="https://github.com/example/repo")
    assert result.direct_dependents == []
    assert result.transitive_dependents == []
    assert result.total_impact_count == 0
    assert result.impact_by_type == {}


# --- single-node: unknown id / unknown analysis_id -----------------------------


def test_single_node_unknown_id_raises_node_not_found(patched_engine: GraphEngine) -> None:
    from app.graph.exceptions import NodeNotFoundError

    with pytest.raises(NodeNotFoundError):
        impact_service.get_single_node_impact("does-not-exist", repo_url="https://github.com/example/repo")


def test_single_node_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        impact_service.get_single_node_impact("db", analysis_id="ghost-id")


# --- multi-node: union, overlap, dedup, direct-priority classification --------


def test_multi_node_union_of_disjoint_targets(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(
        ["backend", "worker"], repo_url="https://github.com/example/repo"
    )
    # backend's impact: direct=[frontend]. worker's impact: direct=[], transitive=[].
    assert {n.id for n in result.direct_dependents} == {"frontend"}
    assert result.transitive_dependents == []
    assert result.total_impact_count == 1


def test_multi_node_overlapping_dependents_are_deduplicated_and_reclassified(
    patched_engine: GraphEngine,
) -> None:
    """"frontend" is transitive for db's own report, but direct for
    backend's — the combined result must classify it as direct (not
    list it in both, and not leave it as transitive)."""
    result = impact_service.get_multi_node_impact(["db", "backend"], repo_url="https://github.com/example/repo")

    direct_ids = {n.id for n in result.direct_dependents}
    transitive_ids = {n.id for n in result.transitive_dependents}

    assert direct_ids == {"backend", "worker", "frontend"}
    assert transitive_ids == set()
    assert direct_ids.isdisjoint(transitive_ids)
    assert result.total_impact_count == 3


def test_multi_node_targets_are_not_filtered_from_dependents(patched_engine: GraphEngine) -> None:
    """Deliberate design choice (see get_multi_node_impact()'s
    docstring): "backend" is itself one of the requested targets, and it
    legitimately also appears as a direct dependent of "db" (the other
    target) — it is NOT filtered out just because it was also requested."""
    result = impact_service.get_multi_node_impact(["db", "backend"], repo_url="https://github.com/example/repo")
    assert "backend" in {n.id for n in result.direct_dependents}


def test_multi_node_impact_by_type_is_recomputed_not_summed(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(["db", "backend"], repo_url="https://github.com/example/repo")
    # final deduplicated set: backend(service), worker(worker), frontend(service)
    assert result.impact_by_type == {"service": 2, "worker": 1}


def test_multi_node_per_node_holds_each_original_unmodified_report(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(["db", "backend"], repo_url="https://github.com/example/repo")
    assert set(result.per_node.keys()) == {"db", "backend"}
    assert result.per_node["db"] == patched_engine.impact_analysis("db")
    assert result.per_node["backend"] == patched_engine.impact_analysis("backend")


def test_multi_node_targets_are_in_requested_order(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(["backend", "db"], repo_url="https://github.com/example/repo")
    assert [t.id for t in result.targets] == ["backend", "db"]


def test_multi_node_duplicate_ids_are_deduplicated(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(
        ["db", "db", "db"], repo_url="https://github.com/example/repo"
    )
    assert list(result.per_node.keys()) == ["db"]
    assert [t.id for t in result.targets] == ["db"]


def test_multi_node_all_no_impact_nodes_returns_empty_combined_result(patched_engine: GraphEngine) -> None:
    result = impact_service.get_multi_node_impact(
        ["cache", "worker"], repo_url="https://github.com/example/repo"
    )
    assert result.direct_dependents == []
    assert result.transitive_dependents == []
    assert result.total_impact_count == 0
    assert result.impact_by_type == {}


def test_multi_node_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_fixture_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    impact_service.get_multi_node_impact(["db", "backend"], analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_multi_node_unknown_id_raises_node_not_found(patched_engine: GraphEngine) -> None:
    from app.graph.exceptions import NodeNotFoundError

    with pytest.raises(NodeNotFoundError):
        impact_service.get_multi_node_impact(
            ["db", "does-not-exist"], repo_url="https://github.com/example/repo"
        )


def test_multi_node_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        impact_service.get_multi_node_impact(["db", "backend"], analysis_id="ghost-id")
