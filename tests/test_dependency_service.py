"""Phase 7C: tests for app/services/dependency_service.py.

Same fixture graph as tests/test_impact_service.py (dependency
direction: source depends_on target -> target's dependents include
source):

    backend -> db        (backend depends on db)
    worker  -> db         (worker depends on db)
    frontend -> backend  (frontend depends on backend)
    cache                  (isolated: no dependencies, no dependents)

So:
- get_dependencies("db")       == []          get_dependents("db")       == [backend, worker, frontend]
- get_dependencies("backend")  == [db]         get_dependents("backend")  == [frontend]
- get_dependencies("worker")   == [db]         get_dependents("worker")   == []
- get_dependencies("frontend") == [backend, db]  get_dependents("frontend") == []
- get_dependencies("cache")    == []           get_dependents("cache")    == []
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, dependency_service
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


def test_single_node_matches_graph_engine_exactly(patched_engine: GraphEngine) -> None:
    result = dependency_service.get_single_node_dependencies("backend", repo_url="https://github.com/example/repo")
    assert result.dependencies == patched_engine.get_dependencies("backend")
    assert result.dependents == patched_engine.get_dependents("backend")


def test_single_node_leaf_dependency(patched_engine: GraphEngine) -> None:
    result = dependency_service.get_single_node_dependencies("backend", repo_url="https://github.com/example/repo")
    assert {n.id for n in result.dependencies} == {"db"}
    assert {n.id for n in result.dependents} == {"frontend"}
    assert result.node_id == "backend"


def test_single_node_transitive_dependencies(patched_engine: GraphEngine) -> None:
    """frontend depends on backend, which depends on db -> frontend's
    dependencies must include db transitively, not just backend."""
    result = dependency_service.get_single_node_dependencies("frontend", repo_url="https://github.com/example/repo")
    assert {n.id for n in result.dependencies} == {"backend", "db"}
    assert result.dependents == []


def test_single_node_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_fixture_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    dependency_service.get_single_node_dependencies("backend", analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


# --- single-node: no-dependency / no-dependent (leaf, root, isolated) ---------


def test_single_node_with_no_dependencies_and_no_dependents(patched_engine: GraphEngine) -> None:
    result = dependency_service.get_single_node_dependencies("cache", repo_url="https://github.com/example/repo")
    assert result.dependencies == []
    assert result.dependents == []


def test_single_node_with_no_dependencies_root_of_dependency_chain(patched_engine: GraphEngine) -> None:
    """db has no dependencies of its own (it's the root everything else
    depends on), but has plenty of dependents."""
    result = dependency_service.get_single_node_dependencies("db", repo_url="https://github.com/example/repo")
    assert result.dependencies == []
    assert {n.id for n in result.dependents} == {"backend", "worker", "frontend"}


def test_single_node_with_no_dependents_leaf_of_dependency_chain(patched_engine: GraphEngine) -> None:
    """worker has a dependency (db) but nothing depends on worker."""
    result = dependency_service.get_single_node_dependencies("worker", repo_url="https://github.com/example/repo")
    assert {n.id for n in result.dependencies} == {"db"}
    assert result.dependents == []


# --- single-node: unknown id / unknown analysis_id -----------------------------


def test_single_node_unknown_id_raises_node_not_found(patched_engine: GraphEngine) -> None:
    from app.graph.exceptions import NodeNotFoundError

    with pytest.raises(NodeNotFoundError):
        dependency_service.get_single_node_dependencies("does-not-exist", repo_url="https://github.com/example/repo")


def test_single_node_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        dependency_service.get_single_node_dependencies("backend", analysis_id="ghost-id")


# --- multi-node: independent per-node breakdown, NO combining -----------------


def test_multi_node_returns_independent_breakdown_per_node(patched_engine: GraphEngine) -> None:
    results = dependency_service.get_multi_node_dependencies(
        ["backend", "worker"], repo_url="https://github.com/example/repo"
    )
    assert set(results.keys()) == {"backend", "worker"}
    assert {n.id for n in results["backend"].dependencies} == {"db"}
    assert {n.id for n in results["backend"].dependents} == {"frontend"}
    assert {n.id for n in results["worker"].dependencies} == {"db"}
    assert results["worker"].dependents == []


def test_multi_node_does_not_union_or_deduplicate_across_nodes(patched_engine: GraphEngine) -> None:
    """Both backend and worker depend on db -- "db" must appear in BOTH
    per-node results independently, not deduplicated away or unioned
    into a single combined structure (unlike Phase 7B's /impact)."""
    results = dependency_service.get_multi_node_dependencies(
        ["backend", "worker"], repo_url="https://github.com/example/repo"
    )
    assert "db" in {n.id for n in results["backend"].dependencies}
    assert "db" in {n.id for n in results["worker"].dependencies}


def test_multi_node_each_result_matches_single_node_call(patched_engine: GraphEngine) -> None:
    results = dependency_service.get_multi_node_dependencies(
        ["backend", "frontend"], repo_url="https://github.com/example/repo"
    )
    single_backend = dependency_service.get_single_node_dependencies(
        "backend", repo_url="https://github.com/example/repo"
    )
    single_frontend = dependency_service.get_single_node_dependencies(
        "frontend", repo_url="https://github.com/example/repo"
    )
    assert {n.id for n in results["backend"].dependencies} == {n.id for n in single_backend.dependencies}
    assert {n.id for n in results["frontend"].dependencies} == {n.id for n in single_frontend.dependencies}


def test_multi_node_duplicate_ids_are_deduplicated_in_the_request(patched_engine: GraphEngine) -> None:
    results = dependency_service.get_multi_node_dependencies(
        ["backend", "backend", "backend"], repo_url="https://github.com/example/repo"
    )
    assert list(results.keys()) == ["backend"]


def test_multi_node_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_fixture_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    dependency_service.get_multi_node_dependencies(["backend", "worker"], analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_multi_node_unknown_id_raises_node_not_found(patched_engine: GraphEngine) -> None:
    from app.graph.exceptions import NodeNotFoundError

    with pytest.raises(NodeNotFoundError):
        dependency_service.get_multi_node_dependencies(
            ["backend", "does-not-exist"], repo_url="https://github.com/example/repo"
        )


def test_multi_node_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        dependency_service.get_multi_node_dependencies(["backend", "worker"], analysis_id="ghost-id")
